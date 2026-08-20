import { FormEvent, useEffect, useRef, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import type { PreferenceMemory, PreferenceMemoryItem } from "../../api/types";
import { ConfirmDialog } from "../ui/ConfirmDialog";

const categoryLabels: Record<PreferenceMemoryItem["category"], string> = {
  food_preference: "口味偏好",
  avoidance: "主动忌口",
  cooking_style: "烹饪偏好",
};

interface PreferenceMemoryPanelProps {
  userId: string;
  open: boolean;
  onClose: () => void;
  onChange: (items: PreferenceMemoryItem[]) => void;
}

export function PreferenceMemoryPanel({ userId, open, onClose, onChange }: PreferenceMemoryPanelProps) {
  const [items, setItems] = useState<PreferenceMemoryItem[]>([]);
  const [category, setCategory] = useState<PreferenceMemoryItem["category"]>("food_preference");
  const [value, setValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string>();
  const [error, setError] = useState<string>();
  const [clearOpen, setClearOpen] = useState(false);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : undefined;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    closeButton.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
      previousFocus?.focus();
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(undefined);
    void apiClient.get<PreferenceMemory>(`/v1/memory/${userId}`)
      .then((memory) => { setItems(memory.items); onChange(memory.items); })
      .catch((requestError) => setError(toUserMessage(requestError)))
      .finally(() => setLoading(false));
  }, [open, userId, onChange]);

  function addItem(event: FormEvent) {
    event.preventDefault();
    const nextValue = value.trim();
    if (!nextValue) return;
    if (items.some((item) => item.category === category && item.value === nextValue)) {
      setError("这条偏好已经存在。");
      return;
    }
    if (items.length >= 50) { setError("最多保存 50 条偏好。"); return; }
    setItems((current) => [...current, { category, value: nextValue }]);
    setValue("");
    setError(undefined);
    setNotice("有未保存的更改。");
  }

  async function save() {
    setSaving(true);
    setError(undefined);
    try {
      const memory = await apiClient.put<PreferenceMemory>(`/v1/memory/${userId}`, { user_id: userId, items });
      setItems(memory.items);
      onChange(memory.items);
      setNotice("偏好已保存，后续规划和对话可以显式使用这些记录。");
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setSaving(false); }
  }

  async function clearAll() {
    setSaving(true);
    setError(undefined);
    try {
      await apiClient.delete(`/v1/memory/${userId}`);
      setItems([]);
      onChange([]);
      setNotice("全部偏好记忆已删除。");
      setClearOpen(false);
    } catch (requestError) { setError(toUserMessage(requestError)); }
    finally { setSaving(false); }
  }

  if (!open) return null;
  return <div className="memory-overlay" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside id="preference-memory-panel" className="memory-drawer" role="dialog" aria-modal="true" aria-labelledby="memory-title" aria-describedby="memory-boundary">
      <header><div><p className="eyebrow">用户可控的本地记忆</p><h2 id="memory-title">偏好管理</h2></div><button ref={closeButton} className="icon-button" type="button" onClick={onClose} aria-label="关闭偏好管理">×</button></header>
      <p id="memory-boundary" className="memory-boundary">这里只保存你主动填写的偏好。MealPilot 不会从行为推断过敏史、疾病或其他健康状态；过敏原仍须在规划档案中明确填写。</p>
      <form className="memory-add-form" onSubmit={addItem}>
        <label>分类<select value={category} onChange={(event) => setCategory(event.target.value as PreferenceMemoryItem["category"])}>{Object.entries(categoryLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label>内容<input maxLength={120} value={value} onChange={(event) => setValue(event.target.value)} placeholder="例如：偏爱清淡少油" /></label>
        <button className="button button-secondary" disabled={!value.trim()}>添加</button>
      </form>
      <div className="memory-list">
        {loading && <p>正在读取偏好…</p>}
        {!loading && !items.length && <div className="memory-empty"><span>◎</span><p>尚未保存偏好。</p></div>}
        {items.map((item, index) => <article key={`${item.category}-${item.value}-${index}`}>
          <div><small>{categoryLabels[item.category]}</small><strong>{item.value}</strong></div>
          <button type="button" onClick={() => { setItems((current) => current.filter((_, itemIndex) => itemIndex !== index)); setNotice("有未保存的更改。"); }} aria-label={`移除${item.value}`}>移除</button>
        </article>)}
      </div>
      {notice && <p className="memory-notice">{notice}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <footer><button className="danger-button" type="button" onClick={() => setClearOpen(true)} disabled={saving || !items.length}>删除全部</button><button className="button button-primary" type="button" onClick={() => void save()} disabled={saving}>{saving ? "保存中…" : "保存更改"}</button></footer><ConfirmDialog open={clearOpen} title="删除全部长期偏好？" description="所有主动保存的偏好都会永久删除，后续规划和对话将不再使用它们。" busy={saving} onCancel={() => setClearOpen(false)} onConfirm={() => void clearAll()}/>
    </aside>
  </div>;
}
