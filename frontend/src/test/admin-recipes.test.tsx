import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import type { AdminRecipeCatalogItem, LlmReviewJob } from "../api/types";

const api = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

vi.mock("../api/client", () => ({ apiClient: api }));

import { AdminRecipesPage } from "../features/admin/AdminRecipesPage";

const duplicateIngredient = {
  canonical_id: "sea-salt",
  canonical_name: "海盐",
  display_quantity: "适量",
  quantity_kind: "QUALITATIVE" as const,
  quantity_origin: "SOURCE_EXPLICIT" as const,
  nutrition_calculation_role: "EXCLUDED_MINOR_INGREDIENT" as const,
  allergens: [],
  allergen_composition_known: true,
};

const recipe: AdminRecipeCatalogItem = {
  record_id: "review-duplicate-ingredients",
  recipe_id: null,
  title: "重复调味料测试",
  origin: "REVIEW_QUEUE",
  lifecycle_status: "PENDING",
  quality_status: "PUBLICATION_READY",
  solver_eligible: false,
  readable_eligible: true,
  menu_draft_eligible: true,
  processing_stage: "FINAL_VALIDATED",
  supported_slots: ["lunch"],
  servings: "2",
  prep_minutes: 20,
  ingredients: [duplicateIngredient, duplicateIngredient],
  cooking_steps: [{ step_number: 1, instruction: "拌匀。", ingredient_refs: [] }],
  source_id: "fixture:duplicate",
  source_url: "https://example.test/duplicate",
  license: "personal-study",
  data_version: "fixture-v1",
  blocking_reasons: [],
  solver_blocking_reasons: ["NUTRITION_QUANTITY_INCOMPLETE"],
};

afterEach(() => {
  vi.restoreAllMocks();
  api.get.mockReset();
});

test("admin catalog renders duplicate ingredient facts without duplicate React keys", async () => {
  api.get.mockImplementation((path: string) => Promise.resolve(path === "/v1/admin/recipes" ? [recipe] : []));
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

  render(<AdminRecipesPage />);
  await screen.findByText("重复调味料测试");
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/v1/admin/recipes"));

  expect(
    consoleError.mock.calls.some((call) => String(call[0]).includes("same key")),
  ).toBe(false);
  expect(screen.getAllByText("可阅读").length).toBeGreaterThan(0);
  expect(screen.getAllByText("可编排菜单").length).toBeGreaterThan(0);
});

test("admin catalog restores durable LLM task history after reopening", async () => {
  const failedJob: LlmReviewJob = {
    job_id: "llm-review-fixture", review_id: recipe.record_id, expected_review_version: 0,
    status: "FAILED", job_version: 2, created_by: "root", created_at: "2026-08-22T00:00:00Z",
    updated_at: "2026-08-22T00:00:01Z", attempts: 1, error_code: "LLM_PROVIDER_CONNECTION_FAILED",
  };
  api.get.mockImplementation((path: string) => Promise.resolve(path === "/v1/admin/recipes" ? [recipe] : [failedJob]));

  render(<AdminRecipesPage />);
  await screen.findByText(/LLM 任务中心/);
  expect(screen.getByText(/无法连接模型服务/)).toBeInTheDocument();
});
