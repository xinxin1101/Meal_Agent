import { useCallback, useEffect, useMemo, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage, toUserMessageForCode } from "../../api/errors";
import type { AdminBatchPublishResponse, AdminRecipeCatalogItem, AdminReviewDetail, LlmReviewJob } from "../../api/types";
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
  const [readableSelected, setReadableSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [workflowFilter, setWorkflowFilter] = useState<"all" | "failed" | "blocked" | "waiting" | "ready">("all");
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [structuring, setStructuring] = useState(false);
  const [structureProgress, setStructureProgress] = useState(0);
  const [llmJobs, setLlmJobs] = useState<LlmReviewJob[]>([]);
  const [workerStatus, setWorkerStatus] = useState<Record<string, unknown>>();
  const [confirmed, setConfirmed] = useState(false);
  const [datasetVersion, setDatasetVersion] = useState(`mealpilot-${new Date().toISOString().slice(0, 10)}`);
  const [error, setError] = useState<string>();
  const [notice, setNotice] = useState<string>();
  const load = useCallback(async () => {
    setLoading(true); setError(undefined);
    try {
      const [catalog, jobs] = await Promise.all([
        apiClient.get<AdminRecipeCatalogItem[]>("/v1/admin/recipes"),
        apiClient.get<LlmReviewJob[]>("/v1/admin/llm-review-jobs?limit=50"),
      ]);
      setRecipes(catalog); setLlmJobs(jobs);
      void apiClient.get<Record<string, unknown>>("/v1/admin/llm-review-worker-status").then(setWorkerStatus).catch(() => undefined);
    }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const active = llmJobs.filter((job) => ["QUEUED", "RUNNING"].includes(job.status));
    if (!active.length) return;
    const timer = window.setInterval(() => {
      void Promise.all(active.map((job) => apiClient.get<LlmReviewJob>(`/v1/admin/llm-review-jobs/${job.job_id}`)))
        .then(async (next) => {
          setLlmJobs((current) => current.map((job) => next.find((value) => value.job_id === job.job_id) ?? job));
          if (next.some((job) => ["SUCCEEDED", "FAILED"].includes(job.status))) await load();
        })
        .catch((requestError) => setError(toUserMessage(requestError)));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [llmJobs, load]);
  const visible = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase();
    const stageMatches = (recipe: AdminRecipeCatalogItem) => workflowFilter === "all"
      || (workflowFilter === "failed" && recipe.processing_stage === "LLM_FAILED")
      || (workflowFilter === "blocked" && recipe.processing_stage === "FINAL_VALIDATION_BLOCKED")
      || (workflowFilter === "waiting" && recipe.processing_stage === "INITIAL_VALIDATED")
      || (workflowFilter === "ready" && recipe.processing_stage === "FINAL_VALIDATED");
    return recipes.filter((recipe) => stageMatches(recipe) && (!keyword || `${recipe.title} ${recipe.recipe_id ?? ""} ${recipe.ingredients.map((item) => item.canonical_name).join(" ")}`.toLocaleLowerCase().includes(keyword)));
  }, [query, recipes, workflowFilter]);
  const publishable = useMemo(() => recipes.filter((item) => item.origin === "REVIEW_QUEUE" && item.lifecycle_status === "PENDING" && item.processing_stage === "FINAL_VALIDATED" && ["PUBLICATION_READY", "SOLVER_READY"].includes(item.quality_status ?? "")), [recipes]);
  const readablePublishable = useMemo(() => recipes.filter((item) => item.origin === "REVIEW_QUEUE" && item.lifecycle_status === "PENDING" && item.processing_stage === "FINAL_VALIDATED" && item.quality_status === "BLOCKED" && item.readable_eligible && !item.readable_published), [recipes]);
  const awaitingStructure = useMemo(() => recipes.filter((item) => item.origin === "REVIEW_QUEUE" && item.lifecycle_status === "PENDING" && ["INITIAL_VALIDATED", "LLM_FAILED"].includes(item.processing_stage ?? "")), [recipes]);
  const capabilityCounts = useMemo(() => ({
    readable: recipes.filter((item) => item.readable_eligible).length,
    menu: recipes.filter((item) => item.menu_draft_eligible).length,
    solver: recipes.filter((item) => item.solver_eligible).length,
  }), [recipes]);

  function toggleSelected(id: string) { setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); }
  function toggleReadableSelected(id: string) { setReadableSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]); }
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

  async function publishReadableSelected() {
    setPublishing(true); setError(undefined); setNotice(undefined);
    try {
      const details = await Promise.all(readableSelected.map((id) => apiClient.get<{ review: { review_version: number } }>(`/v1/admin/reviews/${id}`)));
      const response = await apiClient.post<AdminBatchPublishResponse>("/v1/admin/reviews/batch-publish-readable", {
        items: readableSelected.map((id, index) => ({ review_id: id, expected_review_version: details[index].review.review_version })),
        dataset_version: datasetVersion, confirm_internal_personal_study: true,
      });
      setNotice(`可阅读菜谱发布完成：${response.published_count} 条，失败 ${response.failed_count} 条。它们不会进入菜单或营养 Solver。`);
      if (response.failed_count) setError(response.results.filter((item) => item.status === "FAILED").map((item) => `${item.review_id}: ${item.reason_code}`).join("；"));
      setReadableSelected([]); setConfirmed(false); await load();
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setPublishing(false); }
  }

  async function structurePending() {
    setStructuring(true); setStructureProgress(0); setError(undefined); setNotice(undefined);
    const failures: string[] = [];
    const queued: LlmReviewJob[] = [];
    for (const item of awaitingStructure) {
      try {
        const detail = await apiClient.get<AdminReviewDetail>(`/v1/admin/reviews/${item.record_id}`);
        queued.push(await apiClient.post<LlmReviewJob>(`/v1/admin/reviews/${item.record_id}/assist`, { expected_review_version: detail.review.review_version }));
      } catch (requestError) { failures.push(`${item.title}: ${toUserMessage(requestError)}`); }
      setStructureProgress((current) => current + 1);
    }
    setLlmJobs((current) => [...queued, ...current.filter((job) => !queued.some((next) => next.job_id === job.job_id))]);
    await load();
    if (failures.length) setError(failures.join("；"));
    setNotice(`LLM 结构化任务已入队：共 ${queued.length} 条，提交失败 ${failures.length} 条。下方“任务中心”会汇总最终结果。`);
    setStructuring(false);
  }

  const llmJobSummary = useMemo(() => ({
    queued: llmJobs.filter((item) => item.status === "QUEUED").length,
    running: llmJobs.filter((item) => item.status === "RUNNING").length,
    succeeded: llmJobs.filter((item) => item.status === "SUCCEEDED").length,
    failed: llmJobs.filter((item) => item.status === "FAILED").length,
  }), [llmJobs]);

  if (selectedReviewId) return <AdminReviewWorkbench reviewId={selectedReviewId} onClose={() => setSelectedReviewId(undefined)} onChanged={() => void load()}/>;
  return <section className="admin-recipes-page">
    <header className="page-heading"><div className="page-heading-copy"><p className="eyebrow">管理员 · 菜谱数据治理</p><h1>菜谱采集、结构化与批量审核</h1><p>自动完成初检、LLM 结构化和最终复验；管理员只需检查合格草稿并批量发布。</p></div></header>
    <div className="admin-tabs" role="tablist" aria-label="菜谱管理功能"><button className={tab === "catalog" ? "active" : ""} role="tab" aria-selected={tab === "catalog"} onClick={() => setTab("catalog")}>菜谱目录与批量审核</button><button className={tab === "acquisition" ? "active" : ""} role="tab" aria-selected={tab === "acquisition"} onClick={() => setTab("acquisition")}>受控采集任务</button></div>
    {tab === "acquisition" ? <AdminAcquisitionPanel onReviewQueueChanged={load}/> : <>
      <section className="admin-catalog-toolbar card"><label><span>搜索标题、ID 或食材</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="例如：鸡蛋"/></label><div><strong>{recipes.length}</strong><span>全部记录</span></div><div><strong>{capabilityCounts.readable}</strong><span>可阅读</span></div><div><strong>{capabilityCounts.menu}</strong><span>可编排菜单</span></div><div><strong>{capabilityCounts.solver}</strong><span>可营养规划</span></div><div><strong>{publishable.length}</strong><span>可批量审核</span></div><button className="button button-secondary" type="button" onClick={() => void load()} disabled={loading}>刷新目录</button>{awaitingStructure.length > 0 && <button className="button button-primary" type="button" disabled={structuring} onClick={() => void structurePending()}>{structuring ? `LLM 处理中 ${structureProgress}/${awaitingStructure.length}` : `处理待结构化记录（${awaitingStructure.length}）`}</button>}<div className="admin-card-actions" role="group" aria-label="审核状态筛选">{([['all','全部'],['failed','LLM 失败'],['waiting','等待 LLM'],['blocked','最终校验阻塞'],['ready','可审核']] as const).map(([value,label]) => <button key={value} className={`button button-ghost ${workflowFilter === value ? "active" : ""}`} type="button" onClick={() => setWorkflowFilter(value)}>{label}</button>)}</div></section>
      {llmJobs.length > 0 && <section className="card batch-review-bar" aria-label="LLM 结构化任务中心"><div><strong>LLM 任务中心</strong><span> 排队 {llmJobSummary.queued} · 处理中 {llmJobSummary.running} · 成功 {llmJobSummary.succeeded} · 失败 {llmJobSummary.failed}</span></div><details><summary>查看最近 {llmJobs.length} 个结构化任务</summary><ul>{llmJobs.map((job) => <li key={job.job_id}><button className="text-button" type="button" onClick={() => setSelectedReviewId(job.review_id)}>{job.review_id}</button> · {job.status}{job.error_code ? ` · ${toUserMessageForCode(job.error_code)}` : ""}</li>)}</ul></details></section>}
      {workerStatus && <section className="card batch-review-bar" aria-label="结构化 Worker 状态"><div><strong>结构化 Worker</strong><span> {String(workerStatus.state ?? "UNKNOWN")} · 模型配置 {Boolean(workerStatus.provider_configured) ? "已识别" : "未配置"} · DNS {String(workerStatus.dns_status ?? "未知")}</span></div>{Boolean(workerStatus.last_llm_job_id) && <p className="job-result">最近任务：{String(workerStatus.last_llm_job_id)} · {String(workerStatus.last_llm_status ?? "UNKNOWN")}{workerStatus.last_llm_finished_at ? ` · ${new Date(String(workerStatus.last_llm_finished_at)).toLocaleString()}` : " · 尚未结束"}</p>}{Boolean(workerStatus.last_llm_error_code) && <p className="release-blocked">最近模型任务：{toUserMessageForCode(String(workerStatus.last_llm_error_code))}</p>}</section>}
      {publishable.length > 0 && <section className="card batch-review-bar"><div><strong>已选择 {selected.length} / {publishable.length} 条</strong><button className="text-button" type="button" onClick={() => setSelected(selected.length === publishable.length ? [] : publishable.map((item) => item.record_id))}>{selected.length === publishable.length ? "取消全选" : "选择全部合格草稿"}</button></div><label>数据集版本<input value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)}/></label><label className="history-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/><span>我已抽检所选结构化结果；这些数据仅用于内部个人学习，并保留来源追溯信息。</span></label><button className="button button-primary" type="button" disabled={!selected.length || !confirmed || publishing || !datasetVersion.trim()} onClick={() => void publishSelected()}>{publishing ? "批量发布中…" : "批量通过并发布"}</button></section>}
      {readablePublishable.length > 0 && <section className="card batch-review-bar"><div><strong>可阅读发布：已选择 {readableSelected.length} / {readablePublishable.length} 条</strong><button className="text-button" type="button" onClick={() => setReadableSelected(readableSelected.length === readablePublishable.length ? [] : readablePublishable.map((item) => item.record_id))}>{readableSelected.length === readablePublishable.length ? "取消全选" : "选择全部可阅读草稿"}</button></div><label>数据集版本<input value={datasetVersion} onChange={(event) => setDatasetVersion(event.target.value)}/></label><label className="history-consent"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/><span>我已抽检来源与步骤。发布后仅用于菜谱库展示，不会用于菜单推荐或营养规划。</span></label><button className="button button-secondary" type="button" disabled={!readableSelected.length || !confirmed || publishing || !datasetVersion.trim()} onClick={() => void publishReadableSelected()}>{publishing ? "发布中…" : "发布为可阅读菜谱"}</button></section>}
      {error && <div className="notice error" role="alert">{error}</div>}{notice && <div className="notice success" role="status">{notice}</div>}
      {loading ? <div className="admin-catalog-state" role="status">正在读取菜谱目录…</div> : visible.length === 0 ? <div className="admin-catalog-state"><strong>当前没有菜谱数据</strong><p>请转到“受控采集任务”执行预检并创建采集任务。</p></div> : <div className="admin-recipe-grid">{visible.map((recipe) => { const canSelect = publishable.some((item) => item.record_id === recipe.record_id); const canReadableSelect = readablePublishable.some((item) => item.record_id === recipe.record_id); return <article className={`admin-recipe-card card ${selected.includes(recipe.record_id) || readableSelected.includes(recipe.record_id) ? "selected" : ""}`} key={recipe.record_id}>
        <header><div>{canSelect && <label className="review-select"><input type="checkbox" checked={selected.includes(recipe.record_id)} onChange={() => toggleSelected(recipe.record_id)}/><span>加入菜单/营养发布</span></label>}{canReadableSelect && <label className="review-select"><input type="checkbox" checked={readableSelected.includes(recipe.record_id)} onChange={() => toggleReadableSelected(recipe.record_id)}/><span>加入可阅读发布</span></label>}<p>{recipe.origin === "ACTIVE_CATALOG" ? "正式目录" : "审核队列"} · {recipe.supported_slots.map((slot) => slotLabel[slot]).join(" / ") || "餐次待确认"}</p><h2>{recipe.title}</h2><code>{recipe.recipe_id ?? recipe.record_id}</code></div><span className={recipe.quality_status === "BLOCKED" ? "blocked" : "eligible"}>{recipe.readable_published ? "可阅读已发布" : recipe.processing_stage ? processingLabel[recipe.processing_stage] : recipe.quality_status ?? recipe.lifecycle_status}</span></header>
        <dl><div><dt>状态</dt><dd>{recipe.lifecycle_status}</dd></div><div><dt>最终校验</dt><dd>{recipe.quality_status ?? "-"}</dd></div><div><dt>用途</dt><dd>{recipe.solver_eligible ? "营养规划" : recipe.menu_draft_eligible ? "菜单草案" : recipe.readable_published ? "可阅读已发布" : recipe.readable_eligible ? "可阅读" : "待修正"}</dd></div><div><dt>食材</dt><dd>{recipe.ingredients.length} 项</dd></div><div><dt>步骤</dt><dd>{recipe.cooking_steps.length} 步</dd></div></dl>
        <details><summary>查看食材与制作步骤</summary><section><h3>食材</h3><ul>{recipe.ingredients.map((item, index) => <li key={`${recipe.record_id}:ingredient:${index}`}><span>{item.canonical_name}</span><b>{item.display_quantity}</b></li>)}</ul><h3>制作步骤</h3><ol>{recipe.cooking_steps.map((step) => <li key={step.step_number}>{step.instruction}</li>)}</ol></section></details>
        {recipe.blocking_reasons.length > 0 && <div className="admin-blockers"><strong>页面发布阻塞</strong><span>{recipe.blocking_reasons.map(explainRecipeBlocker).join(" · ")}</span></div>}
        {!recipe.solver_eligible && recipe.solver_blocking_reasons.length > 0 && <div className="admin-blockers"><strong>规划阻塞（不影响页面发布）</strong><span>{recipe.solver_blocking_reasons.map(explainRecipeBlocker).join(" · ")}</span></div>}
        {recipe.origin === "REVIEW_QUEUE" && <div className="admin-card-actions"><button className="button button-secondary" type="button" onClick={() => setSelectedReviewId(recipe.record_id)}>查看与修正</button></div>}
        <footer><span>来源：{recipe.source_id}</span><span>用途：内部个人学习</span><span>数据版本：{recipe.data_version}</span></footer>
      </article>; })}</div>}
    </>}
  </section>;
}
