import { useEffect, useRef, useState } from "react";
import { API_BASE, apiClient } from "../api/client";
import type { DurableRunSnapshot, RunEvent } from "../api/types";

export type EventConnectionState = "idle" | "connecting" | "live" | "reconnecting" | "complete";

export function useRunEvents(runId: string | undefined, runVersion: number | undefined, runStatus: string | undefined, onSnapshot: (snapshot: DurableRunSnapshot) => void) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connectionState, setConnectionState] = useState<EventConnectionState>("idle");
  const lastEventId = useRef(0);
  const snapshotHandler = useRef(onSnapshot);
  snapshotHandler.current = onSnapshot;

  useEffect(() => { lastEventId.current = 0; setEvents([]); }, [runId]);

  useEffect(() => {
    if (!runId) { setConnectionState("idle"); return; }
    let disposed = false;
    let source: EventSource | undefined;
    let reconnectTimer: number | undefined;

    const refreshSnapshot = async () => {
      try { snapshotHandler.current(await apiClient.get<DurableRunSnapshot>(`/v1/runs/${runId}`)); } catch { /* The visible connection state remains authoritative. */ }
    };

    const connect = () => {
      if (disposed) return;
      setConnectionState(lastEventId.current ? "reconnecting" : "connecting");
      source = new EventSource(`${API_BASE}/v1/runs/${runId}/events?last_event_id=${lastEventId.current}`);
      source.onopen = () => setConnectionState("live");
      source.onmessage = (message) => {
        try {
          const event = JSON.parse(message.data) as RunEvent;
          lastEventId.current = Math.max(lastEventId.current, event.event_id);
          setEvents((current) => current.some((item) => item.event_id === event.event_id) ? current : [...current, event]);
          if (["interrupt", "completed", "failed", "cancelled"].includes(event.event_type)) {
            void refreshSnapshot();
            source?.close();
            setConnectionState("complete");
          }
        } catch { /* Ignore malformed untrusted event data. */ }
      };
      source.onerror = () => {
        source?.close();
        if (disposed) return;
        void refreshSnapshot();
        if (["PAUSED", "COMPLETED", "FAILED", "CANCELLED"].includes(runStatus ?? "")) { setConnectionState("complete"); return; }
        setConnectionState("reconnecting");
        reconnectTimer = window.setTimeout(connect, 1500);
      };
    };

    connect();
    return () => { disposed = true; source?.close(); if (reconnectTimer) window.clearTimeout(reconnectTimer); };
  }, [runId, runVersion]);

  return { events, connectionState, lastEventId: lastEventId.current };
}
