from pathlib import Path
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import StreamingResponse

from mealpilot.agent.analyze import extract_supported_constraints
from mealpilot.agent.workflow import run_agent
from mealpilot.data.loader import load_recipe_sets
from mealpilot.domain.models import AccountProfile, AccountSummary, AdminRecipeCatalogItem, AdoptedMealPlan, AdoptPlanCommand, AgentPlanningCommand, AgentRunResult, AuthSession, ChatRequest, ChatResponse, ConversationDetail, ConversationSummary, CreateConversationCommand, DeleteAccountCommand, DeterministicPlanningCommand, DurableRunSnapshot, FeedbackCollection, HistoryCollection, LoginCommand, MealPlan, MenuDraft, MenuDraftCommand, MenuDraftFailure, NutritionTargetSuggestion, PlanFeedback, PlanningFailure, PreferenceMemory, PrivacyPolicy, ProductReadiness, ReadableRecipe, ReadableRecipeIngredient, RegisterCommand, RunAuditSummary, RunCancelCommand, RunDecisionCommand, SaveAccountProfileCommand, SavePlanFeedbackCommand, UserProfile, MealSlot
from mealpilot.nutrition.loaders import load_food_catalog
from mealpilot.nutrition.validation import CatalogCoverageReport, audit_catalog_coverage, calculate_recipe_from_catalog, materialize_catalog_recipes
from mealpilot.nutrition.targets import suggest_targets
from mealpilot.planning.engine import create_deterministic_plan
from mealpilot.planning.menu_draft import create_menu_draft
from mealpilot.runtime.service import DurableRunService, RunConflict
from mealpilot.llm.siliconflow import extract_planning_constraints, load_siliconflow_settings
from mealpilot.memory.chat import answer as answer_chat
from mealpilot.memory.store import PreferenceMemoryStore
from mealpilot.history.service import AdoptionRejected, MealPlanHistoryService
from mealpilot.history.store import HistoryConflict, MealPlanHistoryStore
from mealpilot.conversation.store import ConversationConflict, ConversationStore
from mealpilot.production.settings import load_production_settings
from mealpilot.auth.security import issue_access_token, load_auth_settings, verify_access_token
from mealpilot.auth.store import AccountConflict, AuthenticationFailed, SqliteAccountStore
from mealpilot.ingestion.admin import AdminBatchPublishCommand, AdminBatchPublishResponse, AdminBatchPublishResult, AdminPublishCommand, AdminRevokeCommand, AdminReviewCurationCommand, AdminReviewDetail, AdminReviewVersionCommand, build_trusted_curation, canonical_options
from mealpilot.ingestion.idempotency import ReviewIdempotencyStore
from mealpilot.ingestion.llm_jobs import CancelLlmReviewJobCommand, CreateLlmReviewJobCommand, LlmReviewJob, LlmReviewJobConflict, LlmReviewJobStore
from mealpilot.ingestion.review import ReviewConflict, ReviewRejected, ReviewService, ReviewStore
from mealpilot.ingestion.settings import load_recipe_data_paths
from mealpilot.ingestion.acquisition import list_source_policies, load_source_policy, policy_summary
from mealpilot.ingestion.jobs import CancelRecipeAcquisitionJobCommand, CreateRecipeAcquisitionJobCommand, JobConflict as AcquisitionJobConflict, RecipeAcquisitionEvent, RecipeAcquisitionJob, RecipeAcquisitionJobStore, RecipeAcquisitionPreview, SourcePolicySummary

app = FastAPI(title="MealPilot", version="0.1.0", description="M0 contract shell")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:5174", "http://localhost:8080"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
PROJECT_ROOT = Path(os.getenv("MEALPILOT_PROJECT_ROOT", str(Path(__file__).resolve().parents[2]))).resolve()
runtime_dir = Path(os.getenv("MEALPILOT_RUNTIME_DIR", str(PROJECT_ROOT / ".runtime"))).resolve()
production_settings = load_production_settings()
auth_settings = load_auth_settings(runtime_dir, production_settings.postgres_enabled or os.getenv("MEALPILOT_ENV", "development").casefold() == "production")
trusted_hosts = [value.strip() for value in os.getenv("MEALPILOT_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if value.strip()]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
run_queue = None
if production_settings.postgres_enabled and production_settings.database_url:
    from mealpilot.auth.postgres import PostgresAccountStore
    from mealpilot.production.documents import PostgresConversationStore, PostgresMealPlanHistoryStore
    from mealpilot.production.postgres import PostgresPreferenceMemoryStore, PostgresRunStore
    from mealpilot.production.queue import PostgresRunQueue

    durable_runs = DurableRunService(store=PostgresRunStore(production_settings.database_url))
    preference_memory = PostgresPreferenceMemoryStore(production_settings.database_url)
    meal_plan_history_store = PostgresMealPlanHistoryStore(production_settings.database_url)
    conversation_store = PostgresConversationStore(production_settings.database_url)
    run_queue = PostgresRunQueue(production_settings.database_url)
    account_store = PostgresAccountStore(production_settings.database_url)
else:
    durable_runs = DurableRunService(runtime_dir / "mealpilot.sqlite3")
    preference_memory = PreferenceMemoryStore(runtime_dir / "preference-memory.sqlite3")
    meal_plan_history_store = MealPlanHistoryStore(runtime_dir / "meal-plan-history.sqlite3")
    conversation_store = ConversationStore(runtime_dir / "conversations.sqlite3")
    account_store = SqliteAccountStore(runtime_dir / "accounts.sqlite3")

bootstrap_admin_password = os.getenv("MEALPILOT_ADMIN_PASSWORD", "").strip()
if bootstrap_admin_password:
    account_store.ensure_admin(
        os.getenv("MEALPILOT_ADMIN_USERNAME", "root"),
        bootstrap_admin_password,
        os.getenv("MEALPILOT_ADMIN_DISPLAY_NAME", "Root Administrator"),
    )

recipe_data_paths = load_recipe_data_paths(PROJECT_ROOT)
recipe_review_store = ReviewStore(recipe_data_paths.reviews)
recipe_review_idempotency = ReviewIdempotencyStore(recipe_data_paths.jobs.parent / "review-idempotency.sqlite3")
recipe_acquisition_jobs = RecipeAcquisitionJobStore(recipe_data_paths.jobs)
llm_review_jobs = LlmReviewJobStore(recipe_data_paths.llm_jobs)

if production_settings.rate_limit_per_minute:
    from mealpilot.production.observability import MetricsMiddleware, RateLimitMiddleware
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        limit_per_minute=production_settings.rate_limit_per_minute,
        redis_url=production_settings.redis_url,
        trust_proxy_headers=production_settings.trusted_proxy_headers,
    )

ACCESS_COOKIE = "mealpilot_access"
REFRESH_COOKIE = "mealpilot_refresh"


def _set_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    common = {"httponly": True, "secure": auth_settings.secure_cookie, "samesite": "strict"}
    response.set_cookie(ACCESS_COOKIE, access_token, max_age=auth_settings.access_seconds, path="/", **common)
    response.set_cookie(REFRESH_COOKIE, refresh_token, max_age=auth_settings.refresh_seconds, path="/v1/auth", **common)


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/", secure=auth_settings.secure_cookie, httponly=True, samesite="strict")
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth", secure=auth_settings.secure_cookie, httponly=True, samesite="strict")


def _session(account: AccountSummary, response: Response, refresh_token: str | None = None) -> AuthSession:
    access_token = issue_access_token(account.user_id, auth_settings)
    refresh = refresh_token or account_store.create_refresh_session(account.user_id, auth_settings.refresh_seconds)
    _set_session_cookies(response, access_token, refresh)
    return AuthSession(access_token=access_token, expires_in=auth_settings.access_seconds, account=account)


def current_account(request: Request, authorization: str | None = Header(default=None)) -> AccountSummary:
    token = request.cookies.get(ACCESS_COOKIE)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not authorization:
        raise HTTPException(status_code=401, detail="Bearer authorization is required for state-changing requests", headers={"WWW-Authenticate": "Bearer"})
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not value:
            raise HTTPException(status_code=401, detail="invalid authorization header", headers={"WWW-Authenticate": "Bearer"})
        token = value
    if not token:
        raise HTTPException(status_code=401, detail="authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return account_store.get(verify_access_token(token, auth_settings))
    except (ValueError, KeyError) as error:
        raise HTTPException(status_code=401, detail="access token is invalid or expired", headers={"WWW-Authenticate": "Bearer"}) from error


def _require_owner(user_id: str, account: AccountSummary) -> None:
    if user_id != account.user_id:
        raise HTTPException(status_code=403, detail="resource belongs to another account")


def current_admin(account: AccountSummary = Depends(current_account)) -> AccountSummary:
    if account.role != "ADMIN":
        raise HTTPException(status_code=403, detail="administrator role is required")
    return account


def _bind_command_owner(command: AgentPlanningCommand, account: AccountSummary) -> AgentPlanningCommand:
    if command.user_id is not None and command.user_id != account.user_id:
        raise HTTPException(status_code=403, detail="user_id cannot be supplied for another account")
    return command.model_copy(update={"user_id": account.user_id})


def _require_run_owner(run_id: str, account: AccountSummary) -> AgentPlanningCommand:
    try:
        record = durable_runs.store.get(run_id)
        command = AgentPlanningCommand.model_validate(record.command)
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    _require_owner(command.user_id or "", account)
    return command


def _recipes() -> list:
    paths = [_published_recipe_path()]
    if os.getenv("MEALPILOT_INCLUDE_SAMPLE_RECIPES", "false").casefold() == "true":
        paths.insert(0, PROJECT_ROOT / "data" / "recipes.sample.json")
    return load_recipe_sets(*paths)


def _published_recipe_path() -> Path:
    """Unified default with a documented compatibility override for tests/migration."""
    return Path(os.getenv("MEALPILOT_PUBLISHED_RECIPES_PATH", str(recipe_data_paths.published))).resolve()


def _readable_recipe_path() -> Path:
    return Path(os.getenv("MEALPILOT_READABLE_RECIPES_PATH", str(recipe_data_paths.readable_published))).resolve()


def _readable_recipes() -> list[ReadableRecipe]:
    path = _readable_recipe_path()
    if not path.exists():
        return []
    values = json.loads(path.read_text(encoding="utf-8"))
    return [ReadableRecipe.model_validate(value) for value in values]


def _published_readable_recipes() -> list[ReadableRecipe]:
    """Merge planning recipes and standalone readable records without duplicates."""

    values: dict[tuple[str, str], ReadableRecipe] = {}
    for recipe in _recipes():
        if not recipe.cooking_steps:
            continue
        values[(recipe.recipe_id, recipe.version)] = ReadableRecipe(
            recipe_id=recipe.recipe_id,
            version=recipe.version,
            title=recipe.title,
            supported_slots=recipe.supported_slots,
            servings=recipe.servings,
            prep_minutes=recipe.prep_minutes,
            ingredients=[ReadableRecipeIngredient(group="other", raw_name=item.canonical_name, display_quantity=item.display_quantity) for item in recipe.ingredients],
            cooking_steps=recipe.cooking_steps,
            source=recipe.source,
            numeric_policy_version=recipe.numeric_policy_version,
        )
    for recipe in _readable_recipes():
        values.setdefault((recipe.recipe_id, recipe.version), recipe)
    return sorted(values.values(), key=lambda item: (item.title, item.recipe_id, item.version))


def _nutrition_catalog_path() -> Path:
    """Resolve the formal runtime nutrition catalog; sample data is test-only."""
    return Path(os.getenv("MEALPILOT_NUTRITION_DATA_PATH", str(recipe_data_paths.nutrition))).resolve()


def _nutrition_catalog() -> list:
    path = _nutrition_catalog_path()
    return load_food_catalog(path) if path.exists() else []


def _recipe_review_service() -> ReviewService:
    return ReviewService(recipe_review_store, _nutrition_catalog())


def _admin_review_detail(review_id: str) -> AdminReviewDetail:
    try:
        item = recipe_review_store.get(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="recipe review not found") from error
    return AdminReviewDetail(
        review=item, canonical_ingredients=canonical_options(item.raw),
        can_approve=item.status == "PENDING" and item.processing_stage == "FINAL_VALIDATED" and item.quality_report.status in {"PUBLICATION_READY", "SOLVER_READY"} and not item.migration_warnings,
        can_publish_readable=item.status == "PENDING" and item.processing_stage == "FINAL_VALIDATED" and item.quality_report.readable_eligible and item.readable_publish_receipt is None and not item.migration_warnings,
        can_publish=item.status == "APPROVED" and item.staged_recipe is not None and not item.migration_warnings,
    )


def _admin_review_mutation(scope: str, idempotency_key: str, payload: object, action):
    serialized = payload.model_dump_json() if hasattr(payload, "model_dump_json") else json.dumps(payload, sort_keys=True, default=str)
    fingerprint = hashlib.sha256(f"{scope}:{serialized}".encode("utf-8")).hexdigest()
    try:
        return recipe_review_idempotency.execute(scope, idempotency_key, fingerprint, action)
    except ReviewConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ReviewRejected as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _catalog_coverage() -> CatalogCoverageReport:
    return audit_catalog_coverage(_recipes(), _nutrition_catalog())


def _planning_recipes() -> list:
    return materialize_catalog_recipes(_recipes(), _nutrition_catalog())


def _solver_blocking_reasons(recipe) -> list[str]:
    """Explain why a published display recipe is not a trusted planning input."""

    if recipe.solver_eligible:
        return []
    metrics = calculate_recipe_from_catalog(recipe, _nutrition_catalog())
    reasons: list[str] = []
    if any(not item.allergen_composition_known for item in recipe.ingredients):
        reasons.append("ALLERGEN_COMPOSITION_INCOMPLETE")
    if metrics.missing_nutrition_ids:
        reasons.append("NUTRITION_COVERAGE_INCOMPLETE")
    if metrics.unresolved_quantity_ids:
        reasons.append("NUTRITION_QUANTITY_INCOMPLETE")
    if metrics.nutrition_per_serving is None:
        reasons.append("NUTRITION_NOT_CALCULABLE")
    return reasons or ["RECIPE_NOT_SOLVER_ELIGIBLE"]


def _history_service() -> MealPlanHistoryService:
    recipes = _recipes()
    versions = sorted({recipe.source.data_version for recipe in recipes})
    return MealPlanHistoryService(
        meal_plan_history_store,
        durable_runs.store,
        {recipe.recipe_id: recipe.title for recipe in recipes},
        "+".join(versions),
    )


def _history_recipe_counts(user_id: str | None, enabled: bool) -> dict[str, int]:
    if not enabled or user_id is None:
        return {}
    counts: dict[str, int] = {}
    for item in meal_plan_history_store.recent(user_id, limit=10):
        for meal in item.meals:
            counts[meal.recipe_id] = counts.get(meal.recipe_id, 0) + 1
    return counts


def _explicit_feedback_scores(user_id: str | None, enabled: bool) -> dict[str, int]:
    return meal_plan_history_store.explicit_recipe_scores(user_id) if enabled and user_id else {}


def _require_catalog_ready() -> None:
    """Prevent planning against an empty or incomplete production catalog."""
    report = _catalog_coverage()
    if not report.complete:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "CATALOG_COVERAGE_INCOMPLETE", "catalog_coverage": report.model_dump(mode="json")},
        )
    readiness = _product_readiness()
    if not readiness.ready:
        raise HTTPException(
            status_code=409,
            detail={"reason_code": "PRODUCT_NOT_READY", "product_readiness": readiness.model_dump(mode="json")},
        )


def _product_readiness() -> ProductReadiness:
    recipes = _recipes()
    readable_recipes = _published_readable_recipes()
    eligible = [recipe for recipe in recipes if recipe.solver_eligible]
    per_slot = {slot: sum(slot in recipe.supported_slots for recipe in eligible) for slot in MealSlot}
    menu_slot_counts = {slot: sum(slot in recipe.supported_slots for recipe in recipes) for slot in MealSlot}
    coverage = _catalog_coverage()
    reasons: list[str] = []
    if not recipes:
        reasons.append("NO_PUBLISHED_RECIPES")
    if not eligible:
        reasons.append("NO_SOLVER_ELIGIBLE_RECIPES")
    for slot, reason in (
        (MealSlot.BREAKFAST, "BREAKFAST_COVERAGE_MISSING"),
        (MealSlot.LUNCH, "LUNCH_COVERAGE_MISSING"),
        (MealSlot.DINNER, "DINNER_COVERAGE_MISSING"),
    ):
        if per_slot[slot] == 0:
            reasons.append(reason)
    if not coverage.complete:
        reasons.append("NUTRITION_COVERAGE_INCOMPLETE")
    return ProductReadiness(
        ready=not reasons, reason_codes=reasons, published_recipe_count=len(recipes),
        solver_eligible_count=len(eligible), per_slot_count=per_slot,
        catalog_coverage_complete=coverage.complete,
        display_recipe_count=len(readable_recipes),
        menu_draft_recipe_count=len(recipes),
        menu_draft_per_slot_count=menu_slot_counts,
        menu_draft_slot_coverage_complete=all(menu_slot_counts[slot] > 0 for slot in MealSlot),
    )


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "milestone": "frozen-mvp"}


@app.get("/v1/recipes", response_model=list[ReadableRecipe], tags=["recipes"])
def readable_recipe_catalog(_: AccountSummary = Depends(current_account)) -> list[ReadableRecipe]:
    """Return cooking references only; this endpoint makes no planning claims."""

    return _published_readable_recipes()


@app.get("/ready", tags=["system"])
def ready() -> dict[str, str | bool]:
    if production_settings.postgres_enabled:
        try:
            durable_runs.store.events_after("readiness-probe", 0)
        except Exception as error:
            raise HTTPException(status_code=503, detail="database unavailable") from error
    return {
        "status": "ready",
        "storage": "postgresql" if production_settings.postgres_enabled else "sqlite",
        "product_ready": _product_readiness().ready,
    }


@app.get("/ready/product", tags=["system"])
def ready_product() -> dict[str, str]:
    """Strict readiness for endpoints that promise a nutrition-validated plan."""
    readiness = _product_readiness()
    if not readiness.ready:
        raise HTTPException(status_code=503, detail={"reason_code": "PRODUCT_NOT_READY", "product_readiness": readiness.model_dump(mode="json")})
    return {"status": "ready", "capability": "nutrition_validated_planning"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        from fastapi.responses import Response
    except ImportError as error:
        raise HTTPException(status_code=404, detail="metrics are disabled") from error
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/data-quality/catalog-coverage", response_model=CatalogCoverageReport, tags=["data-quality"])
def catalog_coverage() -> CatalogCoverageReport:
    return _catalog_coverage()


@app.get("/v1/readiness/product", response_model=ProductReadiness, tags=["system"])
def product_readiness() -> ProductReadiness:
    return _product_readiness()


@app.get("/v1/admin/acquisition/policies", response_model=list[SourcePolicySummary], tags=["admin"])
def admin_acquisition_policies(_: AccountSummary = Depends(current_admin)) -> list[SourcePolicySummary]:
    return [policy_summary(policy) for policy in list_source_policies(PROJECT_ROOT)]


@app.post("/v1/admin/acquisition/previews", response_model=RecipeAcquisitionPreview, tags=["admin"])
def admin_acquisition_preview(command: CreateRecipeAcquisitionJobCommand, _: AccountSummary = Depends(current_admin)) -> RecipeAcquisitionPreview:
    try:
        policy = load_source_policy(PROJECT_ROOT, command.policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="source policy not found") from error
    stops: list[str] = []
    if not policy.enabled:
        stops.append("AUTOMATION_POLICY_DISABLED")
    if command.max_records > policy.max_records_per_run:
        stops.append("REQUEST_EXCEEDS_POLICY_LIMIT")
    return RecipeAcquisitionPreview(
        policy=policy_summary(policy), requested_max_records=command.max_records,
        executable=not stops, stops=stops,
    )


@app.post("/v1/admin/acquisition/jobs", response_model=RecipeAcquisitionJob, status_code=202, tags=["admin"])
def admin_create_acquisition_job(command: CreateRecipeAcquisitionJobCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> RecipeAcquisitionJob:
    try:
        policy = load_source_policy(PROJECT_ROOT, command.policy_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="source policy not found") from error
    if not policy.enabled:
        raise HTTPException(status_code=409, detail="AUTOMATION_POLICY_DISABLED")
    if command.max_records > policy.max_records_per_run:
        raise HTTPException(status_code=422, detail="REQUEST_EXCEEDS_POLICY_LIMIT")
    try:
        return recipe_acquisition_jobs.create(command, account.email, idempotency_key)
    except AcquisitionJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/admin/acquisition/jobs", response_model=list[RecipeAcquisitionJob], tags=["admin"])
def admin_list_acquisition_jobs(_: AccountSummary = Depends(current_admin)) -> list[RecipeAcquisitionJob]:
    return recipe_acquisition_jobs.list()


@app.get("/v1/admin/acquisition/jobs/{job_id}", response_model=RecipeAcquisitionJob, tags=["admin"])
def admin_get_acquisition_job(job_id: str, _: AccountSummary = Depends(current_admin)) -> RecipeAcquisitionJob:
    try:
        return recipe_acquisition_jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="acquisition job not found") from error


@app.get("/v1/admin/acquisition/jobs/{job_id}/events", response_model=list[RecipeAcquisitionEvent], tags=["admin"])
def admin_get_acquisition_events(job_id: str, after: int = 0, _: AccountSummary = Depends(current_admin)) -> list[RecipeAcquisitionEvent]:
    try:
        return recipe_acquisition_jobs.events(job_id, after)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="acquisition job not found") from error


@app.post("/v1/admin/acquisition/jobs/{job_id}/cancel", response_model=RecipeAcquisitionJob, tags=["admin"])
def admin_cancel_acquisition_job(job_id: str, command: CancelRecipeAcquisitionJobCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), _: AccountSummary = Depends(current_admin)) -> RecipeAcquisitionJob:
    try:
        return recipe_acquisition_jobs.cancel(job_id, command.expected_job_version, idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="acquisition job not found") from error
    except AcquisitionJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/admin/recipes", response_model=list[AdminRecipeCatalogItem], tags=["admin"])
def admin_recipes(_: AccountSummary = Depends(current_admin)) -> list[AdminRecipeCatalogItem]:
    """Return active recipes and non-active review records without mixing their trust states."""
    items = [AdminRecipeCatalogItem(
        record_id=f"active:{recipe.recipe_id}:{recipe.version}", recipe_id=recipe.recipe_id,
        title=recipe.title, origin="ACTIVE_CATALOG", lifecycle_status="ACTIVE",
        quality_status="SOLVER_READY" if recipe.solver_eligible else "PUBLICATION_READY",
        processing_stage=None,
        solver_eligible=recipe.solver_eligible,
        readable_eligible=True,
        readable_published=True,
        menu_draft_eligible=True,
        supported_slots=recipe.supported_slots,
        servings=recipe.servings, prep_minutes=recipe.prep_minutes, ingredients=recipe.ingredients,
        cooking_steps=recipe.cooking_steps, source_id=recipe.source.source_id,
        source_url=recipe.source.source_url, license=recipe.source.license,
        data_version=recipe.source.data_version,
        solver_blocking_reasons=_solver_blocking_reasons(recipe),
    ) for recipe in _recipes()]
    for review in recipe_review_store.list():
        ingredients = [ingredient for ingredient in review.draft.ingredients if ingredient.canonical_id and ingredient.canonical_name and ingredient.quantity_kind]
        readable_eligible = review.quality_report.readable_eligible
        menu_draft_eligible = (
            review.processing_stage == "FINAL_VALIDATED"
            and review.quality_report.status in {"PUBLICATION_READY", "SOLVER_READY"}
        )
        items.append(AdminRecipeCatalogItem(
            record_id=review.review_id, recipe_id=review.draft.recipe_id, title=review.draft.title,
            origin="REVIEW_QUEUE", lifecycle_status=review.status, quality_status=review.quality_report.status,
            processing_stage=review.processing_stage,
            solver_eligible=False,
            readable_eligible=readable_eligible,
            readable_published=review.readable_publish_receipt is not None,
            menu_draft_eligible=menu_draft_eligible,
            supported_slots=review.draft.supported_slots, servings=review.draft.servings,
            prep_minutes=review.draft.prep_minutes,
            ingredients=[{
                "canonical_id": ingredient.canonical_id, "canonical_name": ingredient.canonical_name,
                "display_quantity": ingredient.display_quantity or ingredient.raw_amount or "unresolved",
                "quantity_kind": ingredient.quantity_kind, "amount_g": ingredient.amount_g,
                "nutrition_calculation_role": ingredient.nutrition_calculation_role,
                "allergens": ingredient.allergens,
                "allergen_composition_known": ingredient.allergen_composition_known,
            } for ingredient in ingredients],
            cooking_steps=review.draft.cooking_steps, source_id=review.raw.source_id,
            source_url=review.raw.source_url, license="PENDING_REVIEW",
            data_version=review.raw.raw_content_hash[:12], blocking_reasons=review.quality_report.blocking_reasons,
            solver_blocking_reasons=review.quality_report.solver_blocking_reasons,
        ))
    return sorted(items, key=lambda item: (item.origin, item.title, item.record_id))


@app.post("/v1/admin/reviews/batch-publish", response_model=AdminBatchPublishResponse, tags=["admin"])
def admin_batch_publish_reviews(command: AdminBatchPublishCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> AdminBatchPublishResponse:
    results: list[AdminBatchPublishResult] = []
    for target in command.items:
        try:
            approve_command = AdminReviewVersionCommand(expected_review_version=target.expected_review_version)
            approved = _admin_review_mutation(
                f"batch-approve:{target.review_id}", f"{idempotency_key}:approve:{target.review_id}", approve_command,
                lambda target=target: _recipe_review_service().approve(target.review_id, target.expected_review_version, account.email),
            )
            publish_command = AdminPublishCommand(expected_review_version=approved.review_version, dataset_version=command.dataset_version)
            published = _admin_review_mutation(
                f"batch-publish:{target.review_id}", f"{idempotency_key}:publish:{target.review_id}", publish_command,
                lambda target=target, approved=approved: _recipe_review_service().publish(target.review_id, approved.review_version, account.email, _published_recipe_path(), command.dataset_version),
            )
            results.append(AdminBatchPublishResult(review_id=target.review_id, status="PUBLISHED", review_version=published.review_version))
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, str) else "BATCH_ITEM_FAILED"
            results.append(AdminBatchPublishResult(review_id=target.review_id, status="FAILED", reason_code=detail))
    published_count = sum(item.status == "PUBLISHED" for item in results)
    return AdminBatchPublishResponse(published_count=published_count, failed_count=len(results) - published_count, results=results)


@app.post("/v1/admin/reviews/batch-publish-readable", response_model=AdminBatchPublishResponse, tags=["admin"])
def admin_batch_publish_readable_reviews(command: AdminBatchPublishCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> AdminBatchPublishResponse:
    results: list[AdminBatchPublishResult] = []
    for target in command.items:
        try:
            published = _admin_review_mutation(
                f"batch-publish-readable:{target.review_id}",
                f"{idempotency_key}:readable:{target.review_id}",
                AdminPublishCommand(expected_review_version=target.expected_review_version, dataset_version=command.dataset_version),
                lambda target=target: _recipe_review_service().publish_readable(target.review_id, target.expected_review_version, account.email, _readable_recipe_path(), command.dataset_version),
            )
            results.append(AdminBatchPublishResult(review_id=target.review_id, status="READABLE_PUBLISHED", review_version=published.review_version))
        except HTTPException as error:
            detail = error.detail if isinstance(error.detail, str) else "BATCH_ITEM_FAILED"
            results.append(AdminBatchPublishResult(review_id=target.review_id, status="FAILED", reason_code=detail))
    published_count = sum(item.status == "READABLE_PUBLISHED" for item in results)
    return AdminBatchPublishResponse(published_count=published_count, failed_count=len(results) - published_count, results=results)


@app.get("/v1/admin/reviews/{review_id}", response_model=AdminReviewDetail, tags=["admin"])
def admin_review_detail(review_id: str, _: AccountSummary = Depends(current_admin)) -> AdminReviewDetail:
    return _admin_review_detail(review_id)


@app.post("/v1/admin/reviews/{review_id}/assist", response_model=LlmReviewJob, status_code=202, tags=["admin"])
def admin_assist_review(review_id: str, command: CreateLlmReviewJobCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> LlmReviewJob:
    try:
        item = recipe_review_store.get(review_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="recipe review not found") from error
    if item.status != "PENDING":
        raise HTTPException(status_code=409, detail="ONLY_PENDING_REVIEW_CAN_BE_ASSISTED")
    if item.review_version != command.expected_review_version:
        raise HTTPException(status_code=409, detail="REVIEW_VERSION_CONFLICT")
    try:
        return llm_review_jobs.create(review_id, command, account.email, idempotency_key)
    except LlmReviewJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/admin/llm-review-jobs/{job_id}", response_model=LlmReviewJob, tags=["admin"])
def admin_get_llm_review_job(job_id: str, _: AccountSummary = Depends(current_admin)) -> LlmReviewJob:
    try:
        return llm_review_jobs.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="LLM review job not found") from error


@app.get("/v1/admin/llm-review-jobs", response_model=list[LlmReviewJob], tags=["admin"])
def admin_list_llm_review_jobs(
    review_id: str | None = None,
    limit: int = 50,
    _: AccountSummary = Depends(current_admin),
) -> list[LlmReviewJob]:
    """Durable task history; used to restore the review page after refresh."""
    return llm_review_jobs.list(review_id=review_id, limit=limit)


@app.get("/v1/admin/llm-review-worker-status", tags=["admin"])
def admin_llm_review_worker_status(_: AccountSummary = Depends(current_admin)) -> dict[str, object]:
    """Expose only safe Worker diagnostics; never return provider secrets."""
    path = recipe_data_paths.worker_status
    if not path.exists():
        return {"state": "NOT_OBSERVED", "reason_code": "WORKER_STATUS_NOT_FOUND"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "UNKNOWN", "reason_code": "WORKER_STATUS_INVALID"}
    allowed = {"state", "updated_at", "provider_configured", "provider_host", "dns_status", "last_llm_error_code", "last_llm_job_id", "last_llm_status", "last_llm_finished_at"}
    return {key: value for key, value in payload.items() if key in allowed}


@app.post("/v1/admin/llm-review-jobs/{job_id}/cancel", response_model=LlmReviewJob, tags=["admin"])
def admin_cancel_llm_review_job(
    job_id: str,
    command: CancelLlmReviewJobCommand,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    _: AccountSummary = Depends(current_admin),
) -> LlmReviewJob:
    try:
        return llm_review_jobs.cancel(job_id, command.expected_job_version, idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="LLM review job not found") from error
    except LlmReviewJobConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.put("/v1/admin/reviews/{review_id}/curation", response_model=AdminReviewDetail, tags=["admin"])
def admin_curate_review(review_id: str, command: AdminReviewCurationCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> AdminReviewDetail:
    try:
        item = recipe_review_store.get(review_id)
        curation = build_trusted_curation(item.raw, command)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="recipe review not found") from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    _admin_review_mutation(
        f"curate:{review_id}", idempotency_key, command,
        lambda: _recipe_review_service().curate(review_id, curation, command.expected_review_version, account.email),
    )
    return _admin_review_detail(review_id)


@app.post("/v1/admin/reviews/{review_id}/approve", response_model=AdminReviewDetail, tags=["admin"])
def admin_approve_review(review_id: str, command: AdminReviewVersionCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> AdminReviewDetail:
    _admin_review_mutation(
        f"approve:{review_id}", idempotency_key, command,
        lambda: _recipe_review_service().approve(review_id, command.expected_review_version, account.email),
    )
    return _admin_review_detail(review_id)


@app.post("/v1/admin/reviews/{review_id}/publish", response_model=AdminReviewDetail, tags=["admin"])
def admin_publish_review(review_id: str, command: AdminPublishCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> AdminReviewDetail:
    _admin_review_mutation(
        f"publish:{review_id}", idempotency_key, command,
        lambda: _recipe_review_service().publish(review_id, command.expected_review_version, account.email, _published_recipe_path(), command.dataset_version),
    )
    return _admin_review_detail(review_id)


@app.post("/v1/admin/reviews/{review_id}/revoke", response_model=AdminReviewDetail, tags=["admin"])
def admin_revoke_review(review_id: str, command: AdminRevokeCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_admin)) -> AdminReviewDetail:
    _admin_review_mutation(
        f"revoke:{review_id}", idempotency_key, command,
        lambda: _recipe_review_service().revoke(review_id, command.expected_review_version, account.email, command.reason, _published_recipe_path(), command.dataset_version),
    )
    return _admin_review_detail(review_id)


@app.post("/v1/auth/register", response_model=AuthSession, status_code=201, tags=["auth"])
def register(command: RegisterCommand, response: Response) -> AuthSession:
    try:
        return _session(account_store.register(command.email, command.password, command.display_name), response)
    except AccountConflict as error:
        raise HTTPException(status_code=409, detail="email is already registered") from error


@app.post("/v1/auth/login", response_model=AuthSession, tags=["auth"])
def login(command: LoginCommand, response: Response) -> AuthSession:
    try:
        return _session(account_store.authenticate(command.email, command.password), response)
    except AuthenticationFailed as error:
        raise HTTPException(status_code=401, detail="email or password is incorrect") from error


@app.post("/v1/auth/refresh", response_model=AuthSession, tags=["auth"])
def refresh_session(request: Request, response: Response) -> AuthSession:
    try:
        account, replacement = account_store.rotate_refresh_session(request.cookies.get(REFRESH_COOKIE, ""), auth_settings.refresh_seconds)
        return _session(account, response, replacement)
    except AuthenticationFailed as error:
        _clear_session_cookies(response)
        raise HTTPException(status_code=401, detail="refresh session is invalid or expired") from error


@app.post("/v1/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response) -> None:
    account_store.revoke_refresh_session(request.cookies.get(REFRESH_COOKIE))
    _clear_session_cookies(response)


@app.get("/v1/account", response_model=AccountSummary, tags=["account"])
def account(account: AccountSummary = Depends(current_account)) -> AccountSummary:
    return account


@app.get("/v1/account/profile", response_model=AccountProfile, tags=["account"])
def get_account_profile(account: AccountSummary = Depends(current_account)) -> AccountProfile:
    return account_store.profile(account.user_id)


@app.put("/v1/account/profile", response_model=AccountProfile, tags=["account"])
def save_account_profile(command: SaveAccountProfileCommand, account: AccountSummary = Depends(current_account)) -> AccountProfile:
    try:
        return account_store.save_profile(account.user_id, command)
    except AccountConflict as error:
        raise HTTPException(status_code=409, detail="profile changed; refresh and retry") from error


@app.get("/v1/privacy/policy", response_model=PrivacyPolicy, tags=["privacy"])
def privacy_policy() -> PrivacyPolicy:
    return PrivacyPolicy(
        policy_version="m23-privacy-v1",
        active_account_data="kept until the account owner deletes it",
        revoked_refresh_token_days=30,
        operational_event_days=90,
        deletion_behavior="account deletion immediately removes active profile, memory, plans, conversations, feedback, runs and credentials",
    )


@app.get("/v1/account/export", tags=["privacy"])
def export_account_data(account: AccountSummary = Depends(current_account)) -> dict[str, object]:
    history = meal_plan_history_store.collection(account.user_id)
    conversations = [conversation_store.get(account.user_id, item.conversation_id) for item in conversation_store.list(account.user_id)]
    records = durable_runs.store.records_for_user(account.user_id)
    return {
        "export_version": "mealpilot-account-export-v1",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": account.model_dump(mode="json"),
        "profile": account_store.profile(account.user_id).model_dump(mode="json"),
        "preference_memory": preference_memory.get(account.user_id).model_dump(mode="json"),
        "history": history.model_dump(mode="json"),
        "feedback": meal_plan_history_store.feedback_collection(account.user_id).model_dump(mode="json"),
        "conversations": [item.model_dump(mode="json") for item in conversations],
        "runs": [{"run_id": item.run_id, "status": item.status, "run_version": item.run_version, "command": item.command, "result": item.result, "proposal": item.proposal} for item in records],
    }


@app.delete("/v1/account", status_code=204, tags=["privacy"])
def delete_account(command: DeleteAccountCommand, response: Response, account: AccountSummary = Depends(current_account)) -> None:
    if not account_store.verify_account_password(account.user_id, command.password):
        raise HTTPException(status_code=401, detail="password is incorrect")
    conversation_store.delete_all_for_account(account.user_id)
    meal_plan_history_store.delete_all_for_account(account.user_id)
    preference_memory.delete(account.user_id)
    durable_runs.store.delete_all_for_account(account.user_id)
    account_store.delete_account(account.user_id)
    _clear_session_cookies(response)


@app.post("/v1/constraint-suggestions", tags=["assistant"])
def suggest_constraints(payload: dict[str, str], _: AccountSummary = Depends(current_account)) -> dict[str, object]:
    """Return query suggestions only; this endpoint never creates or changes a plan."""
    query = payload.get("query", "").strip()
    if not query or len(query) > 1_000:
        raise HTTPException(status_code=422, detail="query must be 1-1000 characters")
    suggested = extract_planning_constraints(query)
    if suggested is not None:
        allowed = {key: value for key, value in suggested.items() if key in {"max_total_minutes", "protein_min_g", "energy_kcal_range"}}
        return {"source": "siliconflow", "constraints": allowed}
    return {
        "source": "deterministic_fallback",
        "constraints": extract_supported_constraints(query),
        "llm_configured": load_siliconflow_settings().configured,
    }


@app.post("/v1/nutrition-target-suggestion", response_model=NutritionTargetSuggestion, tags=["nutrition"])
def nutrition_target_suggestion(profile: UserProfile, _: AccountSummary = Depends(current_account)) -> NutritionTargetSuggestion:
    try:
        return suggest_targets(profile)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/v1/memory/{user_id}", response_model=PreferenceMemory, tags=["memory"])
def get_preference_memory(user_id: str, account: AccountSummary = Depends(current_account)) -> PreferenceMemory:
    _require_owner(user_id, account)
    return preference_memory.get(user_id)


@app.put("/v1/memory/{user_id}", response_model=PreferenceMemory, tags=["memory"])
def save_preference_memory(user_id: str, memory: PreferenceMemory, account: AccountSummary = Depends(current_account)) -> PreferenceMemory:
    _require_owner(user_id, account)
    if memory.user_id != account.user_id:
        raise HTTPException(status_code=403, detail="memory belongs to another account")
    return preference_memory.replace(memory)


@app.delete("/v1/memory/{user_id}", status_code=204, tags=["memory"])
def delete_preference_memory(user_id: str, account: AccountSummary = Depends(current_account)) -> None:
    _require_owner(user_id, account)
    preference_memory.delete(user_id)


@app.get("/v1/history/{user_id}", response_model=HistoryCollection, tags=["history"])
def get_history(user_id: str, account: AccountSummary = Depends(current_account)) -> HistoryCollection:
    _require_owner(user_id, account)
    return meal_plan_history_store.collection(user_id)


@app.get("/v1/history/{user_id}/{history_id}", response_model=AdoptedMealPlan, tags=["history"])
def get_history_item(user_id: str, history_id: str, account: AccountSummary = Depends(current_account)) -> AdoptedMealPlan:
    _require_owner(user_id, account)
    try:
        return meal_plan_history_store.get(user_id, history_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="history plan not found") from error


@app.post("/v1/history/{user_id}/adoptions", response_model=AdoptedMealPlan, status_code=201, tags=["history"])
def adopt_history_plan(user_id: str, command: AdoptPlanCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> AdoptedMealPlan:
    _require_owner(user_id, account)
    _require_run_owner(command.run_id, account)
    try:
        return _history_service().adopt(user_id, command.run_id, command.expected_run_version, idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="run not found") from error
    except AdoptionRejected as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except HistoryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.delete("/v1/history/{user_id}/{history_id}", response_model=HistoryCollection, tags=["history"])
def delete_history_plan(user_id: str, history_id: str, expected_version: int, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> HistoryCollection:
    _require_owner(user_id, account)
    try:
        return meal_plan_history_store.delete(user_id, history_id, expected_version, idempotency_key)
    except HistoryConflict as error:
        raise HTTPException(status_code=409, detail="history changed; refresh and retry") from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="history plan not found") from error


@app.delete("/v1/history/{user_id}", response_model=HistoryCollection, tags=["history"])
def clear_history(user_id: str, expected_version: int, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> HistoryCollection:
    _require_owner(user_id, account)
    try:
        return meal_plan_history_store.clear(user_id, expected_version, idempotency_key)
    except HistoryConflict as error:
        raise HTTPException(status_code=409, detail="history changed; refresh and retry") from error


@app.get("/v1/feedback/{user_id}", response_model=FeedbackCollection, tags=["feedback"])
def get_feedback(user_id: str, account: AccountSummary = Depends(current_account)) -> FeedbackCollection:
    _require_owner(user_id, account)
    return meal_plan_history_store.feedback_collection(user_id)


@app.put("/v1/feedback/{user_id}/{history_id}", response_model=PlanFeedback, tags=["feedback"])
def save_feedback(user_id: str, history_id: str, command: SavePlanFeedbackCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> PlanFeedback:
    _require_owner(user_id, account)
    try:
        return meal_plan_history_store.save_feedback(user_id, history_id, command, idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="history plan not found") from error
    except HistoryConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/v1/users/{user_id}/conversations", response_model=list[ConversationSummary], tags=["chat"])
def list_conversations(user_id: str, account: AccountSummary = Depends(current_account)) -> list[ConversationSummary]:
    _require_owner(user_id, account)
    return conversation_store.list(user_id)


@app.post("/v1/users/{user_id}/conversations", response_model=ConversationDetail, status_code=201, tags=["chat"])
def create_conversation(user_id: str, command: CreateConversationCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> ConversationDetail:
    _require_owner(user_id, account)
    return conversation_store.create(user_id, command.title, idempotency_key)


@app.get("/v1/users/{user_id}/conversations/{conversation_id}", response_model=ConversationDetail, tags=["chat"])
def get_conversation(user_id: str, conversation_id: str, account: AccountSummary = Depends(current_account)) -> ConversationDetail:
    _require_owner(user_id, account)
    try:
        return conversation_store.get(user_id, conversation_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="conversation not found") from error


@app.delete("/v1/users/{user_id}/conversations/{conversation_id}", status_code=204, tags=["chat"])
def delete_conversation(user_id: str, conversation_id: str, expected_version: int, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> None:
    _require_owner(user_id, account)
    try:
        conversation_store.delete(user_id, conversation_id, expected_version, idempotency_key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="conversation not found") from error
    except ConversationConflict as error:
        raise HTTPException(status_code=409, detail="conversation changed; refresh and retry") from error


@app.post("/v1/chat", response_model=ChatResponse, tags=["chat"])
def chat(request: ChatRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> ChatResponse:
    if request.user_id is not None and request.user_id != account.user_id:
        raise HTTPException(status_code=403, detail="user_id cannot be supplied for another account")
    request = request.model_copy(update={"user_id": account.user_id})
    memory = preference_memory.get(request.user_id) if request.use_preferences else PreferenceMemory(user_id=request.user_id)
    current = request.plan_context if request.use_current_plan else None
    if request.use_history and request.history_plan_ids is not None:
        try:
            history = [meal_plan_history_store.get(request.user_id, history_id) for history_id in request.history_plan_ids]
        except KeyError as error:
            raise HTTPException(status_code=422, detail=f"selected history plan does not exist: {error.args[0]}") from error
    else:
        history = meal_plan_history_store.recent(request.user_id, limit=10) if request.use_history else []

    prior_messages = []
    if request.conversation_id is not None:
        if not idempotency_key:
            raise HTTPException(status_code=422, detail="Idempotency-Key is required for persistent chat")
        try:
            detail = conversation_store.get(request.user_id, request.conversation_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="conversation not found") from error
        prior_messages = detail.messages

    answer = answer_chat(request.message, memory, current, history, prior_messages)
    if request.conversation_id is None:
        return answer
    try:
        return conversation_store.append_exchange(
            request.user_id,
            request.conversation_id,
            request.expected_conversation_version or 0,
            request.message,
            answer,
            request.model_dump(mode="json"),
            idempotency_key or "",
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="conversation not found") from error
    except ConversationConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/v1/meal-plans/deterministic", response_model=MealPlan | PlanningFailure, tags=["planning"])
def plan_day(command: DeterministicPlanningCommand, _: AccountSummary = Depends(current_account)) -> MealPlan | PlanningFailure:
    _require_catalog_ready()
    return create_deterministic_plan(_planning_recipes(), command.profile, command.request)


@app.post("/v1/menu-drafts", response_model=MenuDraft | MenuDraftFailure, tags=["planning"])
def menu_draft(command: MenuDraftCommand, _: AccountSummary = Depends(current_account)) -> MenuDraft | MenuDraftFailure:
    """Arrange published recipes without making nutrition or medical validation claims."""

    return create_menu_draft(_recipes(), command)


@app.post("/v1/agent-runs", response_model=AgentRunResult, tags=["agent"])
def start_agent_run(command: AgentPlanningCommand, account: AccountSummary = Depends(current_account)) -> AgentRunResult:
    command = _bind_command_owner(command, account)
    _require_catalog_ready()
    return run_agent(command, _planning_recipes(), history_recipe_counts=_history_recipe_counts(command.user_id, command.use_history), explicit_feedback_scores=_explicit_feedback_scores(command.user_id, command.use_history))


@app.post("/v1/runs", response_model=DurableRunSnapshot, status_code=202, tags=["runtime"])
def create_durable_run(command: AgentPlanningCommand, background_tasks: BackgroundTasks, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> DurableRunSnapshot:
    command = _bind_command_owner(command, account)
    _require_catalog_ready()
    snapshot, created = durable_runs.create(command, idempotency_key)
    if created:
        if run_queue is not None:
            run_queue.enqueue(command.run_id)
        else:
            background_tasks.add_task(durable_runs.execute, command.run_id, _planning_recipes(), _history_recipe_counts(command.user_id, command.use_history), _explicit_feedback_scores(command.user_id, command.use_history))
    return snapshot


@app.get("/v1/runs/{run_id}", response_model=DurableRunSnapshot, tags=["runtime"])
def get_durable_run(run_id: str, account: AccountSummary = Depends(current_account)) -> DurableRunSnapshot:
    _require_run_owner(run_id, account)
    try:
        return durable_runs.get(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="run not found") from error


@app.get("/v1/runs/{run_id}/audit", response_model=RunAuditSummary, tags=["observability"])
def get_durable_run_audit(run_id: str, account: AccountSummary = Depends(current_account)) -> RunAuditSummary:
    _require_run_owner(run_id, account)
    try:
        return durable_runs.audit_summary(run_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="run not found") from error


@app.post("/v1/runs/{run_id}/decisions", response_model=DurableRunSnapshot, tags=["runtime"])
def decide_durable_run(run_id: str, decision: RunDecisionCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> DurableRunSnapshot:
    _require_catalog_ready()
    _require_run_owner(run_id, account)
    try:
        record = durable_runs.store.get(run_id)
        original = AgentPlanningCommand.model_validate(record.command)
        return durable_runs.decide(run_id, decision, idempotency_key, _planning_recipes(), _history_recipe_counts(original.user_id, original.use_history), _explicit_feedback_scores(original.user_id, original.use_history))
    except RunConflict as error:
        raise HTTPException(status_code=409, detail="stale run version or run is not paused") from error
    except (KeyError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/v1/runs/{run_id}/cancel", response_model=DurableRunSnapshot, tags=["runtime"])
def cancel_durable_run(run_id: str, command: RunCancelCommand, idempotency_key: str = Header(..., alias="Idempotency-Key"), account: AccountSummary = Depends(current_account)) -> DurableRunSnapshot:
    _require_run_owner(run_id, account)
    try:
        snapshot = durable_runs.cancel(run_id, command, idempotency_key)
        if run_queue is not None:
            run_queue.cancel(run_id)
        return snapshot
    except RunConflict as error:
        raise HTTPException(status_code=409, detail="run changed or is already terminal; refresh and retry") from error


@app.get("/v1/runs/{run_id}/events", tags=["runtime"])
async def stream_run_events(run_id: str, last_event_id: int = 0, last_event_id_header: str | None = Header(None, alias="Last-Event-ID"), account: AccountSummary = Depends(current_account)) -> StreamingResponse:
    _require_run_owner(run_id, account)
    if last_event_id_header is not None:
        try:
            last_event_id = int(last_event_id_header)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from error
    async def events() -> object:
        cursor = last_event_id
        yield "retry: 1500\n\n"
        for _ in range(300):
            emitted = False
            for event in durable_runs.store.events_after(run_id, cursor):
                emitted = True
                cursor = event["event_id"]
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            try:
                terminal = durable_runs.get(run_id).status in {"PAUSED", "COMPLETED", "FAILED", "CANCELLED"}
            except KeyError:
                terminal = True
            if terminal and not emitted:
                break
            await asyncio.sleep(0.1)
    return StreamingResponse(events(), media_type="text/event-stream")
