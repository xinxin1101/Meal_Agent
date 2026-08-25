import type { FormEvent } from "react";
import { Icon } from "../../components/ui/Icon";
import type { NutritionTargetSuggestion, PlanningMode } from "../../api/types";

export type PlanningConstraints = { minutes: string; protein: string; energyMin: string; energyMax: string };

export function PlanningComposer({ planningMode, query, constraints, parseNotice, submitting, canSubmit, readinessMessage, error, useHistory, historyCount, targetSuggestion, targetSuggestionLoading, onPlanningModeChange, onUseHistoryChange, onQueryChange, onConstraintChange, onParse, onSuggestTargets, onApplySuggestion, onSubmit, onOpenProfile }: {
  planningMode: PlanningMode;
  query: string;
  constraints: PlanningConstraints;
  parseNotice?: string;
  submitting: boolean;
  canSubmit: boolean;
  readinessMessage?: string;
  error?: string;
  useHistory: boolean;
  historyCount: number;
  targetSuggestion?: NutritionTargetSuggestion;
  targetSuggestionLoading: boolean;
  onPlanningModeChange: (value: PlanningMode) => void;
  onUseHistoryChange: (value: boolean) => void;
  onQueryChange: (value: string) => void;
  onConstraintChange: (key: keyof PlanningConstraints, value: string) => void;
  onParse: () => void;
  onSuggestTargets: () => void;
  onApplySuggestion: () => void;
  onSubmit: (event: FormEvent) => void;
  onOpenProfile: () => void;
}) {
  return <form className="planning-composer card" onSubmit={onSubmit} aria-label="创建一日三餐计划">
    <div className="composer-heading"><div><p className="eyebrow">描述本次目标</p><h2>今天想怎么吃？</h2><p>可以直接描述时间和营养目标，我们会提取后让你确认。</p></div><span className="spark-mark"><Icon name="spark" /></span></div>
    <fieldset className="planning-mode-selector"><legend>规划方式</legend><div>
      <label className={planningMode === "verified_nutrition" ? "selected" : ""}><input type="radio" name="planning-mode" value="verified_nutrition" checked={planningMode === "verified_nutrition"} onChange={() => onPlanningModeChange("verified_nutrition")}/><span><strong>营养验证计划</strong><small>只使用 Solver 就绪菜谱，验证能量、蛋白质和安全约束</small></span></label>
      <label className={planningMode === "menu_draft" ? "selected" : ""}><input type="radio" name="planning-mode" value="menu_draft" checked={planningMode === "menu_draft"} onChange={() => onPlanningModeChange("menu_draft")}/><span><strong>普通菜单草稿</strong><small>使用已发布菜谱编排三餐，不声明营养验证通过</small></span></label>
    </div></fieldset>
    {planningMode === "verified_nutrition" && <><label className="query-field"><span className="sr-only">自然语言需求</span><textarea value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="例如：60 分钟内完成，想吃高蛋白的一日三餐" /></label>
    <div className="parse-row"><button className="button button-secondary" type="button" onClick={onParse} disabled={submitting}><Icon name="spark" size={17} />解析需求</button><small>{parseNotice ?? "支持时间、蛋白质和能量范围"}</small></div></>}
    <fieldset className="constraint-fieldset"><legend>本次目标</legend><div className="constraint-grid">
      <label><span><Icon name="clock" size={16} />总时间</span><div><input value={constraints.minutes} onChange={(event) => onConstraintChange("minutes", event.target.value)} inputMode="numeric" /><em>分钟</em></div></label>
      {planningMode === "verified_nutrition" && <><label><span><Icon name="nutrition" size={16} />蛋白质</span><div><input value={constraints.protein} onChange={(event) => onConstraintChange("protein", event.target.value)} inputMode="decimal" /><em>g 起</em></div></label>
      <label className="energy-range"><span><Icon name="spark" size={16} />能量范围</span><div><input value={constraints.energyMin} onChange={(event) => onConstraintChange("energyMin", event.target.value)} inputMode="numeric" aria-label="能量下限" /><i>–</i><input value={constraints.energyMax} onChange={(event) => onConstraintChange("energyMax", event.target.value)} inputMode="numeric" aria-label="能量上限" /><em>kcal</em></div></label></>}
    </div></fieldset>
    {planningMode === "verified_nutrition" && <section className="nutrition-suggestion" aria-live="polite">
      <div><strong>档案目标估算</strong><p>根据已保存的健康成年人档案生成版本化建议。建议不会自动覆盖本次目标。</p></div>
      <button className="button button-secondary" type="button" onClick={onSuggestTargets} disabled={targetSuggestionLoading || submitting}>{targetSuggestionLoading ? "估算中…" : "生成估算建议"}</button>
      {targetSuggestion && <div className="nutrition-suggestion-result"><p><b>{targetSuggestion.energy_kcal_range.min}–{targetSuggestion.energy_kcal_range.max} kcal</b> · 蛋白质至少 <b>{targetSuggestion.protein_min_g} g</b></p><small>策略 {targetSuggestion.policy_version}；静息能量估算 {targetSuggestion.resting_energy_kcal} kcal。仅供健康成年人规划参考。</small><button className="button button-primary" type="button" onClick={onApplySuggestion}>确认并应用到本次目标</button></div>}
    </section>}
    {planningMode === "menu_draft" && <div className="draft-boundary-note" role="note"><Icon name="shield" size={18}/><span>普通菜单草稿会按已声明的过敏原和忌口进行保守筛选，但不会验证能量或蛋白质，也不能替代医疗或完整过敏原安全结论。有营养数据时才会显示；资料不完整时不会生成估算数字。</span></div>}
    {planningMode === "verified_nutrition" && <label className="history-consent"><input type="checkbox" checked={useHistory} onChange={(event) => onUseHistoryChange(event.target.checked)} disabled={!historyCount}/><span>使用最近的已采用计划优化菜品多样性{historyCount ? `（当前 ${historyCount} 条）` : "（暂无历史）"}。历史只影响低优先级软目标，不会放宽安全、营养或时间约束。</span></label>}
    {readinessMessage && <div className="notice" role="status">{readinessMessage}</div>}
    {error && <div className="notice error" role="alert">{error}</div>}
    <div className="composer-actions"><button className="button button-ghost" type="button" onClick={onOpenProfile}><Icon name="profile" size={18} />查看规划档案</button><button className="button button-primary generate-button" disabled={submitting || !canSubmit}>{submitting ? "正在提交…" : planningMode === "menu_draft" ? "生成普通菜单草稿" : "生成营养验证计划"}<Icon name="arrow" size={18} /></button></div>
  </form>;
}
