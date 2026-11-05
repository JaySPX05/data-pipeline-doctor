// Incident detail: diagnosis card, memory ON/OFF comparison, feedback form. Owners: P5 (card, form), P6 (comparison).
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client.js";
import DiagnosisCard from "../components/DiagnosisCard.jsx";

function FeedbackForm({ incident, onSaved }) {
  const dx = incident.diagnosis;
  const [form, setForm] = useState({
    correct: true,
    root_cause: dx?.root_cause || "",
    fix: dx?.suggested_fix || "",
    verdict: dx?.verdict || "NEW",
    minutes_to_fix: 10,
  });
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    onSaved(await api.sendFeedback(incident.id, { ...form, minutes_to_fix: Number(form.minutes_to_fix) }));
  };

  if (incident.feedback) {
    return <p className="rounded-lg border bg-green-50 p-4 text-sm text-green-800">Resolved. The agent has learned from this incident.</p>;
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border bg-white p-5 text-sm">
      <h2 className="font-semibold">Resolve and teach the agent</h2>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={form.correct} onChange={set("correct")} /> The diagnosis was correct
      </label>
      <label className="block">Actual root cause
        <textarea className="mt-1 w-full rounded border p-2" rows={2} value={form.root_cause} onChange={set("root_cause")} />
      </label>
      <label className="block">Fix applied
        <textarea className="mt-1 w-full rounded border p-2" rows={2} value={form.fix} onChange={set("fix")} />
      </label>
      <div className="flex gap-4">
        <label>Verdict{" "}
          <select className="rounded border p-1" value={form.verdict} onChange={set("verdict")}>
            {["KNOWN_ISSUE", "FLAKY", "UPSTREAM_CHANGE", "NEW"].map((v) => <option key={v}>{v}</option>)}
          </select>
        </label>
        <label>Minutes to fix{" "}
          <input type="number" className="w-20 rounded border p-1" value={form.minutes_to_fix} onChange={set("minutes_to_fix")} />
        </label>
      </div>
      <button className="rounded bg-slate-900 px-4 py-2 text-white">Save to memory</button>
    </form>
  );
}

export default function Incident() {
  const { id } = useParams();
  const [incident, setIncident] = useState(null);
  const [withoutMemory, setWithoutMemory] = useState(null);
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    api.getIncident(id).then(setIncident);
  }, [id]);

  const compare = async () => {
    setComparing(true);
    setWithoutMemory(await api.rediagnose(id, false));
    setComparing(false);
  };

  if (!incident) return <p className="text-slate-500">Loading...</p>;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-semibold">{incident.dag_id}.{incident.task_id}</h1>
        <p className="text-sm text-slate-500">
          {incident.occurred_at.replace("T", " ").slice(0, 19)} · {incident.error_class} · {incident.fingerprint}
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button onClick={compare} disabled={comparing} className="rounded border bg-white px-3 py-1.5 text-sm hover:bg-slate-100">
          {comparing ? "Running without memory..." : "Compare: memory OFF vs ON"}
        </button>
      </div>

      <div className={withoutMemory ? "grid gap-4 md:grid-cols-2" : ""}>
        {withoutMemory && <DiagnosisCard diagnosis={withoutMemory} title="Memory OFF" />}
        <DiagnosisCard diagnosis={incident.diagnosis} title={withoutMemory ? "Memory ON" : "Diagnosis"} />
      </div>

      <FeedbackForm incident={incident} onSaved={setIncident} />

      <details className="rounded-lg border bg-white p-4">
        <summary className="cursor-pointer text-sm font-medium">Log tail</summary>
        <pre className="mt-3 overflow-x-auto rounded bg-slate-900 p-3 text-xs text-slate-100">{incident.log_tail}</pre>
      </details>
    </div>
  );
}
