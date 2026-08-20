import { useEffect, useMemo, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import type { AdminBatchPublishResponse, AdminReviewDetail, MealSlot } from "../../api/types";
import { explainRecipeBlocker } from "./blockers";

type IngredientEdit = { raw_name: string; canonical_id: string; amount: string; unit: string; qualitative_label: string; nutrition_calculation_role: "INCLUDED" | "EXCLUDED_MINOR_INGREDIENT" };
const slotLabels: Record<MealSlot, string> = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" };
const stageLabels = { INITIAL_VALIDATED: "初检完成，等待 LLM", LLM_FAILED: "LLM 处理失败", FINAL_VALIDATION_BLOCKED: "最终校验阻塞", FINAL_VALIDATED: "最终校验通过" } as const;

export function AdminReviewWorkbench({ reviewId, onClose, onChanged }: { reviewId: string; onClose: () => void; onChanged: () => void }) {
  const [detail, setDetail] = useState<AdminReviewDetail>();
  const [title, setTitle] = useState("");
  const [ingredients, setIngredients] = useState<IngredientEdit[]>([]);
  const [servings, setServings] = useState("");
  const [prepMinutes, setPrepMinutes] = useState("");
  const [slots, setSlots] = useState<MealSlot[]>([]);
  const [steps, setSteps] = useState<Record<number, string>>({});
  const [pending, setPending] = useState<string>();
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const [datasetVersion, setDatasetVersion] = useState(`mealpilot-${new Date().toISOString().slice(0, 10)}`);

  function hydrate(value: AdminReviewDetail) {
    setDetail(value); setTitle(value.review.curation.title_override ?? value.review.draft.title);
    const overrideByName = new Map(value.review.curation.ingredient_overrides.map((item) => [item.raw_name, item]));
    const draftByName = new Map(value.review.draft.ingredients.map((item) => [item.raw_name, item]));
    setIngredients(value.review.raw.ingredients.map((raw) => {
      const override = overrideByName.get(raw.raw_name); const draft = draftByName.get(raw.raw_name);
      return { raw_name: raw.raw_name, canonical_id: override?.canonical_id ?? draft?.canonical_id ?? "", amount: override?.amount ?? "", unit: override?.unit ?? "g", qualitative_label: override?.qualitative_label ?? (["适量", "少许"].includes(raw.raw_amount.trim()) ? raw.raw_amount.trim() : ""), nutrition_calculation_role: override?.nutrition_calculation_role ?? "INCLUDED" };
    }));
    setServings(value.review.curation.servings ?? "");
    setPrepMinutes(value.review.curation.prep_minutes == null ? "" : String(value.review.curation.prep_minutes));
    setSlots(value.review.curation.supported_slots);
    setSteps(Object.fromEntries(value.review.raw.cooking_steps.map((step) => [step.step_number, value.review.curation.step_overrides[String(step.step_number)] ?? step.instruction])));
  }

  async function load() {
    setError(undefined);
    try { hydrate(await apiClient.get<AdminReviewDetail>(`/v1/admin/reviews/${reviewId}`)); }
    catch (requestError) { setError(toUserMessage(requestError)); }
  }
  useEffect(() => { void load(); }, [reviewId]);

  async function mutate(label: string, path: string, body: unknown, method: "POST" | "PUT" = "POST") {
    setPending(label); setError(undefined); setNotice(undefined);
    try {
      const value = method === "PUT" ? await apiClient.put<AdminReviewDetail>(path, body) : await apiClient.post<AdminReviewDetail>(path, body, 105_000);
      hydrate(value); onChanged();
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setPending(undefined); }
  }

  function revalidate() {
    if (!detail) return;
    const stepOverrides = Object.fromEntries(detail.review.raw.cooking_steps.filter((step) => steps[step.step_number]?.trim() !== step.instruction).map((step) => [step.step_number, steps[step.step_number].trim()]));
    void mutate("最终复验", `/v1/admin/reviews/${reviewId}/curation`, {
      expected_review_version: detail.review.review_version, title: title.trim() || null,
      servings: servings || null, supported_slots: slots, prep_minutes: prepMinutes === "" ? null : Number(prepMinutes),
      ingredients: ingredients.map((item) => ({ raw_name: item.raw_name, canonical_id: item.canonical_id || null, amount: item.amount || null, unit: item.amount ? item.unit : null, qualitative_label: item.amount ? null : item.qualitative_label || null, nutrition_calculation_role: item.nutrition_calculation_role })),
      step_overrides: stepOverrides, excluded_step_numbers: [],
    }, "PUT");
  }

  async function approveAndPublish() {
    if (!detail) return;
    setPending("通过并发布"); setError(undefined); setNotice(undefined);
    try {
      const response = await apiClient.post<AdminBatchPublishResponse>("/v1/admin/reviews/batch-publish", {
        items: [{ review_id: reviewId, expected_review_version: detail.review.review_version }],
        dataset_version: datasetVersion, confirm_internal_personal_study: true,
      });
      if (response.failed_count) throw new Error(response.results[0]?.reason_code ?? "发布失败");
      setNotice("已通过审核并发布到正式菜谱库。"); await load(); onChanged();
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setPending(undefined); }
  }

  const optionById = useMemo(() => new Map(detail?.canonical_ingredients.map((item) => [item.canonical_id, item]) ?? []), [detail]);
  if (!detail) return <div className="review-workbench card"><button className="button button-secondary" type="button" onClick={onClose}>返回目录</button>{error ? <div className="notice error">{error}</div> : <p role="status">正在读取审核记录…</p>}</div>;
  const review = detail.review;
  return <section className="review-workbench">
    <header className="review-workbench-header"><div><button className="text-button" type="button" onClick={onClose}>← 返回菜谱目录</button><p className="eyebrow">审核记录 · v{review.review_version}</p><h1>{review.draft.title}</h1><p>{review.raw.source_id} · {review.status}</p></div><div className="review-header-actions"><button className="button button-secondary" type="button" disabled={Boolean(pending) || review.status !== "PENDING"} onClick={() => void mutate("LLM 结构化", `/v1/admin/reviews/${reviewId}/assist`, { expected_review_version: review.review_version })}>{pending === "LLM 结构化" ? "模型处理中…" : "重新执行 LLM 结构化"}</button><button className="button button-primary" type="button" disabled={Boolean(pending) || review.status !== "PENDING"} onClick={revalidate}>{pending === "最终复验" ? "复验中…" : "保存并最终复验"}</button></div></header>
    {error && <div className="notice error" role="alert">{error}</div>}{notice && <div className="notice success" role="status">{notice}</div>}
    <div className="review-boundary-note">处理顺序：确定性初检 → LLM 受约束结构化 → Pydantic 与业务规则最终复验 → 管理员通过并发布。来源文本始终是不可信数据，LLM 不能填写营养、过敏原、权限或发布状态。</div>
    <section className="review-status-grid">
      <article className="card"><span>自动处理阶段</span><strong>{stageLabels[review.processing_stage]}</strong><ul>{review.processing_errors.length ? review.processing_errors.map((item) => <li key={item}>{item}</li>) : <li>暂无处理错误</li>}</ul></article>
      <article className={`card quality-${review.quality_report.status.toLowerCase()}`}><span>最终数据门禁</span><strong>{review.quality_report.status}</strong><ul>{review.quality_report.blocking_reasons.length ? review.quality_report.blocking_reasons.map((item) => <li key={item}>{explainRecipeBlocker(item)}</li>) : <li>页面展示结构通过</li>}</ul></article>
      <article className="card"><span>规划 Solver 门禁（不影响页面发布）</span><strong>{review.draft.solver_eligible ? "SOLVER_READY" : "仅可展示"}</strong><ul>{review.quality_report.solver_blocking_reasons.length ? review.quality_report.solver_blocking_reasons.map((item) => <li key={item}>{explainRecipeBlocker(item)}</li>) : <li>可以进入规划求解器</li>}</ul></article>
    </section>
    <div className="review-compare-grid">
      <section className="card source-panel"><header><div><p className="eyebrow">来源数据</p><h2>原始提取结果</h2></div><a href={review.raw.source_url} target="_blank" rel="noreferrer">查看来源</a></header><p className="source-trace">保留来源 URL、抓取时间和内容哈希，仅用于内部个人学习，不表示公开转载授权。</p><h3>原始食材</h3><ul>{review.raw.ingredients.map((item) => <li key={`${item.raw_name}:${item.raw_text}`}><span>{item.raw_name}</span><b>{item.raw_amount || "未提供"}</b><small>{item.group}</small></li>)}</ul><h3>原始步骤</h3><ol>{review.raw.cooking_steps.map((step) => <li key={step.step_number}>{step.instruction}</li>)}</ol></section>
      <section className="card curation-panel"><header><div><p className="eyebrow">LLM 草稿与管理员修订</p><h2>最终页面展示结构</h2></div></header>
        <div className="curation-basics"><label className="wide-field">展示标题<input value={title} onChange={(event) => setTitle(event.target.value)}/></label><label>份数<input type="number" min="0.5" step="0.5" value={servings} onChange={(event) => setServings(event.target.value)}/></label><label>总制作时间（分钟）<input type="number" min="0" value={prepMinutes} onChange={(event) => setPrepMinutes(event.target.value)}/></label><fieldset><legend>适用餐次</legend>{(["breakfast", "lunch", "dinner"] as MealSlot[]).map((slot) => <label key={slot}><input type="checkbox" checked={slots.includes(slot)} onChange={(event) => setSlots((current) => event.target.checked ? [...current, slot] : current.filter((item) => item !== slot))}/>{slotLabels[slot]}</label>)}</fieldset></div>
        <h3>食材名称与数量</h3><p className="source-trace">原始食材名默认就是标准名称，无需人工映射。下拉框只用于管理员修正别名或误识别。</p><div className="ingredient-edit-list">{review.raw.ingredients.map((raw, index) => { const edit = ingredients[index]; const option = optionById.get(edit?.canonical_id); return <article key={`${raw.raw_name}:${index}`}><div className="ingredient-source"><strong>{raw.raw_name}</strong><span>{raw.raw_amount}</span></div><label>标准名称（自动）<select value={edit?.canonical_id ?? ""} onChange={(event) => setIngredients((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, canonical_id: event.target.value } : item))}><option value="">自动身份不可用</option>{detail.canonical_ingredients.map((item) => <option key={item.canonical_id} value={item.canonical_id}>{item.canonical_name} · {item.canonical_id}</option>)}</select></label><label>明确数量<input value={edit?.amount ?? ""} onChange={(event) => setIngredients((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, amount: event.target.value } : item))} placeholder="可留空"/></label><label>单位<select value={edit?.unit ?? "g"} disabled={!edit?.amount} onChange={(event) => setIngredients((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, unit: event.target.value } : item))}><option value="g">g</option><option value="kg">kg</option><option value="ml">ml</option><option value="count">个/只</option></select></label><label>定性数量<select value={edit?.qualitative_label ?? ""} disabled={Boolean(edit?.amount)} onChange={(event) => setIngredients((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, qualitative_label: event.target.value } : item))}><option value="">无</option><option value="适量">适量</option><option value="少许">少许</option></select></label><div className={`allergen-fact ${option?.allergen_composition_known ? "known" : "unknown"}`}>{option ? option.allergen_composition_known ? `过敏原：${option.allergens.join("、") || "已知无"}` : "成分未确认：可展示，禁止进入规划" : "正在读取自动身份"}</div></article>; })}</div>
        <h3>制作步骤</h3><div className="step-edit-list">{review.raw.cooking_steps.map((step) => <label key={step.step_number}><span>步骤 {step.step_number}</span><textarea rows={3} value={steps[step.step_number] ?? step.instruction} onChange={(event) => setSteps((current) => ({ ...current, [step.step_number]: event.target.value }))}/></label>)}</div>
      </section>
    </div>
    <section className="card release-panel"><div><h2>通过并发布</h2><p>管理员确认结构化内容与来源可追溯性后即可发布。缺少营养克数的菜谱可作为制作页面展示，但在营养数据完整前不会进入规划 Solver。</p></div><div className="release-actions"><label>数据集版本<input value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)}/></label><button className="button button-primary" type="button" disabled={Boolean(pending) || !detail.can_approve || !datasetVersion.trim()} onClick={() => void approveAndPublish()}>{pending === "通过并发布" ? "发布中…" : "通过并发布"}</button></div>{!detail.can_approve && review.status === "PENDING" && <p className="release-blocked">必须先完成 LLM 结构化，并解决数量、份数、餐次或制作步骤等页面发布阻塞。过敏原或营养资料不完整只会禁止进入规划，不会阻止内部页面展示。</p>}</section>
  </section>;
}
