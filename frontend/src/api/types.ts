export type ActivityLevel = "sedentary" | "light" | "moderate" | "active" | "very_active";
export type NutritionGoal = "lose" | "maintain" | "gain";
export type NutritionParameterSex = "female" | "male" | "unspecified";
export type MealSlot = "breakfast" | "lunch" | "dinner";
export type IngredientQuantityKind = "MEASURED" | "QUALITATIVE" | "UNSPECIFIED";
export type QuantityOrigin = "SOURCE_EXPLICIT" | "DISPLAY_FALLBACK" | "REVIEWER_CONFIRMED";

export interface UserProfile {
  profile_snapshot_id: string;
  adult_confirmed: true;
  age_years: number;
  nutrition_parameter_sex: NutritionParameterSex;
  height_cm: string;
  weight_kg: string;
  activity_level: ActivityLevel;
  goal: NutritionGoal;
  allergens: string[];
  avoidances: string[];
}

export interface AccountSummary { user_id: string; email: string; display_name: string; created_at: string; role: "USER" | "ADMIN" }
export interface AuthSession { access_token: string; token_type: "bearer"; expires_in: number; account: AccountSummary }
export interface AccountProfile { profile_version: number; profile?: UserProfile | null; updated_at?: string | null }
export interface NutritionTargetSuggestion {
  policy_version: "healthy-adult-estimate-v1";
  energy_kcal_range: { min: string; max: string };
  protein_min_g: string;
  resting_energy_kcal: string;
  estimated_daily_energy_kcal: string;
  method: "mifflin-st-jeor-activity-goal";
  requires_user_confirmation: true;
  warnings: string[];
  source_references: string[];
}

export interface MealPlanningRequest {
  request_id: string;
  meal_slots?: MealSlot[];
  persons?: 1;
  max_total_minutes: number;
  energy_kcal_range: { min: string; max: string };
  protein_min_g: string;
  numeric_policy_version: string;
}

export interface AgentPlanningCommand {
  run_id: string;
  query: string;
  profile: UserProfile;
  request: MealPlanningRequest;
  retrieval_top_k_per_slot?: number;
  user_id?: string;
  use_history?: boolean;
}

export interface PlanTotals {
  energy_kcal: string;
  protein_g: string;
  carbohydrate_g: string;
  fat_g: string;
  prep_minutes: number;
}

export interface NegotiationOption { option_id: string; field: "max_total_minutes" | "protein_min_g" | "energy_kcal_range"; proposed_value: string; impact: string }
export interface NegotiationProposal { proposal_id: string; reason_code: string; options: NegotiationOption[]; explanation: string }
export interface RecipeIngredient { canonical_id: string; canonical_name: string; display_quantity: string; quantity_kind: IngredientQuantityKind; quantity_origin?: QuantityOrigin; amount_g?: string | null; nutrition_calculation_role: "INCLUDED" | "EXCLUDED_MINOR_INGREDIENT"; allergens: string[]; allergen_composition_known: boolean }
export interface CookingStep { step_number: number; instruction: string; duration_minutes?: number | null; ingredient_refs: string[] }
export interface Recipe {
  recipe_id: string; version: string; title: string; supported_slots: MealSlot[]; servings: string; prep_minutes: number;
  nutrition_per_serving?: { energy_kcal: string; protein_g: string; carbohydrate_g: string; fat_g: string } | null;
  nutrition_basis?: "CALCULATED_FROM_INGREDIENTS" | "SOURCE_DECLARED" | "REVIEWED_STANDARD_PORTION" | null;
  solver_eligible: boolean; ingredients: RecipeIngredient[]; cooking_steps: CookingStep[];
  source: { source_id: string; source_url: string; license: string; data_version: string };
  numeric_policy_version: string; nutrition_data_version?: string | null;
}
export type PlanningMode = "verified_nutrition" | "menu_draft";
export interface MenuDraftCommand {
  draft_id: string; profile: UserProfile; max_total_minutes?: number | null; acknowledge_unverified: true;
}
export interface MenuDraftMeal {
  slot: MealSlot; recipe_id: string; recipe_version: string; recipe_title: string;
  ingredients: RecipeIngredient[]; cooking_steps: CookingStep[]; prep_minutes: number;
  nutrition_per_serving?: Recipe["nutrition_per_serving"]; source: Recipe["source"]; warnings: string[];
}
export interface MenuDraft {
  draft_id: string; status: "UNVERIFIED_MENU"; meals: MenuDraftMeal[]; total_prep_minutes: number;
  nutrition_totals?: Recipe["nutrition_per_serving"]; nutrition_complete: boolean; warnings: string[]; policy_version: "menu-draft-v1";
  numeric_policy_version: "menu-draft-decimal-v1"; nutrition_data_versions: string[];
}
export interface MenuDraftFailure {
  status: "FAILED"; reason_code: "NO_PUBLISHED_RECIPES" | "INSUFFICIENT_SAFE_DISPLAY_RECIPES";
  message: string; excluded_recipe_ids: string[]; exclusion_reasons: Record<string, string[]>;
}
export interface AdminRecipeCatalogItem {
  record_id: string; recipe_id?: string | null; title: string; origin: "ACTIVE_CATALOG" | "REVIEW_QUEUE";
  lifecycle_status: "ACTIVE" | "PENDING" | "APPROVED" | "REJECTED" | "PUBLISHED" | "REVOKED";
  quality_status?: "BLOCKED" | "PUBLICATION_READY" | "SOLVER_READY" | null; solver_eligible: boolean;
  processing_stage?: "INITIAL_VALIDATED" | "LLM_FAILED" | "FINAL_VALIDATION_BLOCKED" | "FINAL_VALIDATED" | null;
  supported_slots: MealSlot[]; servings?: string | null; prep_minutes?: number | null; ingredients: RecipeIngredient[];
  cooking_steps: CookingStep[]; source_id: string; source_url: string; license: string; data_version: string; blocking_reasons: string[]; solver_blocking_reasons: string[];
}
export interface AdminCanonicalIngredient { canonical_id: string; canonical_name: string; allergens: string[]; allergen_composition_known: boolean }
export interface AdminRawIngredient { group: "main" | "secondary" | "seasoning" | "other"; raw_name: string; raw_amount: string; raw_text: string }
export interface AdminIngredientOverride {
  raw_name: string; canonical_id: string; canonical_name: string; amount?: string | null; unit?: string | null;
  qualitative_label?: "适量" | "少许" | null; quantity_origin?: QuantityOrigin; nutrition_calculation_role: "INCLUDED" | "EXCLUDED_MINOR_INGREDIENT";
  allergens: string[]; allergen_composition_known: boolean;
}
export interface AdminAuthorizationEvidence {
  evidence_id: string; evidence_type: "WRITTEN_PERMISSION" | "OPEN_LICENSE" | "FIRST_PARTY" | "PUBLIC_DOMAIN" | "PERSONAL_STUDY_DECLARATION";
  source_id: string; source_content_hash: string; license_identifier: string; proof_reference: string; granted_by: string;
  granted_at: string; expires_at?: string | null; scope: string[]; notes: string;
}
export interface AdminRecipeReview {
  review_id: string; review_version: number; status: "PENDING" | "APPROVED" | "REJECTED" | "PUBLISHED" | "REVOKED";
  processing_stage: "INITIAL_VALIDATED" | "LLM_FAILED" | "FINAL_VALIDATION_BLOCKED" | "FINAL_VALIDATED"; processing_errors: string[]; source_use_scope: "PERSONAL_STUDY_INTERNAL";
  raw: { source_id: string; source_url: string; title: string; ingredients: AdminRawIngredient[]; cooking_steps: CookingStep[]; warnings: string[]; copyright_notice?: string | null };
  curation: { title_override?: string | null; servings?: string | null; supported_slots: MealSlot[]; prep_minutes?: number | null; ingredient_overrides: AdminIngredientOverride[]; step_overrides: Record<string, string>; excluded_step_numbers: number[] };
  draft: { title: string; ingredients: Array<{ raw_name: string; canonical_id?: string | null; canonical_name?: string | null; display_quantity: string; quantity_kind?: IngredientQuantityKind | null; quantity_origin?: QuantityOrigin }>; cooking_steps: CookingStep[]; solver_eligible: boolean };
  quality_report: { status: "BLOCKED" | "PUBLICATION_READY" | "SOLVER_READY"; blocking_reasons: string[]; solver_blocking_reasons: string[]; warnings: string[]; unspecified_quantity_names?: string[] };
  llm_assistance?: { model: string; prompt_version: string; accepted_suggestions: string[]; rejected_suggestions: string[]; requires_human_review: true } | null;
  authorization_evidence: AdminAuthorizationEvidence[];
}
export interface AdminReviewDetail { review: AdminRecipeReview; canonical_ingredients: AdminCanonicalIngredient[]; can_approve: boolean; can_publish: boolean }
export interface AdminBatchPublishResponse { published_count: number; failed_count: number; results: Array<{ review_id: string; status: "PUBLISHED" | "FAILED"; review_version?: number | null; reason_code?: string | null }> }
export interface ProductReadiness {
  ready: boolean;
  reason_codes: Array<"NO_PUBLISHED_RECIPES" | "NO_SOLVER_ELIGIBLE_RECIPES" | "BREAKFAST_COVERAGE_MISSING" | "LUNCH_COVERAGE_MISSING" | "DINNER_COVERAGE_MISSING" | "NUTRITION_COVERAGE_INCOMPLETE">;
  published_recipe_count: number; solver_eligible_count: number;
  per_slot_count: Record<MealSlot, number>; catalog_coverage_complete: boolean;
}
export interface SourcePolicySummary {
  policy_id: string; policy_version: string; enabled: boolean; purpose: "PERSONAL_STUDY";
  max_records_per_run: number; max_pages_per_category: number; minimum_delay_seconds: string;
  category_count: number; images: false; comments: false; auto_publish: false;
}
export interface RecipeAcquisitionPreview { policy: SourcePolicySummary; requested_max_records: number; executable: boolean; stops: string[]; destination: "RAW_QUARANTINE_AND_REVIEW_QUEUE" }
export interface RecipeAcquisitionJob {
  job_id: string; policy_id: string; max_records: number;
  status: "QUEUED" | "RUNNING" | "REVIEW_READY" | "PARTIAL" | "FAILED" | "CANCELLED";
  job_version: number; created_by: string; created_at: string; updated_at: string;
  started_at?: string | null; completed_at?: string | null; attempts: number;
  lease_expires_at?: string | null; result?: Record<string, unknown> | null; error_code?: string | null;
}
export interface RecipeAcquisitionEvent { event_id: number; job_id: string; event_type: string; job_version: number; created_at: string; payload: Record<string, unknown> }
export interface PlanSelection { slot: MealSlot; recipe_id: string; recipe_version: string; portion: "0.5" | "1.0" | "1.5" | "2.0"; recipe_title?: string | null; ingredients: RecipeIngredient[] }
export interface CandidateCoverageReport { status: "EMPTY_RETRIEVAL" | "INSUFFICIENT_COVERAGE" | "READY"; per_slot_count: Record<MealSlot, number>; excluded_recipe_ids: string[]; exclusion_reasons: Record<string, string[]> }
export interface ValidationReport { valid: boolean; violations: string[]; warnings: string[]; totals: PlanTotals | null; validator_version: string; nutrition_data_version: string | null }
export interface PlanningFailure { status: "EMPTY_RETRIEVAL" | "INSUFFICIENT_COVERAGE" | "READY" | "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN"; reason_code: string; message: string; coverage_report: CandidateCoverageReport }
export interface MealPlan { plan_id: string; selections: PlanSelection[]; totals: PlanTotals; solver_status: "OPTIMAL" | "FEASIBLE" | "INFEASIBLE" | "UNKNOWN"; validation_report: ValidationReport | null; numeric_policy_version: string }
export interface AgentTraceEvent { node: "analyze" | "retrieve" | "solve" | "validate" | "repair" | "finalize"; outcome: string; safe_payload: Record<string, string | number | string[]> }
export interface AgentRunResult { run_id: string; status: "COMPLETED" | "FAILED"; parsed_constraints: Record<string, string>; attempted_strategies: string[]; trace: AgentTraceEvent[]; result: MealPlan | PlanningFailure; explanation: string | null }
export interface DurableRunSnapshot { run_id: string; status: "QUEUED" | "RUNNING" | "PAUSED" | "COMPLETED" | "FAILED" | "CANCELLED"; run_version: number; result?: AgentRunResult | null; proposal?: NegotiationProposal | null }
export interface RunDecisionCommand { option_id: string; expected_run_version: number }
export interface RunEvent { event_id: number; event_type: "queued" | "started" | "resumed" | "analyze" | "retrieve" | "solve" | "repair" | "validate" | "finalize" | "interrupt" | "completed" | "failed" | "cancelled"; run_version: number; payload: { status?: string; outcome?: string; safe_payload?: Record<string, unknown> } }

export interface PreferenceMemoryItem { category: "food_preference" | "avoidance" | "cooking_style"; value: string }
export interface PreferenceMemory { user_id: string; items: PreferenceMemoryItem[] }
export interface ChatPlanContext { plan_id: string; meals: string[]; totals: PlanTotals }
export interface HistoryPlanReference { history_id: string; original_plan_id: string; adopted_at: string }
export interface AdoptedMeal { slot: MealSlot; recipe_id: string; recipe_version: string; recipe_title: string; portion: PlanSelection["portion"]; ingredients: RecipeIngredient[] }
export interface PlanningConstraintsSnapshot { max_total_minutes: number; energy_kcal_range: { min: string; max: string }; protein_min_g: string }
export interface AdoptedMealPlan {
  history_id: string; history_version: number; user_id: string; original_run_id: string; original_plan_id: string; adopted_at: string;
  meals: AdoptedMeal[]; verified_totals: PlanTotals; planning_constraints: PlanningConstraintsSnapshot; profile_snapshot_id: string;
  validation_report: ValidationReport; recipe_data_version: string; nutrition_data_version?: string | null; numeric_policy_version: string; status: "ADOPTED";
}
export interface HistoryCollection { user_id: string; collection_version: number; items: AdoptedMealPlan[] }
export type MealFeedbackOutcome = "COMPLETED" | "SKIPPED" | "REPLACED";
export interface MealFeedback { slot: MealSlot; outcome: MealFeedbackOutcome; rating?: number | null; replacement_recipe_id?: string | null; note?: string | null }
export interface RecipeFeedbackDirective { recipe_id: string; action: "REUSE" | "AVOID" }
export interface SavePlanFeedbackCommand { expected_feedback_version: number; meals: MealFeedback[]; directives: RecipeFeedbackDirective[] }
export interface PlanFeedback { feedback_id: string; user_id: string; history_id: string; feedback_version: number; submitted_at: string; meals: MealFeedback[]; directives: RecipeFeedbackDirective[]; feedback_policy_version: "explicit-feedback-v1" }
export interface FeedbackCollection { user_id: string; items: PlanFeedback[] }
export interface ChatRequest {
  user_id?: string; message: string; plan_context?: ChatPlanContext; use_current_plan?: boolean; use_history?: boolean; use_preferences?: boolean;
  conversation_id?: string; expected_conversation_version?: number; history_plan_ids?: string[];
}
export interface ChatResponse {
  reply: string; memories_used: PreferenceMemoryItem[]; response_source: "siliconflow" | "deterministic_fallback";
  current_plan_used?: string | null; history_plans_used: HistoryPlanReference[]; conversation_id?: string | null;
  conversation_version?: number | null; user_message_id?: string | null; assistant_message_id?: string | null;
}
export interface ConversationSummary {
  conversation_id: string; user_id: string; title: string; conversation_version: number; created_at: string; updated_at: string; message_count: number;
}
export interface ConversationMessage {
  message_id: string; conversation_id: string; role: "user" | "assistant"; content: string; created_at: string;
  response_source?: ChatResponse["response_source"] | null; memories_used: PreferenceMemoryItem[];
  current_plan_used?: string | null; history_plans_used: HistoryPlanReference[];
}
export interface ConversationDetail { summary: ConversationSummary; messages: ConversationMessage[] }

export function isMealPlan(result: MealPlan | PlanningFailure): result is MealPlan { return "selections" in result; }
export function isMenuDraft(result: MenuDraft | MenuDraftFailure): result is MenuDraft { return result.status === "UNVERIFIED_MENU"; }
