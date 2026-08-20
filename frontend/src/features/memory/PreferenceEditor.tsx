import { FormEvent, useEffect, useState } from "react";
import { apiClient } from "../../api/client";
import { toUserMessage } from "../../api/errors";
import type { PreferenceMemory, PreferenceMemoryItem } from "../../api/types";
import { Icon } from "../../components/ui/Icon";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";

const categoryLabels: Record<PreferenceMemoryItem["category"], string> = { food_preference: "口味偏好", avoidance: "主动忌口", cooking_style: "烹饪偏好" };

export function PreferenceEditor({ userId, items, onChange }: { userId: string; items: PreferenceMemoryItem[]; onChange: (items: PreferenceMemoryItem[]) => void }) {
  const [draftItems, setDraftItems] = useState(items);
  const [category, setCategory] = useState<PreferenceMemoryItem["category"]>("food_preference");
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string>();
  const [error, setError] = useState<string>();
  const [clearOpen, setClearOpen] = useState(false);
  useEffect(() => setDraftItems(items), [items]);
  function add(event: FormEvent) {
    event.preventDefault(); const next = value.trim(); if (!next) return;
    if (draftItems.some((item) => item.category === category && item.value === next)) { setError("这条偏好已经存在。"); return; }
    setDraftItems((current) => [...current, { category, value: next }]); setValue(""); setNotice("有未保存的更改。"); setError(undefined);
  }
  async function save() { setSaving(true); setError(undefined); try { const memory = await apiClient.put<PreferenceMemory>(`/v1/memory/${userId}`, { user_id: userId, items: draftItems }); setDraftItems(memory.items); onChange(memory.items); setNotice("偏好已保存，后续规划和对话可以使用这些记录。"); } catch (requestError) { setError(toUserMessage(requestError)); } finally { setSaving(false); } }
  async function clearAll() { setSaving(true); try { await apiClient.delete(`/v1/memory/${userId}`); setDraftItems([]); onChange([]); setNotice("全部偏好记忆已删除。"); setClearOpen(false); } catch (requestError) { setError(toUserMessage(requestError)); } finally { setSaving(false); } }
  return <section className="preference-editor card">
    <header><div><p className="eyebrow">用户主动保存</p><h2>长期饮食偏好</h2><p>只保存你明确填写的内容，不从行为推断过敏、疾病或健康状态。</p></div><span className="memory-total"><Icon name="memory" size={18}/>{draftItems.length} 条</span></header>
    <form className="preference-form" onSubmit={add}><label><span>分类</span><select value={category} onChange={(event) => setCategory(event.target.value as PreferenceMemoryItem["category"])}>{Object.entries(categoryLabels).map(([key,label]) => <option key={key} value={key}>{label}</option>)}</select></label><label><span>偏好内容</span><input value={value} maxLength={120} onChange={(event) => setValue(event.target.value)} placeholder="例如：偏爱清淡少油" /></label><button className="button button-secondary" disabled={!value.trim()}>添加</button></form>
    <div className="preference-list">{draftItems.length === 0 ? <div className="preference-empty"><Icon name="memory" size={28}/><strong>尚未保存长期偏好</strong><p>可以添加口味、主动忌口和烹饪偏好。</p></div> : draftItems.map((item,index) => <article key={`${item.category}-${item.value}-${index}`}><div><small>{categoryLabels[item.category]}</small><strong>{item.value}</strong></div><button type="button" onClick={() => { setDraftItems((current) => current.filter((_, itemIndex) => itemIndex !== index)); setNotice("有未保存的更改。"); }}>移除</button></article>)}</div>
    {notice && <p className="notice success">{notice}</p>}{error && <p className="notice error" role="alert">{error}</p>}
    <footer><button className="button button-danger" type="button" onClick={() => setClearOpen(true)} disabled={saving || !draftItems.length}>删除全部</button><button className="button button-primary" type="button" onClick={() => void save()} disabled={saving}>{saving ? "保存中…" : "保存偏好"}</button></footer><ConfirmDialog open={clearOpen} title="删除全部长期偏好？" description="所有主动保存的口味、忌口和烹饪偏好都会永久删除，后续规划和对话将不再使用它们。" busy={saving} onCancel={() => setClearOpen(false)} onConfirm={() => void clearAll()}/>
  </section>;
}
