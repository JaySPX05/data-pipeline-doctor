// API client. Set VITE_USE_MOCKS=true to build the UI before the backend is ready (Day 1).
import * as mocks from "../mocks/incidents.js";

export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === "true";
const BASE = import.meta.env.VITE_API_URL || "";

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${await res.text()}`);
  return res.json();
}

const delay = (v) => new Promise((r) => setTimeout(() => r(v), 250));

export const api = {
  listIncidents: () => (USE_MOCKS ? delay(mocks.incidents) : request("/api/incidents")),
  getIncident: (id) =>
    USE_MOCKS ? delay(mocks.incidents.find((i) => i.id === id)) : request(`/api/incidents/${id}`),
  sendFeedback: (id, body) =>
    USE_MOCKS
      ? delay({ ...mocks.incidents.find((i) => i.id === id), status: "resolved", feedback: body })
      : request(`/api/incidents/${id}/feedback`, { method: "POST", body: JSON.stringify(body) }),
  rediagnose: (id, memory) =>
    USE_MOCKS ? delay(mocks.memoryOffDiagnosis) : request(`/api/incidents/${id}/rediagnose?memory=${memory}`, { method: "POST" }),
  learningCurve: () => (USE_MOCKS ? delay(mocks.learningCurve) : request("/api/metrics/learning-curve")),
  reflect: () => (USE_MOCKS ? delay({ text: mocks.healthBrief }) : request("/api/reflect", { method: "POST", body: "{}" })),
  searchMemory: (q) =>
    USE_MOCKS ? delay(mocks.memories) : request(`/api/memory/search?q=${encodeURIComponent(q)}`),
  health: () => (USE_MOCKS ? delay({ status: "ok" }) : request("/api/health")),
};
