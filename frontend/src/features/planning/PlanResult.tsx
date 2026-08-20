import type { MealPlan, MealSlot, PlanTotals } from "../../api/types";
import { Icon } from "../../components/ui/Icon";

const recipeNames: Record<string, string> = {
  "recipe-oat-egg-v1": "燕麦鸡蛋碗",
  "recipe-beef-vegetables-v1": "牛肉时蔬",
  "recipe-chicken-rice-v1": "鸡胸肉糙米饭",
  "recipe-tofu-noodles-v1": "豆腐蔬菜面",
  "recipe-yogurt-fruit-v1": "酸奶水果杯",
};
export const recipeDisplayName = (recipeId: string) => recipeNames[recipeId] ?? "本地验证菜谱";
const slotLabels: Record<MealSlot, string> = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" };
const slotNumbers: Record<MealSlot, string> = { breakfast: "01", lunch: "02", dinner: "03" };
const fmt = (value: string | number, digits = 0) => Number(value).toFixed(digits);

function GoalBar({ label, value, minimum, maximum, unit }: { label: string; value: number; minimum: number; maximum: number; unit: string }) {
  const percentage = Math.max(4, Math.min(100, value / maximum * 100));
  return <div className="goal-row"><div><strong>{label}</strong><span>{fmt(value)} {unit} / {minimum === maximum ? `≥ ${fmt(minimum)}` : `${fmt(minimum)}–${fmt(maximum)}`}</span></div><div className="goal-track" aria-hidden="true"><i style={{ width: `${percentage}%` }} /></div></div>;
}

export function PlanResult({ plan, explanation, energyMin, energyMax, proteinMin, adopted, adopting, onAdopt, onAsk, onReplan }: { plan: MealPlan; explanation?: string | null; energyMin: number; energyMax: number; proteinMin: number; adopted: boolean; adopting: boolean; onAdopt: () => void; onAsk: () => void; onReplan: () => void }) {
  const totals: PlanTotals = plan.totals;
  return <section className="plan-result card" aria-labelledby="plan-result-title">
    <header className="result-header"><div><span className="verified-badge"><Icon name="shield" size={16} />已通过确定性复核</span><h2 id="plan-result-title">你的今日三餐</h2><p>安全约束先过滤，营养和时间已重新计算。</p></div><div className="result-date"><small>方案编号</small><strong>{plan.plan_id.slice(0, 12)}</strong></div></header>
    <div className="metric-strip">
      <div><Icon name="spark"/><span><b>{fmt(totals.energy_kcal)}</b><small>总能量 · kcal</small></span></div>
      <div><Icon name="nutrition"/><span><b>{fmt(totals.protein_g, 1)} g</b><small>蛋白质</small></span></div>
      <div><Icon name="clock"/><span><b>{fmt(totals.prep_minutes)} 分钟</b><small>总准备时间</small></span></div>
    </div>
    <div className="goal-panel"><GoalBar label="能量目标" value={Number(totals.energy_kcal)} minimum={energyMin} maximum={energyMax} unit="kcal"/><GoalBar label="蛋白质目标" value={Number(totals.protein_g)} minimum={proteinMin} maximum={Math.max(proteinMin, Number(totals.protein_g))} unit="g"/><p>碳水 {fmt(totals.carbohydrate_g, 1)} g · 脂肪 {fmt(totals.fat_g, 1)} g</p></div>
      <div className="meal-list">{plan.selections.map((meal) => <article className={`meal-card ${meal.slot}`} key={`${meal.slot}-${meal.recipe_id}`}><span className="meal-number">{slotNumbers[meal.slot]}</span><div className="meal-copy"><small>{slotLabels[meal.slot]}</small><h3>{meal.recipe_title ?? recipeDisplayName(meal.recipe_id)}</h3><p>建议份量 {meal.portion} 份</p>{meal.ingredients.length > 0 && <ul className="meal-ingredients" aria-label={`${meal.recipe_title ?? recipeDisplayName(meal.recipe_id)}食材用量`}>{meal.ingredients.map((ingredient) => <li key={ingredient.canonical_id}><span>{ingredient.canonical_name}</span><strong>{ingredient.display_quantity}</strong></li>)}</ul>}</div><span className="meal-check"><Icon name="check" size={18}/></span></article>)}</div>
    {explanation && <section className="plan-explanation"><div className="explanation-icon"><Icon name="spark" /></div><div><h3>为什么这样安排？</h3><p>{explanation}</p></div></section>}
    <footer className="result-actions"><button className="button button-secondary" type="button" onClick={onReplan}>调整条件重新规划</button><button className="button button-secondary" type="button" onClick={onAsk}><Icon name="chat" size={18}/>询问助手</button><button className="button button-primary" type="button" onClick={onAdopt} disabled={adopting || adopted}><Icon name="check" size={18}/>{adopting ? "正在采用…" : adopted ? "已加入历史" : "采用此计划"}</button></footer>
  </section>;
}
