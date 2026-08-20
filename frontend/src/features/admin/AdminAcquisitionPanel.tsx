import { useEffect, useMemo, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import type { RecipeAcquisitionEvent, RecipeAcquisitionJob, RecipeAcquisitionPreview, SourcePolicySummary } from "../../api/types";

const statusLabel: Record<RecipeAcquisitionJob["status"], string> = {
  QUEUED: "等待 Worker", RUNNING: "采集中", REVIEW_READY: "已进入审核队列",
  PARTIAL: "完成，无新增内容", FAILED: "失败", CANCELLED: "已取消",
};

export function AdminAcquisitionPanel({ onReviewQueueChanged }: { onReviewQueueChanged: () => void }) {
  const [policies, setPolicies] = useState<SourcePolicySummary[]>([]);
  const [jobs, setJobs] = useState<RecipeAcquisitionJob[]>([]);
  const [selectedPolicy, setSelectedPolicy] = useState("meishichina.personal-study");
  const [maxRecords, setMaxRecords] = useState(10);
  const [acknowledged, setAcknowledged] = useState(false);
  const [preview, setPreview] = useState<RecipeAcquisitionPreview>();
  const [events, setEvents] = useState<Record<string, RecipeAcquisitionEvent[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function load() {
    try {
      const [nextPolicies, nextJobs] = await Promise.all([
        apiClient.get<SourcePolicySummary[]>("/v1/admin/acquisition/policies"),
        apiClient.get<RecipeAcquisitionJob[]>("/v1/admin/acquisition/jobs"),
      ]);
      setPolicies(nextPolicies); setJobs(nextJobs);
    } catch (requestError) { setError(toUserMessage(requestError)); }
  }

  useEffect(() => { void load(); }, []);
  const active = useMemo(() => jobs.some((job) => ["QUEUED", "RUNNING"].includes(job.status)), [jobs]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [active]);
  useEffect(() => {
    const completed = jobs.filter((job) => job.status === "REVIEW_READY");
    if (completed.length) onReviewQueueChanged();
  }, [jobs, onReviewQueueChanged]);

  const policy = policies.find((item) => item.policy_id === selectedPolicy);
  const command = { policy_id: selectedPolicy, max_records: maxRecords, acknowledge_personal_study: true as const };

  async function createPreview() {
    setBusy(true); setError(undefined);
    try { setPreview(await apiClient.post<RecipeAcquisitionPreview>("/v1/admin/acquisition/previews", command)); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setBusy(false); }
  }

  async function queueJob() {
    setBusy(true); setError(undefined);
    try { await apiClient.post<RecipeAcquisitionJob>("/v1/admin/acquisition/jobs", command); setAcknowledged(false); await load(); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setBusy(false); }
  }

  async function cancel(job: RecipeAcquisitionJob) {
    setBusy(true); setError(undefined);
    try { await apiClient.post<RecipeAcquisitionJob>(`/v1/admin/acquisition/jobs/${job.job_id}/cancel`, { expected_job_version: job.job_version }); await load(); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setBusy(false); }
  }

  async function toggleEvents(jobId: string) {
    if (events[jobId]) { setEvents((current) => { const next = { ...current }; delete next[jobId]; return next; }); return; }
    try { setEvents((current) => ({ ...current, [jobId]: [] })); const values = await apiClient.get<RecipeAcquisitionEvent[]>(`/v1/admin/acquisition/jobs/${jobId}/events`); setEvents((current) => ({ ...current, [jobId]: values })); }
    catch (requestError) { setError(toUserMessage(requestError)); }
  }

  return <section className="admin-acquisition">
    <div className="admin-acquisition-intro card">
      <div><p className="eyebrow">独立采集与自动结构化</p><h2>受控采集任务</h2><p>Worker 会依次完成原始数据初检、LLM 受约束结构化和确定性最终复验。只有最终复验通过的草稿才能进入管理员批量发布范围。</p></div>
      <div className="acquisition-form">
        <label><span>来源策略</span><select value={selectedPolicy} onChange={(event) => { setSelectedPolicy(event.target.value); setPreview(undefined); }}>{policies.map((item) => <option key={item.policy_id} value={item.policy_id}>{item.policy_id}</option>)}</select></label>
        <label><span>本次最多新增（不是数据库上限）</span><input type="number" min={1} max={Math.min(20, policy?.max_records_per_run ?? 20)} value={maxRecords} onChange={(event) => { setMaxRecords(Number(event.target.value)); setPreview(undefined); }}/></label>
        <div className="policy-facts"><span>最多扫描：{policy?.max_pages_per_category ?? "-"} 个分类页</span><span>图片：不采集</span><span>评论：不采集</span><span>自动发布：关闭</span><span>最小间隔：{policy?.minimum_delay_seconds ?? "-"} 秒</span></div>
        <label className="history-consent"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)}/><span>我确认本次仅用于个人学习，并会在批量发布前抽检来源与最终结构化结果。</span></label>
        <div className="admin-card-actions"><button className="button button-secondary" type="button" onClick={() => void createPreview()} disabled={busy}>仅预检（不联网）</button><button className="button button-primary" type="button" onClick={() => void queueJob()} disabled={busy || !acknowledged || !preview?.executable}>加入采集队列</button></div>
        {!policy?.enabled && <div className="notice">该策略当前处于禁用状态。请先在仓库配置中审查并显式启用，界面不能绕过这一安全门禁。</div>}
        {preview && <div className={preview.executable ? "notice success" : "notice error"} role="status">预检：{preview.executable ? "允许排队" : `阻塞：${preview.stops.join(" · ")}`}</div>}
      </div>
    </div>
    {error && <div className="notice error" role="alert">{error}</div>}
    <div className="acquisition-job-list">
      {jobs.length === 0 ? <div className="admin-catalog-state"><strong>暂无采集任务</strong><p>先执行无网络预检，再确认个人学习边界并加入队列。</p></div> : jobs.map((job) => <article className="card acquisition-job" key={job.job_id}>
        <header><div><strong>{statusLabel[job.status]}</strong><code>{job.job_id}</code></div><span className={`job-status ${job.status.toLowerCase()}`}>{job.status}</span></header>
        <dl><div><dt>策略</dt><dd>{job.policy_id}</dd></div><div><dt>上限</dt><dd>{job.max_records} 条</dd></div><div><dt>尝试</dt><dd>{job.attempts}</dd></div><div><dt>创建时间</dt><dd>{new Date(job.created_at).toLocaleString()}</dd></div></dl>
        {job.error_code && <div className="notice error">错误代码：{job.error_code}</div>}
        {job.result && <p className="job-result">发现 {String(job.result.discovered_count ?? 0)} 条；新增或变化 {String(job.result.new_or_changed_count ?? 0)} 条；{Number(job.result.new_or_changed_count ?? 0) > 0 ? "新增记录已进入隔离审核队列。" : "当前扫描范围内均为已采集记录，没有触及数据库容量上限。"}</p>}
        <div className="admin-card-actions">{job.status === "QUEUED" && <button className="button button-secondary" type="button" disabled={busy} onClick={() => void cancel(job)}>取消排队</button>}<button className="button button-ghost" type="button" onClick={() => void toggleEvents(job.job_id)}>{events[job.job_id] ? "收起事件" : "查看事件"}</button></div>
        {events[job.job_id] && <ol className="job-events">{events[job.job_id].map((event) => <li key={event.event_id}><time>{new Date(event.created_at).toLocaleTimeString()}</time><span>{event.event_type}</span><small>v{event.job_version}</small></li>)}</ol>}
      </article>)}
    </div>
  </section>;
}
