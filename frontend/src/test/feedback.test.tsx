import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { HistoryPage } from "../features/history/HistoryPage";
import type { HistoryCollection } from "../api/types";

const totals = { energy_kcal: "1600", protein_g: "90", carbohydrate_g: "150", fat_g: "50", prep_minutes: 50 };
const collection: HistoryCollection = { user_id: "u1", collection_version: 1, items: [{
  history_id: "h1", history_version: 1, user_id: "u1", original_run_id: "r1", original_plan_id: "p1", adopted_at: "2026-08-13T00:00:00Z", status: "ADOPTED",
  meals: [{ slot: "breakfast", recipe_id: "oats", recipe_version: "1", recipe_title: "燕麦", portion: "1.0", ingredients: [] }, { slot: "lunch", recipe_id: "rice", recipe_version: "1", recipe_title: "米饭", portion: "1.0", ingredients: [] }, { slot: "dinner", recipe_id: "tofu", recipe_version: "1", recipe_title: "豆腐", portion: "1.0", ingredients: [] }],
  verified_totals: totals, planning_constraints: { max_total_minutes: 60, energy_kcal_range: { min: "1500", max: "1700" }, protein_min_g: "90" }, profile_snapshot_id: "profile", validation_report: { valid: true, violations: [], warnings: [], totals, validator_version: "v", nutrition_data_version: "n" }, recipe_data_version: "recipes", nutrition_data_version: "n", numeric_policy_version: "numeric",
}] };

test("feedback is only submitted after the user explicitly saves it", async () => {
  const onFeedback = vi.fn().mockResolvedValue(undefined);
  render(<HistoryPage collection={collection} feedback={[]} loading={false} onRefresh={vi.fn()} onDelete={vi.fn()} onClear={vi.fn()} onFeedback={onFeedback} onAsk={vi.fn()} onReplan={vi.fn()}/>);
  fireEvent.click(screen.getByRole("button", { name: /查看详情与反馈/ }));
  expect(onFeedback).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("早餐完成结果"), { target: { value: "SKIPPED" } });
  fireEvent.click(screen.getByRole("button", { name: "保存明确反馈" }));
  await waitFor(() => expect(onFeedback).toHaveBeenCalledWith("h1", expect.objectContaining({ meals: expect.arrayContaining([expect.objectContaining({ slot: "breakfast", outcome: "SKIPPED" })]) })));
});
