import type { DurableRunSnapshot } from "../../api/types";
import { useRunEvents } from "../../hooks/useRunEvents";
import { TraceTimeline } from "../timeline/TraceTimeline";
import { Icon } from "../ui/Icon";

const connectionLabels = { idle: "等待任务", connecting: "正在同步进度", live: "实时进度已连接", reconnecting: "连接恢复中，正在同步最新状态", complete: "运行记录已同步" };

export function PlanningDashboard({ runId, runVersion, status, onSnapshot }: { runId?: string; runVersion?: number; status?: string; onSnapshot: (snapshot: DurableRunSnapshot) => void }) {
  const { events, connectionState, lastEventId } = useRunEvents(runId, runVersion, status, onSnapshot);
  if (!runId || !status) return null;
  const terminal = ["PAUSED", "COMPLETED", "FAILED", "CANCELLED"].includes(status);
  return <div className="dashboard-observability" aria-label="规划任务实时状态">
    <TraceTimeline events={events} terminal={terminal} />
    <details className="technical-details"><summary><Icon name="settings" size={16} />技术详情</summary><div className={`connection ${connectionState}`} role="status" aria-live="polite"><span aria-hidden="true" />{connectionLabels[connectionState]}{lastEventId > 0 && <small>最后事件 #{lastEventId}</small>}</div><p>Run {runId} · 版本 {runVersion}</p></details>
  </div>;
}
