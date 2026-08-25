import type { MealSlot, MenuDraft } from "../../api/types";
import { Icon } from "../../components/ui/Icon";

const slotLabels: Record<MealSlot, string> = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" };
const slotNumbers: Record<MealSlot, string> = { breakfast: "01", lunch: "02", dinner: "03" };

export function MenuDraftResult({ draft, onRebuild }: { draft: MenuDraft; onRebuild: () => void }) {
  return <section className="plan-result menu-draft-result card" aria-labelledby="menu-draft-title">
    <header className="result-header"><div><span className="verified-badge draft-badge"><Icon name="spark" size={16}/>普通菜单草稿 · 未进行营养验证</span><h2 id="menu-draft-title">今天的三餐草稿</h2><p>来自正式已发布菜谱，可用于查看食材和制作步骤；不代表营养或过敏安全验证通过。</p></div><div className="result-date"><small>草稿编号</small><strong>{draft.draft_id.slice(0, 12)}</strong></div></header>
    <div className="metric-strip draft-metrics"><div><Icon name="clock"/><span><b>{draft.total_prep_minutes} 分钟</b><small>预计总制作时间</small></span></div>{draft.nutrition_totals ? <><div><Icon name="spark"/><span><b>{Number(draft.nutrition_totals.energy_kcal).toFixed(0)}</b><small>已有数据总能量 · kcal</small></span></div><div><Icon name="nutrition"/><span><b>{Number(draft.nutrition_totals.protein_g).toFixed(1)} g</b><small>已有数据蛋白质</small></span></div></> : <div className="nutrition-unavailable"><Icon name="nutrition"/><span><b>暂无可靠营养总量</b><small>所选菜谱数据不完整，因此不估算、不显示数字</small></span></div>}</div>
    {draft.warnings.includes("TIME_PREFERENCE_EXCEEDED") && <div className="notice draft-time-notice" role="status">当前三餐预计用时超过你填写的时间偏好；普通菜单草稿只提示差异，不宣称时间约束已通过验证。</div>}
    <div className="draft-warning"><Icon name="shield" size={19}/><p><strong>使用边界</strong><span>如你声明了过敏原，组成未知的菜谱不会进入草稿；当前警告表示部分复合食材尚未完成过敏原组成审核。</span></p></div>
    <div className="meal-list">{draft.meals.map((meal) => <article className={`meal-card ${meal.slot}`} key={`${meal.slot}-${meal.recipe_id}`}><span className="meal-number">{slotNumbers[meal.slot]}</span><div className="meal-copy"><small>{slotLabels[meal.slot]}</small><h3>{meal.recipe_title}</h3><p>制作约 {meal.prep_minutes} 分钟</p>{meal.nutrition_per_serving ? <p className="meal-nutrition">每份 {Number(meal.nutrition_per_serving.energy_kcal).toFixed(0)} kcal · 蛋白质 {Number(meal.nutrition_per_serving.protein_g).toFixed(1)} g</p> : <p className="meal-nutrition unavailable">暂无可靠营养数据</p>}<ul className="meal-ingredients" aria-label={`${meal.recipe_title}食材用量`}>{meal.ingredients.map((ingredient) => <li key={`${ingredient.canonical_id}:${ingredient.display_quantity}`}><span>{ingredient.canonical_name}</span><strong>{ingredient.display_quantity}</strong></li>)}</ul><details className="draft-steps"><summary>查看制作步骤</summary><ol>{meal.cooking_steps.map((step) => <li key={step.step_number}>{step.instruction}</li>)}</ol></details><a className="source-link" href={meal.source.source_url} target="_blank" rel="noreferrer">查看来源</a></div></article>)}</div>
    <footer className="result-actions"><button className="button button-secondary" type="button" onClick={onRebuild}>调整后重新编排</button></footer>
  </section>;
}
