"""M0 contract models; see docs/domain_contracts.md for the JSON contract."""

from decimal import Decimal
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class MealSlot(StrEnum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


class ActivityLevel(StrEnum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


class NutritionGoal(StrEnum):
    LOSE = "lose"
    MAINTAIN = "maintain"
    GAIN = "gain"


class IngredientQuantityKind(StrEnum):
    MEASURED = "MEASURED"
    QUALITATIVE = "QUALITATIVE"
    UNSPECIFIED = "UNSPECIFIED"


class QuantityOrigin(StrEnum):
    SOURCE_EXPLICIT = "SOURCE_EXPLICIT"
    DISPLAY_FALLBACK = "DISPLAY_FALLBACK"
    REVIEWER_CONFIRMED = "REVIEWER_CONFIRMED"


class SolverStatus(StrEnum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


class CoverageStatus(StrEnum):
    EMPTY_RETRIEVAL = "EMPTY_RETRIEVAL"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    READY = "READY"


class SourceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    source_url: HttpUrl
    license: str
    data_version: str


class Nutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    energy_kcal: NonNegativeDecimal
    protein_g: NonNegativeDecimal
    carbohydrate_g: NonNegativeDecimal
    fat_g: NonNegativeDecimal


class RecipeIngredient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    canonical_name: str
    display_quantity: str = Field(min_length=1, max_length=100)
    quantity_kind: IngredientQuantityKind
    quantity_origin: QuantityOrigin = QuantityOrigin.SOURCE_EXPLICIT
    amount_g: PositiveDecimal | None = None
    nutrition_calculation_role: Literal["INCLUDED", "EXCLUDED_MINOR_INGREDIENT"] = "INCLUDED"
    allergens: list[str] = Field(default_factory=list)
    allergen_composition_known: bool = True

    @model_validator(mode="after")
    def quantity_contract(self) -> "RecipeIngredient":
        if self.quantity_kind == IngredientQuantityKind.MEASURED and self.amount_g is None:
            raise ValueError("MEASURED ingredient requires amount_g")
        if self.quantity_kind in {IngredientQuantityKind.QUALITATIVE, IngredientQuantityKind.UNSPECIFIED} and self.amount_g is not None:
            raise ValueError("non-measured ingredient cannot carry amount_g")
        if self.quantity_origin == QuantityOrigin.DISPLAY_FALLBACK and self.quantity_kind != IngredientQuantityKind.UNSPECIFIED:
            raise ValueError("DISPLAY_FALLBACK is only valid for UNSPECIFIED quantity")
        if self.nutrition_calculation_role == "EXCLUDED_MINOR_INGREDIENT" and self.quantity_kind != IngredientQuantityKind.QUALITATIVE:
            raise ValueError("only a qualitative ingredient may be excluded as a minor ingredient")
        return self


class CookingStep(BaseModel):
    """A user-visible cooking instruction; source review happens before publication."""

    model_config = ConfigDict(extra="forbid")
    step_number: int = Field(ge=1)
    instruction: str = Field(min_length=1, max_length=2_000)
    duration_minutes: int | None = Field(default=None, ge=0)
    ingredient_refs: list[str] = Field(default_factory=list)


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    version: str
    title: str
    supported_slots: list[MealSlot]
    servings: PositiveDecimal
    prep_minutes: int = Field(ge=0)
    nutrition_per_serving: Nutrition | None
    nutrition_basis: Literal["CALCULATED_FROM_INGREDIENTS", "SOURCE_DECLARED", "REVIEWED_STANDARD_PORTION"] | None
    solver_eligible: bool
    ingredients: list[RecipeIngredient]
    cooking_steps: list[CookingStep] = Field(default_factory=list)
    source: SourceMetadata
    numeric_policy_version: str
    nutrition_data_version: str | None = None

    @field_validator("supported_slots")
    @classmethod
    def require_slot(cls, value: list[MealSlot]) -> list[MealSlot]:
        if not value:
            raise ValueError("a recipe must support at least one meal slot")
        return value

    @field_validator("cooking_steps")
    @classmethod
    def require_ordered_steps(cls, value: list[CookingStep]) -> list[CookingStep]:
        if value and [step.step_number for step in value] != list(range(1, len(value) + 1)):
            raise ValueError("cooking steps must be numbered consecutively from 1")
        return value

    @model_validator(mode="after")
    def solver_ready_recipe_has_nutrition(self) -> "Recipe":
        if self.solver_eligible and (self.nutrition_per_serving is None or self.nutrition_basis is None):
            raise ValueError("solver-eligible recipe requires an authoritative nutrition basis")
        if self.solver_eligible and any(
            ingredient.quantity_kind == IngredientQuantityKind.UNSPECIFIED for ingredient in self.ingredients
        ):
            raise ValueError("solver-eligible recipe cannot contain unspecified quantities")
        return self


class ReadableRecipeIngredient(BaseModel):
    """Source-preserving ingredient text for cooking-reference publication."""

    model_config = ConfigDict(extra="forbid")
    group: Literal["main", "secondary", "seasoning", "other"]
    raw_name: str = Field(min_length=1, max_length=200)
    display_quantity: str = Field(min_length=1, max_length=100)


class ReadableRecipe(BaseModel):
    """A reviewed cooking reference that is not necessarily a planning input."""

    model_config = ConfigDict(extra="forbid")
    recipe_id: str
    version: str
    title: str = Field(min_length=1, max_length=120)
    supported_slots: list[MealSlot] = Field(default_factory=list)
    servings: PositiveDecimal | None = None
    prep_minutes: int | None = Field(default=None, ge=0)
    ingredients: list[ReadableRecipeIngredient] = Field(min_length=1)
    cooking_steps: list[CookingStep] = Field(min_length=1)
    source: SourceMetadata
    warnings: list[str] = Field(default_factory=list)
    numeric_policy_version: str


class AdminRecipeCatalogItem(BaseModel):
    """Read-only projection for administrators; review rows are never planning inputs."""

    model_config = ConfigDict(extra="forbid")
    record_id: str
    recipe_id: str | None = None
    title: str
    origin: Literal["ACTIVE_CATALOG", "REVIEW_QUEUE"]
    lifecycle_status: Literal["ACTIVE", "PENDING", "APPROVED", "REJECTED", "PUBLISHED", "REVOKED"]
    quality_status: Literal["BLOCKED", "PUBLICATION_READY", "SOLVER_READY"] | None = None
    processing_stage: Literal["INITIAL_VALIDATED", "LLM_FAILED", "FINAL_VALIDATION_BLOCKED", "FINAL_VALIDATED"] | None = None
    solver_eligible: bool
    # A record may be readable before it is usable in an unverified menu, and
    # menu-capable before it has authoritative nutrition for CP-SAT.
    # Defaults keep historic review projections backward compatible.
    readable_eligible: bool = False
    readable_published: bool = False
    menu_draft_eligible: bool = False
    supported_slots: list[MealSlot] = Field(default_factory=list)
    servings: PositiveDecimal | None = None
    prep_minutes: int | None = Field(default=None, ge=0)
    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    cooking_steps: list[CookingStep] = Field(default_factory=list)
    source_id: str
    source_url: HttpUrl
    license: str
    data_version: str
    blocking_reasons: list[str] = Field(default_factory=list)
    solver_blocking_reasons: list[str] = Field(default_factory=list)

class DecimalRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: NonNegativeDecimal
    max: NonNegativeDecimal

    @field_validator("max")
    @classmethod
    def max_must_be_valid(cls, value: Decimal, info: object) -> Decimal:
        minimum = getattr(info, "data", {}).get("min")
        if minimum is not None and value < minimum:
            raise ValueError("max must be greater than or equal to min")
        return value


class UserProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_snapshot_id: str
    adult_confirmed: Literal[True]
    age_years: int = Field(ge=18)
    nutrition_parameter_sex: Literal["female", "male", "unspecified"]
    height_cm: PositiveDecimal
    weight_kg: PositiveDecimal
    activity_level: ActivityLevel
    goal: NutritionGoal
    allergens: list[str]
    avoidances: list[str]


class NutritionTargetSuggestion(BaseModel):
    """Advisory healthy-adult estimate; never becomes authoritative without user confirmation."""
    model_config = ConfigDict(extra="forbid")
    policy_version: Literal["healthy-adult-estimate-v1"] = "healthy-adult-estimate-v1"
    energy_kcal_range: "DecimalRange"
    protein_min_g: NonNegativeDecimal
    resting_energy_kcal: NonNegativeDecimal
    estimated_daily_energy_kcal: NonNegativeDecimal
    method: Literal["mifflin-st-jeor-activity-goal"] = "mifflin-st-jeor-activity-goal"
    requires_user_confirmation: Literal[True] = True
    warnings: list[str] = Field(min_length=1)
    source_references: list[str] = Field(min_length=1)


class MealPlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    meal_slots: list[MealSlot] = Field(
        default_factory=lambda: [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER]
    )
    persons: Literal[1] = 1
    max_total_minutes: int = Field(ge=0)
    energy_kcal_range: DecimalRange
    protein_min_g: NonNegativeDecimal = Decimal("0")
    numeric_policy_version: str

    @field_validator("meal_slots")
    @classmethod
    def require_unique_slots(cls, value: list[MealSlot]) -> list[MealSlot]:
        if set(value) != {MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER}:
            raise ValueError("M1 requires breakfast, lunch, and dinner exactly once")
        if len(value) != 3:
            raise ValueError("meal slots must be unique")
        return value


class CandidateCoverageReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CoverageStatus
    per_slot_count: dict[MealSlot, int]
    excluded_recipe_ids: list[str]
    exclusion_reasons: dict[str, list[str]]


class PlanSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: MealSlot
    recipe_id: str
    recipe_version: str
    portion: Literal["0.5", "1.0", "1.5", "2.0"]
    recipe_title: str | None = None
    ingredients: list[RecipeIngredient] = Field(default_factory=list)


class PlanTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    energy_kcal: NonNegativeDecimal
    protein_g: NonNegativeDecimal
    carbohydrate_g: NonNegativeDecimal
    fat_g: NonNegativeDecimal
    prep_minutes: int = Field(ge=0)


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    valid: bool
    violations: list[str]
    warnings: list[str]
    totals: PlanTotals | None
    validator_version: str
    nutrition_data_version: str | None = None


class MealPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str
    selections: list[PlanSelection]
    totals: PlanTotals
    solver_status: SolverStatus
    validation_report: ValidationReport | None
    numeric_policy_version: str


class MenuDraftCommand(BaseModel):
    """Explicit opt-in to a non-validated cooking menu, not a nutrition plan."""

    model_config = ConfigDict(extra="forbid")
    draft_id: str = Field(min_length=1, max_length=100)
    profile: UserProfile
    max_total_minutes: int | None = Field(default=None, ge=0)
    acknowledge_unverified: Literal[True]


class MenuDraftMeal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: MealSlot
    recipe_id: str
    recipe_version: str
    recipe_title: str
    ingredients: list[RecipeIngredient]
    cooking_steps: list[CookingStep]
    prep_minutes: int = Field(ge=0)
    nutrition_per_serving: Nutrition | None
    source: SourceMetadata
    warnings: list[str] = Field(default_factory=list)


class MenuDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    status: Literal["UNVERIFIED_MENU"] = "UNVERIFIED_MENU"
    meals: list[MenuDraftMeal] = Field(min_length=3, max_length=3)
    total_prep_minutes: int = Field(ge=0)
    nutrition_totals: Nutrition | None
    nutrition_complete: bool
    warnings: list[str] = Field(min_length=1)
    policy_version: Literal["menu-draft-v1"] = "menu-draft-v1"
    numeric_policy_version: Literal["menu-draft-decimal-v1"] = "menu-draft-decimal-v1"
    nutrition_data_versions: list[str] = Field(default_factory=list)


class MenuDraftFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["FAILED"] = "FAILED"
    reason_code: Literal["NO_PUBLISHED_RECIPES", "INSUFFICIENT_SAFE_DISPLAY_RECIPES", "INSUFFICIENT_MEAL_SLOT_COVERAGE"]
    message: str
    excluded_recipe_ids: list[str] = Field(default_factory=list)
    exclusion_reasons: dict[str, list[str]] = Field(default_factory=dict)


class PlanningFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CoverageStatus | SolverStatus
    reason_code: str
    message: str
    coverage_report: CandidateCoverageReport


class DeterministicPlanningCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: UserProfile
    request: MealPlanningRequest


class AgentRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: Literal["analyze", "retrieve", "solve", "validate", "repair", "finalize"]
    outcome: str
    safe_payload: dict[str, str | int | list[str]] = Field(default_factory=dict)


class AgentPlanningCommand(BaseModel):
    """M2 input: structured request remains authoritative over free text."""
    model_config = ConfigDict(extra="forbid")
    run_id: str
    query: str = Field(min_length=1, max_length=1_000)
    profile: UserProfile
    request: MealPlanningRequest
    retrieval_top_k_per_slot: int = Field(default=1, ge=1, le=10)
    user_id: str | None = Field(default=None, min_length=1, max_length=80)
    use_history: bool = False


class AgentRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: AgentRunStatus
    parsed_constraints: dict[str, str]
    attempted_strategies: list[str]
    trace: list[AgentTraceEvent]
    result: MealPlan | PlanningFailure
    explanation: str | None = None


class NegotiationOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str
    field: Literal["max_total_minutes", "protein_min_g", "energy_kcal_range"]
    proposed_value: str
    impact: str


class NegotiationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    reason_code: str
    options: list[NegotiationOption] = Field(min_length=1, max_length=3)
    explanation: str


class NegotiationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str


class NegotiationPaused(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: Literal["PAUSED"]
    proposal: NegotiationProposal


class NegotiationCompleted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: Literal["COMPLETED", "FAILED"]
    result: AgentRunResult


class RunDecisionCommand(NegotiationDecision):
    expected_run_version: int = Field(ge=0)


class RunCancelCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_run_version: int = Field(ge=0)


class DurableRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: Literal["QUEUED", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"]
    run_version: int
    result: AgentRunResult | None = None
    proposal: NegotiationProposal | None = None


class RunAuditSummary(BaseModel):
    """Safe operational metadata; never includes raw health-profile fields or free text."""
    model_config = ConfigDict(extra="forbid")
    run_id: str
    status: str
    run_version: int
    profile_fingerprint: str
    numeric_policy_version: str
    event_types: list[str]


class PreferenceMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["food_preference", "avoidance", "cooking_style"]
    value: str = Field(min_length=1, max_length=120)


class PreferenceMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=80)
    items: list[PreferenceMemoryItem] = Field(default_factory=list, max_length=50)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str | None = Field(default=None, min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=1_000)
    plan_context: "ChatPlanContext | None" = None
    use_current_plan: bool = True
    use_history: bool = True
    use_preferences: bool = True
    conversation_id: str | None = Field(default=None, min_length=1, max_length=120)
    expected_conversation_version: int | None = Field(default=None, ge=0)
    history_plan_ids: list[str] | None = Field(default=None, max_length=10)

    @model_validator(mode="after")
    def conversation_fields_are_paired(self) -> "ChatRequest":
        if (self.conversation_id is None) != (self.expected_conversation_version is None):
            raise ValueError("conversation_id and expected_conversation_version must be provided together")
        if self.history_plan_ids is not None and len(set(self.history_plan_ids)) != len(self.history_plan_ids):
            raise ValueError("history_plan_ids must be unique")
        return self


class ChatPlanContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=120)
    meals: list[str] = Field(min_length=1, max_length=3)
    totals: PlanTotals


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str
    memories_used: list[PreferenceMemoryItem] = Field(default_factory=list)
    response_source: Literal["siliconflow", "deterministic_fallback"]
    current_plan_used: str | None = None
    history_plans_used: list["HistoryPlanReference"] = Field(default_factory=list)
    conversation_id: str | None = None
    conversation_version: int | None = Field(default=None, ge=0)
    user_message_id: str | None = None
    assistant_message_id: str | None = None


class AdoptedMeal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: MealSlot
    recipe_id: str
    recipe_version: str
    recipe_title: str
    portion: Literal["0.5", "1.0", "1.5", "2.0"]
    ingredients: list[RecipeIngredient] = Field(default_factory=list)


class PlanningConstraintsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_total_minutes: int
    energy_kcal_range: DecimalRange
    protein_min_g: NonNegativeDecimal


class AdoptedMealPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history_id: str
    history_version: int = Field(ge=1)
    user_id: str = Field(min_length=1, max_length=80)
    original_run_id: str
    original_plan_id: str
    adopted_at: datetime
    meals: list[AdoptedMeal] = Field(min_length=3, max_length=3)
    verified_totals: PlanTotals
    planning_constraints: PlanningConstraintsSnapshot
    profile_snapshot_id: str
    validation_report: ValidationReport
    recipe_data_version: str
    nutrition_data_version: str | None = None
    numeric_policy_version: str
    status: Literal["ADOPTED"] = "ADOPTED"


class AdoptPlanCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    expected_run_version: int = Field(ge=0)


class HistoryCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    collection_version: int = Field(ge=0)
    items: list[AdoptedMealPlan]


class MealFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot: MealSlot
    outcome: Literal["COMPLETED", "SKIPPED", "REPLACED"]
    rating: int | None = Field(default=None, ge=1, le=5)
    replacement_recipe_id: str | None = Field(default=None, min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def replacement_matches_outcome(self) -> "MealFeedback":
        if self.outcome == "REPLACED" and self.replacement_recipe_id is None:
            raise ValueError("replacement_recipe_id is required for REPLACED feedback")
        if self.outcome != "REPLACED" and self.replacement_recipe_id is not None:
            raise ValueError("replacement_recipe_id is only allowed for REPLACED feedback")
        return self


class RecipeFeedbackDirective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str = Field(min_length=1, max_length=120)
    action: Literal["REUSE", "AVOID"]


class SavePlanFeedbackCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_feedback_version: int = Field(ge=0)
    meals: list[MealFeedback] = Field(min_length=1, max_length=3)
    directives: list[RecipeFeedbackDirective] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def entries_are_unique(self) -> "SavePlanFeedbackCommand":
        if len({item.slot for item in self.meals}) != len(self.meals):
            raise ValueError("meal feedback slots must be unique")
        if len({item.recipe_id for item in self.directives}) != len(self.directives):
            raise ValueError("recipe directives must be unique")
        return self


class PlanFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback_id: str
    user_id: str = Field(min_length=1, max_length=80)
    history_id: str
    feedback_version: int = Field(ge=1)
    submitted_at: datetime
    meals: list[MealFeedback]
    directives: list[RecipeFeedbackDirective]
    feedback_policy_version: Literal["explicit-feedback-v1"] = "explicit-feedback-v1"


class FeedbackCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    items: list[PlanFeedback]


class HistoryPlanReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    history_id: str
    original_plan_id: str
    adopted_at: datetime


class CreateConversationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=80)


class ConversationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str
    user_id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=80)
    conversation_version: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(ge=0)


class ConversationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)
    created_at: datetime
    response_source: Literal["siliconflow", "deterministic_fallback"] | None = None
    memories_used: list[PreferenceMemoryItem] = Field(default_factory=list)
    current_plan_used: str | None = None
    history_plans_used: list[HistoryPlanReference] = Field(default_factory=list)


class ConversationDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: ConversationSummary
    messages: list[ConversationMessage]


ChatResponse.model_rebuild()


class RegisterCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized.count("@") != 1 or "." not in normalized.rsplit("@", 1)[1]:
            raise ValueError("a valid email address is required")
        return normalized


class LoginCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_login_email(cls, value: str) -> str:
        return value.strip().casefold()


class AccountSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    email: str
    display_name: str
    created_at: datetime
    role: Literal["USER", "ADMIN"] = "USER"


class AuthSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)
    account: AccountSummary


class AccountProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_version: int = Field(ge=0)
    profile: UserProfile | None = None
    updated_at: datetime | None = None


class SaveAccountProfileCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_profile_version: int = Field(ge=0)
    profile: UserProfile


class DeleteAccountCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)
    confirmation: Literal["DELETE MY ACCOUNT"]


class PrivacyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_version: str
    active_account_data: str
    revoked_refresh_token_days: int
    operational_event_days: int
    deletion_behavior: str


class ProductReadiness(BaseModel):
    """Business readiness is separate from infrastructure liveness/readiness."""

    model_config = ConfigDict(extra="forbid")
    ready: bool
    reason_codes: list[Literal[
        "NO_PUBLISHED_RECIPES", "NO_SOLVER_ELIGIBLE_RECIPES",
        "BREAKFAST_COVERAGE_MISSING", "LUNCH_COVERAGE_MISSING", "DINNER_COVERAGE_MISSING",
        "NUTRITION_COVERAGE_INCOMPLETE",
    ]]
    published_recipe_count: int = Field(ge=0)
    solver_eligible_count: int = Field(ge=0)
    per_slot_count: dict[MealSlot, int]
    catalog_coverage_complete: bool
    # Display/menu availability is intentionally separate from strict Solver
    # business readiness. Slot coverage is advisory because MenuDraft may use
    # a labelled slot fallback.
    display_recipe_count: int = Field(default=0, ge=0)
    menu_draft_recipe_count: int = Field(default=0, ge=0)
    menu_draft_per_slot_count: dict[MealSlot, int] = Field(default_factory=dict)
    menu_draft_slot_coverage_complete: bool = False
