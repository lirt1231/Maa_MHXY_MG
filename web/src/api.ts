import type { Metrics, Run, RunSummary, SystemState, Task } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) }
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败（${response.status}）`);
  }
  return response.json();
}

export const api = {
  system: () => request<SystemState>("/api/system"),
  tasks: () => request<{ items: Task[] }>("/api/tasks"),
  runs: () => request<{ items: Run[]; summary: RunSummary }>("/api/runs"),
  metrics: (metric: "gold" | "experience") => request<Metrics>(`/api/metrics?range=7d&metric=${metric}`),
  start: (taskId: string, options: Record<string, unknown>) =>
    request<Run>("/api/runs", { method: "POST", body: JSON.stringify({ taskId, options, checklistConfirmed: true }) }),
  stop: (runId: string) => request<Run>(`/api/runs/${runId}/stop`, { method: "POST" }),
  capture: () => request("/api/metrics/capture", { method: "POST" }),
  startGame: () => request<SystemState>("/api/game/start", { method: "POST" }),
  correctMetric: (id: number, values: Record<string, number | null>) =>
    request(`/api/metrics/${id}`, { method: "PATCH", body: JSON.stringify(values) })
};
