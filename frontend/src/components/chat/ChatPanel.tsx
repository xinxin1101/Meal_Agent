import { FormEvent, useEffect, useMemo, useState } from "react";
import { apiRequest } from "../../api/client";
import { ConflictError, toUserMessage } from "../../api/errors";
import type {
  AdoptedMealPlan,
  ChatPlanContext,
  ChatRequest,
  ChatResponse,
  ConversationDetail,
  ConversationMessage,
  ConversationSummary,
  PreferenceMemoryItem,
} from "../../api/types";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { Icon } from "../ui/Icon";

const categoryLabels: Record<PreferenceMemoryItem["category"], string> = {
  food_preference: "口味偏好",
  avoidance: "主动忌口",
  cooking_style: "烹饪偏好",
};

const formatShortDate = (value: string) => new Intl.DateTimeFormat("zh-CN", {
  month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
}).format(new Date(value));

interface ChatPanelProps {
  userId: string;
  planContext?: ChatPlanContext;
  history: AdoptedMealPlan[];
  initialHistoryPlanIds: string[];
}

export function ChatPanel({ userId, planContext, history, initialHistoryPlanIds }: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail>();
  const [selectedHistoryIds, setSelectedHistoryIds] = useState<string[]>(initialHistoryPlanIds);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [useCurrentPlan, setUseCurrentPlan] = useState(true);
  const [usePreferences, setUsePreferences] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<ConversationSummary>();
  const [deleting, setDeleting] = useState(false);

  const selectedHistory = useMemo(
    () => history.filter((item) => selectedHistoryIds.includes(item.history_id)),
    [history, selectedHistoryIds],
  );

  useEffect(() => {
    setSelectedHistoryIds(initialHistoryPlanIds.filter((id) => history.some((item) => item.history_id === id)));
  }, [history, initialHistoryPlanIds]);

  useEffect(() => { void initialize(); }, [userId]);

  async function initialize() {
    setLoading(true);
    setError(undefined);
    try {
      const items = await apiRequest<ConversationSummary[]>(`/v1/users/${userId}/conversations`);
      setConversations(items);
      if (items.length) await openConversation(items[0].conversation_id, false);
      else await createConversation();
    } catch (requestError) {
      setError(toUserMessage(requestError));
    } finally {
      setLoading(false);
    }
  }

  async function refreshList() {
    const items = await apiRequest<ConversationSummary[]>(`/v1/users/${userId}/conversations`);
    setConversations(items);
  }

  async function createConversation() {
    setError(undefined);
    const detail = await apiRequest<ConversationDetail>(`/v1/users/${userId}/conversations`, {
      method: "POST", body: JSON.stringify({}),
    });
    setActiveConversation(detail);
    await refreshList();
  }

  async function openConversation(conversationId: string, showLoading = true) {
    if (showLoading) setLoading(true);
    setError(undefined);
    try {
      const detail = await apiRequest<ConversationDetail>(`/v1/users/${userId}/conversations/${conversationId}`);
      setActiveConversation(detail);
    } catch (requestError) {
      setError(toUserMessage(requestError));
    } finally {
      if (showLoading) setLoading(false);
    }
  }

  async function deleteConversation() {
    if (!deleteTarget) return;
    setDeleting(true); setError(undefined);
    try {
      await apiRequest<void>(`/v1/users/${userId}/conversations/${deleteTarget.conversation_id}?expected_version=${deleteTarget.conversation_version}`, { method: "DELETE" });
      const remaining = conversations.filter((item) => item.conversation_id !== deleteTarget.conversation_id);
      setConversations(remaining);
      if (activeConversation?.summary.conversation_id === deleteTarget.conversation_id) {
        if (remaining.length) await openConversation(remaining[0].conversation_id, false);
        else setActiveConversation(undefined);
      }
      setDeleteTarget(undefined);
    } catch (requestError) {
      if (requestError instanceof ConflictError) await refreshList();
      setError(toUserMessage(requestError));
    } finally { setDeleting(false); }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || sending || !activeConversation) return;
    setDraft("");
    setError(undefined);
    setSending(true);

    const payload: ChatRequest = {
      user_id: userId,
      message: content,
      conversation_id: activeConversation.summary.conversation_id,
      expected_conversation_version: activeConversation.summary.conversation_version,
      plan_context: planContext,
      use_current_plan: useCurrentPlan,
      use_history: selectedHistoryIds.length > 0,
      history_plan_ids: selectedHistoryIds,
      use_preferences: usePreferences,
    };
    try {
      const response = await apiRequest<ChatResponse>(
        "/v1/chat",
        { method: "POST", body: JSON.stringify(payload) },
        18_000,
      );
      if (response.conversation_id) await openConversation(response.conversation_id, false);
      await refreshList();
    } catch (requestError) {
      if (requestError instanceof ConflictError) {
        await openConversation(activeConversation.summary.conversation_id, false);
        setError("这段对话已在其他窗口更新，已为你刷新，请重新发送。");
      } else {
        const isTimeout = requestError instanceof DOMException && requestError.name === "AbortError";
        setError(isTimeout ? "等待回复超过 18 秒，请稍后重试。" : toUserMessage(requestError));
      }
      setDraft(content);
    } finally {
      setSending(false);
    }
  }

  function toggleHistory(historyId: string) {
    setSelectedHistoryIds((current) => current.includes(historyId)
      ? current.filter((id) => id !== historyId)
      : current.length < 10 ? [...current, historyId] : current);
  }

  return <div className="conversation-layout">
    <aside className="conversation-sidebar" aria-label="对话列表">
      <div className="conversation-sidebar-heading"><strong>对话记录</strong><button type="button" onClick={() => void createConversation()}>新对话</button></div>
      <div className="conversation-list">
        {conversations.map((item) => <div className={`conversation-list-item ${activeConversation?.summary.conversation_id === item.conversation_id ? "active" : ""}`} key={item.conversation_id}>
          <button className="conversation-open" type="button" onClick={() => void openConversation(item.conversation_id)}><strong>{item.title}</strong><small>{formatShortDate(item.updated_at)} · {item.message_count} 条消息</small></button>
          <button className="conversation-delete" type="button" aria-label={`删除对话：${item.title}`} title="删除对话" onClick={() => setDeleteTarget(item)}><Icon name="trash" size={16}/></button>
        </div>)}
        {!loading && conversations.length === 0 && <p>还没有保存的对话。</p>}
      </div>
    </aside>

    <section className="chat-conversation" aria-live="polite">
      <div className="context-controls" aria-label="本次对话可用上下文">
        <label><input type="checkbox" checked={useCurrentPlan} onChange={(event) => setUseCurrentPlan(event.target.checked)} disabled={!planContext}/>当前计划</label>
        <label><input type="checkbox" checked={usePreferences} onChange={(event) => setUsePreferences(event.target.checked)}/>长期偏好</label>
      </div>

      <details className="history-context-selector" open={selectedHistoryIds.length > 0}>
        <summary>精确选择历史计划（已选 {selectedHistoryIds.length}）</summary>
        {history.length ? <div>{history.map((item) => <label key={item.history_id}>
          <input type="checkbox" checked={selectedHistoryIds.includes(item.history_id)} onChange={() => toggleHistory(item.history_id)}/>
          <span><strong>{new Date(item.adopted_at).toLocaleDateString("zh-CN")}</strong>{item.meals.map((meal) => meal.recipe_title).join("、")}</span>
        </label>)}</div> : <p>还没有可选择的已采用计划。</p>}
      </details>

      <div className="message-list" role="log" aria-label="MealPilot 对话消息" aria-relevant="additions text">
        {loading && <div className="chat-empty"><span className="loading-ring"/><h3>正在恢复对话</h3></div>}
        {!loading && !activeConversation?.messages.length && <div className="chat-empty">
          <span className="chat-empty-mark" aria-hidden="true">M</span><h3>开始一段可以继续的对话</h3>
          <p>刷新或重新打开页面后，这里的消息和回答依据仍会保留。</p>
          <div className="quick-prompts"><button type="button" onClick={() => setDraft("当前晚餐可以替换成什么？")}>晚餐可以换什么？</button><button type="button" onClick={() => setDraft("结合我选中的历史计划，有哪些菜重复了？")}>检查历史重复</button><button type="button" onClick={() => setDraft("怎样在不改变安全约束的情况下缩短制作时间？")}>怎样节省时间？</button></div>
        </div>}
        {activeConversation?.messages.map((message) => <PersistentMessage key={message.message_id} message={message}/>) }
        {sending && <article className="message assistant-message pending-message" role="status"><div className="typing"><i/><i/><i/></div><p>正在读取你明确授权的计划和偏好…</p></article>}
      </div>

      {selectedHistory.length > 0 && <p className="selected-history-note">本次只允许读取这 {selectedHistory.length} 份历史计划；未选记录不会发送给模型。</p>}
      {error && <div className="chat-error" role="alert"><span>{error}</span><button type="button" onClick={() => setError(undefined)}>关闭</button></div>}
      <form className="chat-composer" onSubmit={sendMessage}>
        <textarea value={draft} onChange={(event) => setDraft(event.target.value)} disabled={sending || loading} maxLength={1000} placeholder="输入问题，MealPilot 会保存本次对话，并展示实际使用的上下文证据。"/>
        <div><small>{draft.length}/1000 · 仅面向健康成年人，不提供医疗建议</small><button className="primary" disabled={sending || loading || !draft.trim()}>{sending ? "发送中…" : "发送"}</button></div>
      </form>
    </section>
    <ConfirmDialog open={Boolean(deleteTarget)} title="删除这段对话？" description={`“${deleteTarget?.title ?? ""}”中的全部消息和回答依据都会永久删除，此操作无法撤销。`} busy={deleting} onCancel={() => setDeleteTarget(undefined)} onConfirm={() => void deleteConversation()}/>
  </div>;
}

function PersistentMessage({ message }: { message: ConversationMessage }) {
  if (message.role === "user") return <article className="message user-message"><p>{message.content}</p><small>你</small></article>;
  return <article className="message assistant-message">
    <div className="message-meta"><strong>MealPilot</strong><span className={`source-badge ${message.response_source}`}>{message.response_source === "siliconflow" ? "模型生成" : "本地可靠回复"}</span></div>
    <p>{message.content}</p>
    <div className="answer-evidence-summary"><span>当前计划：{message.current_plan_used ?? "未使用"}</span><span>历史：{message.history_plans_used.length} 份</span><span>偏好：{message.memories_used.length} 条</span></div>
    <details className="memory-evidence"><summary>查看本次回答依据</summary>
      <section><strong>长期偏好</strong>{message.memories_used.length ? <ul>{message.memories_used.map((item, index) => <li key={`${item.category}-${item.value}-${index}`}><span>{categoryLabels[item.category]}</span>{item.value}</li>)}</ul> : <p>未使用长期偏好。</p>}</section>
      <section><strong>历史计划</strong>{message.history_plans_used.length ? <ul>{message.history_plans_used.map((item) => <li key={item.history_id}>{new Date(item.adopted_at).toLocaleDateString("zh-CN")} · {item.original_plan_id}</li>)}</ul> : <p>未使用历史计划。</p>}</section>
    </details>
  </article>;
}
