import VerdictBadge from "./VerdictBadge.jsx";

export default function DiagnosisCard({ diagnosis, title = "Diagnosis" }) {
  if (!diagnosis) return null;
  const pct = Math.round(diagnosis.confidence * 100);
  return (
    <section className="panel panel-pad">
      <div className="diagnosis-heading">
        <div><div className="section-label" style={{ marginBottom: 5 }}>{title}</div><h2 className="panel-title">AI diagnosis <span className="badge badge-blue" style={{ marginLeft: 5 }}>Memory {diagnosis.memory_used ? "enriched" : "off"}</span></h2></div>
        <div className="diagnosis-confidence">{pct}% confidence</div>
      </div>
      <div className="diagnosis-meta"><VerdictBadge verdict={diagnosis.verdict} /><span className={diagnosis.page_oncall ? "page-oncall" : "no-page"}>{diagnosis.page_oncall ? "Page on-call" : "Don't page"}</span></div>

      <div className="diagnosis-section">
        <h3 className="section-label">Likely root cause</h3>
        <p>{diagnosis.root_cause}</p>
      </div>
      <div className="diagnosis-section">
        <h3 className="section-label">Suggested fix</h3>
        <p className="preserve-lines">{diagnosis.suggested_fix}</p>
      </div>

      <div className="evidence-section">
        <h3 className="section-label">Evidence from memory <span className="muted" style={{ fontWeight: 400 }}>({diagnosis.evidence.length})</span></h3>
        {diagnosis.evidence.length === 0 ? <p className="muted" style={{ fontSize: 10 }}>No memories used.</p> : (
          <ul className="evidence-list">{diagnosis.evidence.map((ev, i) => <li className="evidence-item" key={ev.memory_id || i}>{ev.occurred_at && <span className="mono muted" style={{ marginRight: 8 }}>{ev.occurred_at.slice(0, 10)}</span>}{ev.text}</li>)}</ul>
        )}
      </div>
      <p className="diagnosis-foot">{diagnosis.model} · {diagnosis.latency_ms} ms · memory {diagnosis.memory_used ? "on" : "off"}</p>
    </section>
  );
}
