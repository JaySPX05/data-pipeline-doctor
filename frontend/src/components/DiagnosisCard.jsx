// The diagnosis card: the most important UI element in the demo. Owner: P5.
import VerdictBadge from "./VerdictBadge.jsx";

export default function DiagnosisCard({ diagnosis, title = "Diagnosis" }) {
  if (!diagnosis) return null;
  const pct = Math.round(diagnosis.confidence * 100);
  return (
    <section className="rounded-lg border bg-white p-5">
      <div className="mb-3 flex items-center gap-3">
        <h2 className="font-semibold">{title}</h2>
        <VerdictBadge verdict={diagnosis.verdict} />
        <span className="text-sm text-slate-500">{pct}% confident</span>
        <span
          className={`ml-auto rounded px-2 py-0.5 text-xs ${diagnosis.page_oncall ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700"}`}
        >
          {diagnosis.page_oncall ? "Page on-call" : "Don't page"}
        </span>
      </div>

      <h3 className="text-xs font-medium uppercase text-slate-500">Root cause</h3>
      <p className="mb-3">{diagnosis.root_cause}</p>

      <h3 className="text-xs font-medium uppercase text-slate-500">Suggested fix</h3>
      <p className="mb-4 whitespace-pre-line">{diagnosis.suggested_fix}</p>

      <h3 className="text-xs font-medium uppercase text-slate-500">
        Evidence from memory ({diagnosis.evidence.length})
      </h3>
      {diagnosis.evidence.length === 0 ? (
        <p className="text-sm text-slate-400">No memories used.</p>
      ) : (
        <ul className="mt-1 space-y-2">
          {diagnosis.evidence.map((ev, i) => (
            <li key={ev.memory_id || i} className="rounded border-l-4 border-blue-400 bg-blue-50 px-3 py-2 text-sm">
              {ev.occurred_at && (
                <span className="mr-2 font-mono text-xs text-slate-500">{ev.occurred_at.slice(0, 10)}</span>
              )}
              {ev.text}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-4 text-xs text-slate-400">
        {diagnosis.model} · {diagnosis.latency_ms} ms · memory {diagnosis.memory_used ? "on" : "off"}
      </p>
    </section>
  );
}
