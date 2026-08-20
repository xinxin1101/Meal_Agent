import { render, screen } from "@testing-library/react";
import { MenuDraftResult } from "../features/planning/MenuDraftResult";

test("menu draft hides missing nutrition totals and remains visibly unverified", () => {
  render(<MenuDraftResult draft={{
    draft_id: "draft-test", status: "UNVERIFIED_MENU", total_prep_minutes: 75,
    nutrition_totals: null, nutrition_complete: false,
    warnings: ["MENU_DRAFT_NOT_NUTRITION_VALIDATED"], policy_version: "menu-draft-v1",
    numeric_policy_version: "menu-draft-decimal-v1", nutrition_data_versions: [],
    meals: (["breakfast", "lunch", "dinner"] as const).map((slot, index) => ({
      slot, recipe_id: `recipe-${index}`, recipe_version: "1", recipe_title: `菜谱 ${index + 1}`,
      ingredients: [], cooking_steps: [], prep_minutes: 25, nutrition_per_serving: null,
      source: { source_id: `source-${index}`, source_url: "https://example.test/recipe", license: "internal", data_version: "v1" },
      warnings: ["NUTRITION_DATA_UNAVAILABLE"],
    })),
  }} onRebuild={() => undefined}/>);
  expect(screen.getByText(/未进行营养验证/)).toBeTruthy();
  expect(screen.getByText("暂无可靠营养总量")).toBeTruthy();
  expect(screen.queryByText(/总能量 · kcal/)).toBeNull();
});
