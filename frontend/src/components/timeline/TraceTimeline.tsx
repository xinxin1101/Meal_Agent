import type { RunEvent } from "../../api/types";

const nodes = [
  { id: "analyze", label: "理解需求" },
  { id: "retrieve", label: "检索菜谱" },
  { id: "solve", label: "组合方案" },
  { id: "validate", label: "安全复核" },
  { id: "finalize", label: "完成" },
] as const;

export function TraceTimeline({ events, terminal }: { events: RunEvent[]; terminal: boolean }) {
  const seen = new Map(events.filter((event) => nodes.some((node) => node.id === event.event_type)).map((event) => [event.event_type, event]));
  const firstPending = nodes.findIndex((node) => !seen.has(node.id));
  return <section className="trace" aria-label="规划执行进度">
    <ol>{nodes.map((node, index) => {
      const event = seen.get(node.id);
      const state = event ? "done" : !terminal && index === firstPending ? "active" : terminal ? "skipped" : "pending";
      return <li className={state} key={node.id} aria-current={state === "active" ? "step" : undefined}><span className="trace-dot" aria-hidden="true" /><div><b>{node.label}</b><small>{event?.payload.outcome ?? (state === "active" ? "正在执行" : state === "skipped" ? "未执行" : "等待")}</small></div></li>;
    })}</ol>
  </section>;
}
