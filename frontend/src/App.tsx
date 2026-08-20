import { FormEvent, useEffect, useMemo, useState } from "react";
import { AppPage, AppShell } from "./app/AppShell";
import { apiClient } from "./api/client";
import { ConflictError, toUserMessage } from "./api/errors";
import type { AdoptedMealPlan, AgentPlanningCommand, ChatPlanContext, DurableRunSnapshot, FeedbackCollection, HistoryCollection, MealPlan, MenuDraft, MenuDraftFailure, NegotiationOption, NutritionTargetSuggestion, PlanFeedback, PlanningConstraintsSnapshot, PlanningMode, PreferenceMemory, PreferenceMemoryItem, ProductReadiness, SavePlanFeedbackCommand } from "./api/types";
import { isMealPlan, isMenuDraft } from "./api/types";
import { Icon } from "./components/ui/Icon";
import { ChatWorkspace } from "./components/chat/ChatWorkspace";
import { NegotiationPanel } from "./components/negotiation/NegotiationPanel";
import { PlanningDashboard } from "./components/planning/PlanningDashboard";
import { useUserProfile } from "./context/UserProfileContext";
import { PreferenceEditor } from "./features/memory/PreferenceEditor";
import { HistoryPage } from "./features/history/HistoryPage";
import { PlanningComposer, PlanningConstraints } from "./features/planning/PlanningComposer";
import { PlanResult, recipeDisplayName } from "./features/planning/PlanResult";
import { MenuDraftResult } from "./features/planning/MenuDraftResult";
import { ProfileDrawer } from "./features/profile/ProfileDrawer";
import { ProfileFields } from "./features/profile/ProfileFields";
import { ProfileSummary } from "./features/profile/ProfileSummary";
import { useAuth } from "./context/AuthContext";
import { AccountPrivacyPanel } from "./features/profile/AccountPrivacyPanel";
import { AdminRecipesPage } from "./features/admin/AdminRecipesPage";

const statusLabels: Record<DurableRunSnapshot["status"], string> = {
  QUEUED: "计划已提交",
  RUNNING: "正在生成计划",
  PAUSED: "需要确认调整",
  COMPLETED: "计划已完成",
  FAILED: "本次规划未完成",
  CANCELLED: "规划已取消",
};
const slotLabels = { breakfast: "早餐", lunch: "午餐", dinner: "晚餐" };

function extractPlan(snapshot?: DurableRunSnapshot): MealPlan | undefined {
  const result = snapshot?.result?.result;
  return result && isMealPlan(result) ? result : undefined;
}

function readinessMessage(readiness?: ProductReadiness): string {
  if (!readiness) return "正在检查正式菜谱库是否可用于规划…";
  if (readiness.ready) return "";
  if (readiness.published_recipe_count > 0 && readiness.solver_eligible_count === 0) {
    return `已发布 ${readiness.published_recipe_count} 条可展示菜谱，但它们尚未补齐可信营养、数量或过敏原依据，当前可求解菜谱为 0 条。请在管理员菜谱目录查看“规划阻塞”，不能用 LLM 猜测这些安全数据。`;
  }
  return `当前正式菜谱库尚未达到业务就绪标准（已发布 ${readiness.published_recipe_count} 条，可求解 ${readiness.solver_eligible_count} 条）。请由管理员补齐早餐、午餐和晚餐的 Solver 就绪菜谱。`;
}

function menuDraftReadinessMessage(readiness?: ProductReadiness): string | undefined {
  if (!readiness) return "正在检查正式菜谱目录…";
  if (readiness.published_recipe_count < 3) return `普通菜单草稿至少需要 3 条已发布菜谱，当前只有 ${readiness.published_recipe_count} 条。`;
  return undefined;
}

export default function App() {
  const { account } = useAuth();
  const [activePage, setActivePage] = useState<AppPage>("plan");
  const [planningMode, setPlanningMode] = useState<PlanningMode>("verified_nutrition");
  const [profileOpen, setProfileOpen] = useState(false);
  const [query, setQuery] = useState("时间 60 分钟，蛋白质 90g，1500-1700 kcal");
  const [constraints, setConstraints] = useState<PlanningConstraints>({ minutes: "60", protein: "90", energyMin: "1500", energyMax: "1700" });
  const [snapshot, setSnapshot] = useState<DurableRunSnapshot>();
  const [submitting, setSubmitting] = useState(false);
  const [creatingPlan, setCreatingPlan] = useState(false);
  const [error, setError] = useState<string>();
  const [parseNotice, setParseNotice] = useState<string>();
  const [targetSuggestion, setTargetSuggestion] = useState<NutritionTargetSuggestion>();
  const [targetSuggestionLoading, setTargetSuggestionLoading] = useState(false);
  const [menuDraft, setMenuDraft] = useState<MenuDraft>();
  const [negotiationConflict, setNegotiationConflict] = useState<string>();
  const [savedMemories, setSavedMemories] = useState<PreferenceMemoryItem[]>([]);
  const [history, setHistory] = useState<HistoryCollection>({ user_id: account.user_id, collection_version: 0, items: [] });
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState<string>();
  const [useHistory, setUseHistory] = useState(true);
  const [adopting, setAdopting] = useState(false);
  const [assistantHistoryPlanIds, setAssistantHistoryPlanIds] = useState<string[]>([]);
  const [productReadiness, setProductReadiness] = useState<ProductReadiness>();
  const [feedback, setFeedback] = useState<PlanFeedback[]>([]);
  const userId = account.user_id;
  const { profile, validationErrors, toContract, saveProfile, profileSaving } = useUserProfile();

  useEffect(() => { void apiClient.get<PreferenceMemory>(`/v1/memory/${userId}`).then((memory) => setSavedMemories(memory.items)).catch(() => undefined); }, [userId]);
  useEffect(() => { void apiClient.get<FeedbackCollection>(`/v1/feedback/${userId}`).then((value) => setFeedback(value.items)).catch(() => undefined); }, [userId]);
  async function savePlanFeedback(historyId: string, command: SavePlanFeedbackCommand) { const saved = await apiClient.saveFeedback(userId, historyId, command); setFeedback((current) => [saved, ...current.filter((item) => item.history_id !== historyId)]); }
  async function refreshHistory() { setHistoryLoading(true); setHistoryError(undefined); try { setHistory(await apiClient.get<HistoryCollection>(`/v1/history/${userId}`)); } catch (requestError) { setHistoryError(toUserMessage(requestError)); } finally { setHistoryLoading(false); } }
  useEffect(() => { void refreshHistory(); }, [userId]);
  useEffect(() => { void apiClient.get<ProductReadiness>("/v1/readiness/product").then(setProductReadiness).catch(() => setProductReadiness(undefined)); }, [activePage]);

  async function refreshRun(runId: string) {
    try { setSnapshot(await apiClient.get<DurableRunSnapshot>(`/v1/runs/${runId}`)); } catch { /* SSE remains responsible for recovery. */ }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (validationErrors.length) { setError(validationErrors.join(" ")); setProfileOpen(true); return; }
    if (planningMode === "menu_draft") {
      setSubmitting(true); setSnapshot(undefined); setMenuDraft(undefined); setError(undefined); setNegotiationConflict(undefined);
      try {
        const result = await apiClient.post<MenuDraft | MenuDraftFailure>("/v1/menu-drafts", {
          draft_id: `draft-${crypto.randomUUID()}`,
          profile: toContract(`menu-draft-${crypto.randomUUID()}`),
          max_total_minutes: Number(constraints.minutes),
          acknowledge_unverified: true,
        });
        if (isMenuDraft(result)) setMenuDraft(result);
        else setError(result.reason_code === "INSUFFICIENT_SAFE_DISPLAY_RECIPES" ? "应用已声明的过敏原和忌口后，不足 3 条安全可用的已发布菜谱。请补充经过审核的菜谱，不能放宽安全约束。" : "当前没有已发布菜谱可用于普通菜单草稿。");
      } catch (requestError) { setError(toUserMessage(requestError)); }
      finally { setSubmitting(false); }
      return;
    }
    const runId = `ui-${crypto.randomUUID()}`;
    setSubmitting(true); setCreatingPlan(true); setSnapshot(undefined); setMenuDraft(undefined); setError(undefined); setNegotiationConflict(undefined);
    const command: AgentPlanningCommand = {
      run_id: runId,
      user_id: userId,
      use_history: useHistory && history.items.length > 0,
      query: savedMemories.length ? `${query}\n用户明确保存的偏好：${savedMemories.map((item) => `${item.category}:${item.value}`).join("；")}` : query,
      profile: toContract(`profile-${runId}`),
      request: { request_id: `request-${runId}`, max_total_minutes: Number(constraints.minutes), energy_kcal_range: { min: constraints.energyMin, max: constraints.energyMax }, protein_min_g: constraints.protein, numeric_policy_version: "contract-v2-decimal-v1" },
    };
    try { setSnapshot(await apiClient.createRun(command)); window.setTimeout(() => void refreshRun(runId), 250); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setSubmitting(false); setCreatingPlan(false); }
  }

  async function parseQuery() {
    setSubmitting(true); setError(undefined); setParseNotice(undefined);
    try {
      const body = await apiClient.post<{ source?: string; constraints?: Record<string, string> }>("/v1/constraint-suggestions", { query });
      const next = body.constraints ?? {};
      setConstraints((current) => {
        const [energyMin, energyMax] = next.energy_kcal_range?.split("-") ?? [];
        return { minutes: next.max_total_minutes ?? current.minutes, protein: next.protein_min_g ?? current.protein, energyMin: energyMin || current.energyMin, energyMax: energyMax || current.energyMax };
      });
      setParseNotice(Object.keys(next).length ? `已${body.source === "siliconflow" ? "通过模型" : "用本地规则"}识别，请确认下方目标。` : "没有识别到支持的数值目标，请直接编辑下方数值。");
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setSubmitting(false); }
  }

  async function suggestNutritionTargets() {
    if (validationErrors.length) { setError(validationErrors.join(" ")); setProfileOpen(true); return; }
    if (profile.nutrition_parameter_sex === "unspecified") {
      setError("生成估算建议前，请在规划档案中选择“女性参数”或“男性参数”。该参数不会根据姓名或行为推断。");
      setProfileOpen(true);
      return;
    }
    setTargetSuggestionLoading(true); setError(undefined);
    try { setTargetSuggestion(await apiClient.post<NutritionTargetSuggestion>("/v1/nutrition-target-suggestion", toContract(`suggestion-${crypto.randomUUID()}`))); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setTargetSuggestionLoading(false); }
  }

  function applyNutritionSuggestion() {
    if (!targetSuggestion) return;
    setConstraints((current) => ({ ...current, energyMin: targetSuggestion.energy_kcal_range.min, energyMax: targetSuggestion.energy_kcal_range.max, protein: targetSuggestion.protein_min_g }));
    setParseNotice(`已由你确认并应用 ${targetSuggestion.policy_version} 估算；提交前仍可修改。`);
  }

  async function cancelCurrentRun() {
    if (!snapshot || !["QUEUED", "RUNNING", "PAUSED"].includes(snapshot.status)) return;
    try { setSnapshot(await apiClient.cancelRun(snapshot.run_id, snapshot.run_version)); }
    catch (requestError) { setError(toUserMessage(requestError)); await refreshRun(snapshot.run_id); }
  }

  async function choose(optionId: string) {
    if (!snapshot) return;
    setSubmitting(true); setNegotiationConflict(undefined);
    try { setSnapshot(await apiClient.decideRun(snapshot.run_id, { option_id: optionId, expected_run_version: snapshot.run_version })); }
    catch (requestError) { if (requestError instanceof ConflictError) { setNegotiationConflict("可选调整已经更新，我们已同步最新状态，请重新选择。"); await refreshRun(snapshot.run_id); } else setError(toUserMessage(requestError)); }
    finally { setSubmitting(false); }
  }

  async function adoptCurrentPlan() {
    if (!snapshot || snapshot.status !== "COMPLETED") return;
    setAdopting(true); setError(undefined);
    try { await apiClient.adoptPlan(userId, snapshot.run_id, snapshot.run_version); await refreshHistory(); }
    catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setAdopting(false); }
  }

  async function deleteHistoryItem(historyId: string) {
    try { setHistory(await apiClient.deleteHistory(userId, historyId, history.collection_version)); }
    catch (requestError) { setHistoryError(toUserMessage(requestError)); await refreshHistory(); }
  }

  async function clearHistoryItems() {
    try { setHistory(await apiClient.clearHistory(userId, history.collection_version)); }
    catch (requestError) { setHistoryError(toUserMessage(requestError)); await refreshHistory(); }
  }

  function replanFromHistory(next: PlanningConstraintsSnapshot) {
    setConstraints({ minutes: String(next.max_total_minutes), protein: next.protein_min_g, energyMin: next.energy_kcal_range.min, energyMax: next.energy_kcal_range.max });
    setSnapshot(undefined); setActivePage("plan"); window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const plan = extractPlan(snapshot);
  const planContext = useMemo<ChatPlanContext | undefined>(() => plan ? {
    plan_id: plan.plan_id,
    meals: plan.selections.map((item) => `${slotLabels[item.slot]}：${item.recipe_title ?? recipeDisplayName(item.recipe_id)} × ${item.portion}`),
    totals: plan.totals,
  } : undefined, [plan]);
  const negotiationValues: Partial<Record<NegotiationOption["field"], string>> = { max_total_minutes: `${constraints.minutes} 分钟`, protein_min_g: `${constraints.protein} g`, energy_kcal_range: `${constraints.energyMin}–${constraints.energyMax} kcal` };

  const currentAdopted = Boolean(snapshot && history.items.some((item) => item.original_run_id === snapshot.run_id));
  return <AppShell activePage={activePage} onNavigate={setActivePage} memoryCount={savedMemories.length} historyCount={history.items.length} account={account}>
    <section className="today-page" hidden={activePage !== "plan"}>
      <header className="page-heading"><div className="page-heading-copy"><p className="eyebrow">今日 · 一日三餐</p><h1>把约束，变成好好吃饭。</h1><p>描述今天的时间和营养目标，MealPilot 会从本地菜谱中生成并复核一份可执行方案。</p></div><div className="safety-inline"><Icon name="shield" size={20}/><span>仅适用于健康成年人，不构成医疗建议。安全约束不会被模型放宽。</span></div></header>
      <div className="today-layout"><PlanningComposer planningMode={planningMode} query={query} constraints={constraints} parseNotice={parseNotice} submitting={submitting} canSubmit={!validationErrors.length && (planningMode === "menu_draft" ? (productReadiness?.published_recipe_count ?? 0) >= 3 : productReadiness?.ready === true)} readinessMessage={planningMode === "menu_draft" ? menuDraftReadinessMessage(productReadiness) : productReadiness?.ready ? undefined : readinessMessage(productReadiness)} error={error} useHistory={useHistory} historyCount={history.items.length} targetSuggestion={targetSuggestion} targetSuggestionLoading={targetSuggestionLoading} onPlanningModeChange={(value) => { setPlanningMode(value); setSnapshot(undefined); setMenuDraft(undefined); setError(undefined); }} onUseHistoryChange={setUseHistory} onQueryChange={setQuery} onConstraintChange={(key,value) => setConstraints((current) => ({...current,[key]:value}))} onParse={() => void parseQuery()} onSuggestTargets={() => void suggestNutritionTargets()} onApplySuggestion={applyNutritionSuggestion} onSubmit={submit} onOpenProfile={() => setProfileOpen(true)}/><ProfileSummary onEdit={() => setProfileOpen(true)}/></div>
      {(creatingPlan || snapshot) && <section className="run-panel card" aria-live="polite" aria-busy={creatingPlan || snapshot?.status === "QUEUED" || snapshot?.status === "RUNNING"}><div className="run-heading"><div><h2>规划进度</h2><p>正在按照硬约束筛选、组合并复核三餐。</p></div><div className="run-heading-actions">{snapshot && ["QUEUED", "RUNNING", "PAUSED"].includes(snapshot.status) && <button className="button button-secondary" type="button" onClick={() => void cancelCurrentRun()}>取消本次规划</button>}{snapshot && <span className={`run-status ${snapshot.status.toLowerCase()}`}><i />{statusLabels[snapshot.status]}</span>}</div></div>{creatingPlan && !snapshot ? <div className="planning-loading"><span className="loading-ring"/><strong>正在创建规划任务</strong><p>你的输入会先经过安全过滤，再进入确定性求解。</p></div> : snapshot && <PlanningDashboard runId={snapshot.run_id} runVersion={snapshot.run_version} status={snapshot.status} onSnapshot={setSnapshot}/>} {snapshot?.status === "PAUSED" && snapshot.proposal && <NegotiationPanel proposal={snapshot.proposal} runVersion={snapshot.run_version} currentValues={negotiationValues} pending={submitting} conflictMessage={negotiationConflict} onConfirm={choose}/>} {snapshot?.status === "FAILED" && <div className="notice error">{snapshot.result && !isMealPlan(snapshot.result.result) ? snapshot.result.result.message : "当前条件下没有生成可验证方案，请保留输入后调整条件再试。"}</div>}{snapshot?.status === "CANCELLED" && <div className="notice">本次规划已经取消，没有方案会进入历史记录。</div>}</section>}
      {snapshot?.status === "COMPLETED" && plan && <PlanResult plan={plan} explanation={snapshot.result?.explanation} energyMin={Number(constraints.energyMin)} energyMax={Number(constraints.energyMax)} proteinMin={Number(constraints.protein)} adopted={currentAdopted} adopting={adopting} onAdopt={() => void adoptCurrentPlan()} onAsk={() => setActivePage("assistant")} onReplan={() => { setSnapshot(undefined); window.scrollTo({top:0,behavior:"smooth"}); }}/>} 
      {planningMode === "menu_draft" && menuDraft && <MenuDraftResult draft={menuDraft} onRebuild={() => { setMenuDraft(undefined); window.scrollTo({top:0,behavior:"smooth"}); }}/>} 
      <ProfileDrawer open={profileOpen} onClose={() => setProfileOpen(false)}/>
    </section>
    <section hidden={activePage !== "history"}><HistoryPage collection={history} feedback={feedback} loading={historyLoading} error={historyError} onRefresh={() => void refreshHistory()} onDelete={deleteHistoryItem} onClear={clearHistoryItems} onFeedback={savePlanFeedback} onAsk={(item: AdoptedMealPlan) => { setAssistantHistoryPlanIds([item.history_id]); setActivePage("assistant"); }} onReplan={replanFromHistory}/></section>
    <section className="assistant-page" hidden={activePage !== "assistant"}><header className="page-heading"><div className="page-heading-copy"><p className="eyebrow">连续对话</p><h1>围绕计划，继续聊。</h1><p>对话会持久保存；每条回答都会展示使用的当前计划、精确历史记录和长期偏好。</p></div>{planContext && <button className="button button-secondary" type="button" onClick={() => setActivePage("plan")}><Icon name="calendar" size={17}/>查看当前计划</button>}</header><ChatWorkspace userId={userId} planContext={planContext} memoryCount={savedMemories.length} history={history.items} initialHistoryPlanIds={assistantHistoryPlanIds} onMemoryChange={setSavedMemories}/></section>
    <section className="profile-page" hidden={activePage !== "profile"}><header className="page-heading"><div className="page-heading-copy"><p className="eyebrow">由你控制的数据</p><h1>偏好与规划档案</h1><p>长期偏好、身体参数和过敏原分区管理。过敏原不会从行为推断。</p></div><div className="safety-inline"><Icon name="shield" size={20}/><span>个人档案只用于健康成年人膳食规划；日志不会记录敏感字段。</span></div></header><div className="profile-page-grid"><section className="profile-editor-card card"><ProfileFields radioGroupName="page-allergen-status"/></section><PreferenceEditor userId={userId} items={savedMemories} onChange={setSavedMemories}/></div></section>
    <section hidden={activePage !== "profile"} className="profile-account-actions"><button className="button button-primary profile-save" type="button" disabled={validationErrors.length > 0 || profileSaving} onClick={() => void saveProfile()}>{profileSaving ? "保存中…" : "保存规划档案"}</button><AccountPrivacyPanel/></section>
    {account.role === "ADMIN" && <section hidden={activePage !== "admin-recipes"}><AdminRecipesPage /></section>}
  </AppShell>;
}
