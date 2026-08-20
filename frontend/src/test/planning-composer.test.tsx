import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { PlanningComposer } from "../features/planning/PlanningComposer";

const suggestion = {
  policy_version: "healthy-adult-estimate-v1" as const,
  energy_kcal_range: { min: "1500", max: "1700" }, protein_min_g: "60.0",
  resting_energy_kcal: "1400", estimated_daily_energy_kcal: "1900",
  method: "mifflin-st-jeor-activity-goal" as const, requires_user_confirmation: true as const,
  warnings: ["仅供参考"], source_references: ["source"],
};

test("nutrition estimate never applies until the user explicitly confirms it", () => {
  const apply = vi.fn();
  render(<PlanningComposer planningMode="verified_nutrition" query="目标" constraints={{ minutes: "60", protein: "90", energyMin: "1500", energyMax: "1700" }} submitting={false} canSubmit readinessMessage={undefined} error={undefined} useHistory={false} historyCount={0} targetSuggestion={suggestion} targetSuggestionLoading={false} onPlanningModeChange={vi.fn()} onUseHistoryChange={vi.fn()} onQueryChange={vi.fn()} onConstraintChange={vi.fn()} onParse={vi.fn()} onSuggestTargets={vi.fn()} onApplySuggestion={apply} onSubmit={vi.fn()} onOpenProfile={vi.fn()}/>);
  expect(apply).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认并应用到本次目标" }));
  expect(apply).toHaveBeenCalledTimes(1);
});

test("menu draft mode does not present nutrition controls as validated targets", () => {
  render(<PlanningComposer planningMode="menu_draft" query="" constraints={{ minutes: "60", protein: "90", energyMin: "1500", energyMax: "1700" }} submitting={false} canSubmit readinessMessage={undefined} error={undefined} useHistory={false} historyCount={0} targetSuggestion={undefined} targetSuggestionLoading={false} onPlanningModeChange={vi.fn()} onUseHistoryChange={vi.fn()} onQueryChange={vi.fn()} onConstraintChange={vi.fn()} onParse={vi.fn()} onSuggestTargets={vi.fn()} onApplySuggestion={vi.fn()} onSubmit={vi.fn()} onOpenProfile={vi.fn()}/>);
  expect(screen.queryByText("蛋白质")).toBeNull();
  expect(screen.queryByText("能量范围")).toBeNull();
  expect(screen.getByText(/不会验证能量或蛋白质/)).toBeTruthy();
  expect(screen.getByRole("button", { name: /生成普通菜单草稿/ })).toBeTruthy();
});
