// API client. Set VITE_USE_MOCKS=true to build the UI before the backend is ready (Day 1).
import * as mocks from "../mocks/incidents.js";

export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";
// Full URL ("https://api.example.com") or, on Render, just the backend's host name.
const RAW_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/+$/, "");
const BASE = RAW_BASE && !/^https?:\/\//.test(RAW_BASE) ? `https://${RAW_BASE}` : RAW_BASE;

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json();
}

const delay = (v) => new Promise((r) => setTimeout(() => r(v), 250));

const filterMockIncidents = ({ status, verdict, dag_id } = {}) =>
  mocks.incidents.filter((incident) =>
    (!status || incident.status === status) &&
    (!verdict || incident.diagnosis?.verdict === verdict) &&
    (!dag_id || incident.dag_id === dag_id),
  );

const incidentQuery = ({ status, verdict, dag_id } = {}) => {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (verdict) params.set("verdict", verdict);
  if (dag_id) params.set("dag_id", dag_id);
  const query = params.toString();
  return query ? `/api/incidents?${query}` : "/api/incidents";
};

export const api = {
  listIncidents: (filters = {}) =>
    USE_MOCKS ? delay(filterMockIncidents(filters)) : request(incidentQuery(filters)),
  getIncident: (id) =>
    USE_MOCKS ? delay(mocks.incidents.find((i) => i.id === id)) : request(`/api/incidents/${id}`),
  sendFeedback: (id, body) =>
    USE_MOCKS
      ? delay({ ...mocks.incidents.find((i) => i.id === id), status: "resolved", feedback: body })
      : request(`/api/incidents/${id}/feedback`, { method: "POST", body: JSON.stringify(body) }),
  rediagnose: (id, memory) =>
    USE_MOCKS ? delay(mocks.memoryOffDiagnosis) : request(`/api/incidents/${id}/rediagnose?memory=${memory}`, { method: "POST" }),
  listPipelines: () => (USE_MOCKS ? delay(null) : request("/api/pipelines")),
  metricsSummary: () => (USE_MOCKS ? delay(null) : request("/api/metrics/summary")),
  learningCurve: () => (USE_MOCKS ? delay(mocks.learningCurve) : request("/api/metrics/learning-curve")),
  reflect: () => (USE_MOCKS ? delay({ text: mocks.healthBrief }) : request("/api/reflect", { method: "POST", body: "{}" })),
  searchMemory: (q) =>
    USE_MOCKS ? delay(mocks.searchMemories(q)) : request(`/api/memory/search?q=${encodeURIComponent(q)}`),
  memoryStatus: () =>
    USE_MOCKS ? delay({ mock: true }) : request("/api/memory/status"),
  health: () => (USE_MOCKS ? delay({ status: "ok" }) : request("/api/health")),
};
