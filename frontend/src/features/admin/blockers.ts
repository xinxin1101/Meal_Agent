const blockerLabel: Record<string, string> = {
  INGREDIENT_QUANTITY_INCOMPLETE: "来源数量无法安全换算（需保留克数，或明确为适量/少许）",
  SERVINGS_MISSING: "缺少份数",
  TIME_MISSING: "缺少制作时间",
  MEAL_SLOTS_MISSING: "缺少适用餐次",
  ALLERGEN_COMPOSITION_INCOMPLETE: "过敏原组成未确认（可展示，禁止进入规划）",
  NUTRITION_COVERAGE_INCOMPLETE: "本地营养库未覆盖全部食材",
  NUTRITION_QUANTITY_INCOMPLETE: "营养计算所需克数不完整",
  NUTRITION_NOT_CALCULABLE: "当前无法形成可信的每份营养值",
  RECIPE_NOT_SOLVER_ELIGIBLE: "该菜谱尚未被标记为可用于规划",
};

export function explainRecipeBlocker(code: string): string {
  if (code.startsWith("NON_ACTIONABLE_STEP:")) return `制作步骤 ${code.split(":")[1]} 缺少可执行动作`;
  if (code.startsWith("IMAGE_DEPENDENT_STEP:")) return `制作步骤 ${code.split(":")[1]} 依赖图片，纯文字无法执行`;
  if (code.startsWith("MEDICAL_STEP_TEXT:")) return `制作步骤 ${code.split(":")[1]} 包含医疗化表述`;
  return blockerLabel[code] ?? code;
}
