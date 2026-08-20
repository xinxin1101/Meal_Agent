import { useCallback, useEffect, useMemo, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import type { AdminBatchPublishResponse, AdminRecipeCatalogItem, AdminReviewDetail } from "../../api/types";
import { AdminAcquisitionPanel } from "./AdminAcquisitionPanel";
import { AdminReviewWorkbench } from "./AdminReviewWorkbench";
import { explainRecipeBlocker } from "./blockers";

const slotLabel = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" } as const;
const processingLabel = { INITIAL_VALIDATED: "等待 LLM", LLM_FAILED: "LLM 失败", FINAL_VALIDATION_BLOCKED: "最终校验阻塞", FINAL_VALIDATED: "可批量审核" } as const;

export function AdminRecipesPage() {
  const [tab, setTab] = useState<"catalog" | "acquisition">("catalog");
  const [recipes, setRecipes] = useState<AdminRecipeCatalogItem[]>([]);
  const [selectedReviewId, setSelectedReviewId] = useState<string>();
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [structuring, setStructuring] = useState(false);
  const [structureProgress, setStructureProgress] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  const [datasetVersion, setDatasetVersion] = useState(`mealpilot-${new Date().toISOString().slice(0, 10)}`);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const load = useCallback(async () => {
    setLoading(true); setError(undefined);
    try { setRecipes(await apiClient.get<AdminRecipeCatalogItem[]>("/v1/admin/recipes")); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const visible = useMemo(() => { const keyword = query.trim().toLocaleLowerCase(); return keyword ? recipes.filter((recipe) => `${recipe.title} ${recipe.recipe_id ?? ""} ${recipe.ingredients.map((item) => item.canonical_name).join(" ")}`.toLocaleLowerCase().includes(keyword)) : recipes; }, [query, recipes]);
  const publishable = useMemo(() => recipes.filter((item) => item.origin === "REVIEW_QUEUE" && item.lifecycle_status === "PENDING" && item.processing_stage === "FINAL_VALIDATED" && ["PUBLICATION_READY", "SOLVER_READY"].includes(item.quality_status ?? "")), [recipes]);
  const awaitingStructure = useMemo(() => recipes.filter((item) => item.origin === "REVIEW_QUEUE" && item.lifecycle_status === "PENDING" && ["INITIAL_VALIDATED", "LLM_FAILED"].includes(item.processing_stage ?? "")), [recipes]);

  function toggleSelected(id: string) { setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); }
  // The projection does not expose an editable OCC field in data_version; use a detail lookup before batch submission.
  async function publishSelected() {
    setPublishing(true); setError(undefined); setNotice(undefined);
    try {
      const details = await Promise.all(selected.map((id) => apiClient.get<{ review: { review_version: number } }>(`/v1/admin/reviews/${id}`)));
      const response = await apiClient.post<AdminBatchPublishResponse>("/v1/admin/reviews/batch-publish", {
        items: selected.map((id, index) => ({ review_id: id, expected_review_version: details[index].review.review_version })),
        dataset_version: datasetVersion, confirm_internal_personal_study: true,
      });
      setNotice(`批量处理完成：发布 ${response.published_count} 条，失败 ${response.failed_count} 条。`);
      if (response.failed_count) setError(response.results.filter((item) => item.status === "FAILED").map((item) => `${item.review_id}: ${item.reason_code}`).join("；"));
      setSelected([]); setConfirmed(false); await load();
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setPublishing(false); }
  }

  async function structurePending() {
    setStructuring(true); setStructureProgress(0); setError(undefined); setNotice(undefined);
    const failures: string[] = [];
    for (const item of awaitingStructure) {
      try {
        const detail = await apiClient.get<AdminReviewDetail>(`/v1/admin/reviews/${item.record_id}`);
        await apiClient.post<AdminReviewDetail>(`/v1/admin/reviews/${item.record_id}/assist`, { expected_review_version: detail.review.review_version }, 105_000);
      } catch (requestError) { failures.push(`${item.title}: ${toUserMessage(requestError)}`); }
      setStructureProgress((current) => current + 1);
    }
    if (failures.length) setError(failures.join("；"));
    setNotice(`LLM 结构化处理完成：共 ${awaitingStructure.length} 条，失败 ${failures.length} 条。`);
    await load(); setStructuring(false);
  }

  if (selectedReviewId) return <AdminReviewWorkbench reviewId={selectedReviewId} onClose={() => setSelectedReviewId(undefined)} onChanged={() => void load()}/>;
  return <section className="admin-recipes-page">
    <header className="page-heading"><div className="page-heading-copy"><p className="eyebrow">管理员 · 菜谱数据治理</p><h1>菜谱采集、结构化与批量审核</h1><p>自动完成初检、LLM 结构化和最终复验；管理员只需检查合格草稿并批量发布。</p></div></header>
    <div className="admin-tabs" role="tablist" aria-label="菜谱管理功能"><button className={tab === "catalog" ? "active" : ""} role="tab" aria-selected={tab === "catalog"} onClick={() => setTab("catalog")}>菜谱目录与批量审核</button><button className={tab === "acquisition" ? "active" : ""} role="tab" aria-selected={tab === "acquisition"} onClick={() => setTab("acquisition")}>受控采集任务</button></div>
    {tab === "acquisition" ? <AdminAcquisitionPanel onReviewQueueChanged={load}/> : <>
      <section className="admin-catalog-toolbar card"><label><span>搜索标题、ID 或食材</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：鸡蛋"/></label><div><strong>{recipes.length}</strong><span>全部记录</span></div><div><strong>{publishable.length}</strong><span>可批量审核</span></div><button className="button button-secondary" type="button" onClick={() => void load()} disabled={loading}>刷新目录</button>{awaitingStructure.length > 0 && <button className="button button-primary" type="button" disabled={structuring} onClick={() => void structurePending()}>{structuring ? `LLM 处理中 ${structureProgress}/${awaitingStructure.length}` : `处理待结构化记录（${awaitingStructure.length}）`}</button>}</section>
      {publishable.length > 0 && <section className="card batch-review-bar"><div><strong>已选择 {selected.length} / {publishable.length} 条</strong><button className="text-button" type="button" onClick={() => setSelected(selected.length === publishable.length ? [] : publishable.map((item) => item.record_id))}>{selected.length === publishable.length ? "取消全选" : "选择全部合格草稿"}</button></div><label>数据集版本<input value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)}/></label><label className="history-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/><span>我已抽检所选结构化结果；这些数据仅用于内部个人学习，并保留来源追溯信息。</span></label><button className="button button-primary" type="button" disabled={!selected.length || !confirmed || publishing || !datasetVersion.trim()} onClick={() => void publishSelected()}>{publishing ? "批量发布中…" : "批量通过并发布"}</button></section>}
      {error && <div className="notice error" role="alert">{error}</div>}{notice && <div className="notice success" role="status">{notice}</div>}
      {loading ? <div className="admin-catalog-state" role="status">正在读取菜谱目录…</div> : visible.length === 0 ? <div className="admin-catalog-state"><strong>当前没有菜谱数据</strong><p>请转到“受控采集任务”执行预检并创建采集任务。</p></div> : <div className="admin-recipe-grid">{visible.map((recipe) => { const canSelect = publishable.some((item) => item.record_id === recipe.record_id); return <article className={`admin-recipe-card card ${selected.includes(recipe.record_id) ? "selected" : ""}`} key={recipe.record_id}>
        <header><div>{canSelect && <label className="review-select"><input type="checkbox" checked={selected.includes(recipe.record_id)} onChange={() => toggleSelected(recipe.record_id)}/><span>加入批量审核</span></label>}<p>{recipe.origin === "ACTIVE_CATALOG" ? "正式目录" : "审核队列"} · {recipe.supported_slots.map((slot) => slotLabel[slot]).join(" / ") || "餐次待确认"}</p><h2>{recipe.title}</h2><code>{recipe.recipe_id ?? recipe.record_id}</code></div><span className={recipe.quality_status === "BLOCKED" ? "blocked" : "eligible"}>{recipe.processing_stage ? processingLabel[recipe.processing_stage] : recipe.quality_status ?? recipe.lifecycle_status}</span></header>
        <dl><div><dt>状态</dt><dd>{recipe.lifecycle_status}</dd></div><div><dt>最终校验</dt><dd>{recipe.quality_status ?? "-"}</dd></div><div><dt>食材</dt><dd>{recipe.ingredients.length} 项</dd></div><div><dt>步骤</dt><dd>{recipe.cooking_steps.length} 步</dd></div></dl>
        <details><summary>查看食材与制作步骤</summary><section><h3>食材</h3><ul>{recipe.ingredients.map((item) => <li key={`${item.canonical_id}:${item.display_quantity}`}><span>{item.canonical_name}</span><b>{item.display_quantity}</b></li>)}</ul><h3>制作步骤</h3><ol>{recipe.cooking_steps.map((step) => <li key={step.step_number}>{step.instruction}</li>)}</ol></section></details>
        {recipe.blocking_reasons.length > 0 && <div className="admin-blockers"><strong>页面发布阻塞</strong><span>{recipe.blocking_reasons.map(explainRecipeBlocker).join(" · ")}</span></div>}
        {!recipe.solver_eligible && recipe.solver_blocking_reasons.length > 0 && <div className="admin-blockers"><strong>规划阻塞（不影响页面发布）</strong><span>{recipe.solver_blocking_reasons.map(explainRecipeBlocker).join(" · ")}</span></div>}
        {recipe.origin === "REVIEW_QUEUE" && <div className="admin-card-actions"><button className="button button-secondary" type="button" onClick={() => setSelectedReviewId(recipe.record_id)}>查看与修正</button></div>}
        <footer><span>来源：{recipe.source_id}</span><span>用途：内部个人学习</span><span>数据版本：{recipe.data_version}</span></footer>
      </article>; })}</div>}
    </>}
  </section>;
}
