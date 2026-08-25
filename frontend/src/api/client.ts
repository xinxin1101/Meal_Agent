import { ApiError, ConflictError, ValidationError } from "./errors";
import type { AdoptedMealPlan, AgentPlanningCommand, DurableRunSnapshot, HistoryCollection, PlanFeedback, RunDecisionCommand, SavePlanFeedbackCommand } from "./types";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
let accessToken: string | undefined;
let refreshInFlight: Promise<boolean> | undefined;

export function setAccessToken(token?: string) { accessToken = token; }

async function refreshAccessToken(): Promise<boolean> {
  if (!refreshInFlight) refreshInFlight = fetch(`${API_BASE}/v1/auth/refresh`, { method: "POST", credentials: "include" })
    .then(async (response) => {
      if (!response.ok) return false;
      const session = await response.json() as { access_token: string };
      accessToken = session.access_token;
      return true;
    }).catch(() => false).finally(() => { refreshInFlight = undefined; });
  return refreshInFlight;
}

function needsIdempotency(method: string, path: string): boolean {
  if (method === "POST" && (path === "/v1/admin/reviews/batch-publish" || path === "/v1/admin/reviews/batch-publish-readable")) return true;
  if (method === "POST" && (path === "/v1/admin/acquisition/jobs" || /^\/v1\/admin\/acquisition\/jobs\/[^/]+\/cancel$/.test(path))) return true;
  if (method === "POST" && /^\/v1\/admin\/llm-review-jobs\/[^/]+\/cancel$/.test(path)) return true;
  if (["POST", "PUT"].includes(method) && /^\/v1\/admin\/reviews\/[^/]+\/(assist|curation|evidence|approve|publish|revoke)$/.test(path)) return true;
  return (method === "POST" && (path === "/v1/runs" || path === "/v1/chat" || /^\/v1\/runs\/[^/]+\/(decisions|cancel)$/.test(path) || /^\/v1\/history\/[^/]+\/adoptions$/.test(path) || /^\/v1\/users\/[^/]+\/conversations$/.test(path)))
    || (method === "PUT" && /^\/v1\/feedback\/[^/]+\/[^/]+$/.test(path))
    || (method === "DELETE" && (/^\/v1\/history\/[^/]+(?:\/[^/?]+)?/.test(path) || /^\/v1\/users\/[^/]+\/conversations\/[^/?]+/.test(path)));
}

export async function apiRequest<T>(path: string, init: RequestInit = {}, timeoutMs = 15_000): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (accessToken && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${accessToken}`);
  if (needsIdempotency(method, path) && !headers.has("Idempotency-Key")) headers.set("Idempotency-Key", crypto.randomUUID());
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response = await fetch(`${API_BASE}${path}`, { ...init, method, headers, credentials: "include", signal: init.signal ?? controller.signal });
    if (response.status === 401 && !path.startsWith("/v1/auth/") && await refreshAccessToken()) {
      if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
      response = await fetch(`${API_BASE}${path}`, { ...init, method, headers, credentials: "include", signal: init.signal ?? controller.signal });
    }
    const detail = response.status === 204 ? undefined : await response.json().catch(() => undefined);
    if (!response.ok) {
      if (response.status === 409) throw new ConflictError(detail);
      if (response.status === 422) throw new ValidationError(detail);
      throw new ApiError(response.status, `请求失败（HTTP ${response.status}）。`, detail);
    }
    return detail as T;
  } finally { window.clearTimeout(timeout); }
}

export const apiClient = {
  get: <T>(path: string) => apiRequest<T>(path),
  post: <T>(path: string, body: unknown, timeoutMs?: number) => apiRequest<T>(path, { method: "POST", body: JSON.stringify(body) }, timeoutMs),
  put: <T>(path: string, body: unknown) => apiRequest<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  delete: (path: string) => apiRequest<void>(path, { method: "DELETE" }),
  createRun: (command: AgentPlanningCommand) => apiRequest<DurableRunSnapshot>("/v1/runs", { method: "POST", body: JSON.stringify(command) }),
  decideRun: (runId: string, decision: RunDecisionCommand) => apiRequest<DurableRunSnapshot>(`/v1/runs/${runId}/decisions`, { method: "POST", body: JSON.stringify(decision) }),
  cancelRun: (runId: string, expectedRunVersion: number) => apiRequest<DurableRunSnapshot>(`/v1/runs/${runId}/cancel`, { method: "POST", body: JSON.stringify({ expected_run_version: expectedRunVersion }) }),
  adoptPlan: (userId: string, runId: string, expectedRunVersion: number) => apiRequest<AdoptedMealPlan>(`/v1/history/${userId}/adoptions`, { method: "POST", body: JSON.stringify({ run_id: runId, expected_run_version: expectedRunVersion }) }),
  deleteHistory: (userId: string, historyId: string, expectedVersion: number) => apiRequest<HistoryCollection>(`/v1/history/${userId}/${historyId}?expected_version=${expectedVersion}`, { method: "DELETE" }),
  clearHistory: (userId: string, expectedVersion: number) => apiRequest<HistoryCollection>(`/v1/history/${userId}?expected_version=${expectedVersion}`, { method: "DELETE" }),
  saveFeedback: (userId: string, historyId: string, command: SavePlanFeedbackCommand) => apiRequest<PlanFeedback>(`/v1/feedback/${userId}/${historyId}`, { method: "PUT", body: JSON.stringify(command) }),
};
