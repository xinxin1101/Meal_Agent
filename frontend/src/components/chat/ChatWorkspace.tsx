import { useState } from "react";
import type { AdoptedMealPlan, ChatPlanContext, PreferenceMemoryItem } from "../../api/types";
import { PreferenceMemoryPanel } from "../memory/PreferenceMemoryPanel";
import { ChatPanel } from "./ChatPanel";

interface ChatWorkspaceProps {
  userId: string;
  planContext?: ChatPlanContext;
  memoryCount: number;
  history: AdoptedMealPlan[];
  initialHistoryPlanIds: string[];
  onMemoryChange: (items: PreferenceMemoryItem[]) => void;
}

export function ChatWorkspace({ userId, planContext, memoryCount, history, initialHistoryPlanIds, onMemoryChange }: ChatWorkspaceProps) {
  const [memoryOpen, setMemoryOpen] = useState(false);
  return <section className="chat-workspace card">
    <header className="workspace-header"><div><span className={`context-status ${planContext ? "linked" : ""}`}>{planContext ? "已关联当前验证计划" : "当前没有关联计划"}</span>{planContext && <strong>{planContext.totals.energy_kcal} kcal · 蛋白质 {planContext.totals.protein_g} g · {planContext.totals.prep_minutes} 分钟</strong>}</div><button className="memory-trigger" type="button" onClick={() => setMemoryOpen(true)} aria-expanded={memoryOpen} aria-controls="preference-memory-panel"><span>查看偏好证据</span><b aria-label={`${memoryCount} 条偏好`}>{memoryCount}</b></button></header>
    <ChatPanel userId={userId} planContext={planContext} history={history} initialHistoryPlanIds={initialHistoryPlanIds} />
    <PreferenceMemoryPanel userId={userId} open={memoryOpen} onClose={() => setMemoryOpen(false)} onChange={onMemoryChange} />
  </section>;
}
