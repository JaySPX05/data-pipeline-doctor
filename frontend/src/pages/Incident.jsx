// Incident detail: diagnosis, memory comparison, feedback form and log tail.
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client.js";
import DiagnosisCard from "../components/DiagnosisCard.jsx";
import VerdictBadge from "../components/VerdictBadge.jsx";

function FeedbackForm({ incident, onSaved }) {
  const dx = incident.diagnosis;
  const [form, setForm] = useState({
    correct: true,
    root_cause: dx?.root_cause || "",
    fix: dx?.suggested_fix || "",
    verdict: dx?.verdict || "NEW",
    minutes_to_fix: 10,
  });
  const [saveError, setSaveError] = useState(null);
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.type === "checkbox" ? event.target.checked : event.target.value });

  const submit = async (event) => {
    event.preventDefault();
    setSaveError(null);
    try {
      onSaved(await api.sendFeedback(incident.id, { ...form, minutes_to_fix: Number(form.minutes_to_fix) }));
    } catch (error) {
      setSaveError(error.message || "Could not save feedback.");
    }
  };

  if (incident.feedback) return <p className="resolved-callout">Resolved. The agent has learned from this incident.</p>;

  return (
    <form onSubmit={submit} className="panel panel-pad feedback-form">
      <h2 className="panel-title">Resolve and teach the agent</h2>
      <label className="checkbox-label"><input type="checkbox" checked={form.correct} onChange={set("correct")} /> The diagnosis was correct</label>
      <label>Actual root cause<textarea rows={2} value={form.root_cause} onChange={set("root_cause")} /></label>
      <label>Fix applied<textarea rows={2} value={form.fix} onChange={set("fix")} /></label>
      <div className="form-inline">
        <label>Verdict <select value={form.verdict} onChange={set("verdict")}>{["KNOWN_ISSUE", "FLAKY", "UPSTREAM_CHANGE", "NEW"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Minutes to fix <input type="number" value={form.minutes_to_fix} onChange={set("minutes_to_fix")} /></label>
      </div>
      <div><button className="button-primary">Save to memory</button></div>
      {saveError && <p className="inline-error" role="alert">Could not save feedback: {saveError}</p>}
    </form>
  );
}

export default function Incident() {
  const { id } = useParams();
  const [incident, setIncident] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [relatedIncidents, setRelatedIncidents] = useState([]);
  const [withoutMemory, setWithoutMemory] = useState(null);
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState(null);

  useEffect(() => {
    let active = true;
    setIncident(null);
    setLoadError(null);
    api.getIncident(id).then((record) => { if (active) { if (record) setIncident(record); else setLoadError("Incident was not found in the current data source."); } }).catch((error) => { if (active) setLoadError(error.message || "Could not load this incident."); });
    api.listIncidents().then((records) => { if (active) setRelatedIncidents(records || []); }).catch(() => {});
    return () => { active = false; };
  }, [id]);

  const compare = async () => {
    setComparing(true);
    setCompareError(null);
    try { setWithoutMemory(await api.rediagnose(id, false)); }
    catch (error) { setCompareError(error.message || "Could not run the memory comparison."); }
    finally { setComparing(false); }
  };

  if (loadError) return <div className="panel panel-pad" role="alert"><p className="inline-error">Could not load incident: {loadError}</p><Link to="/" className="table-link">Return to Incident Inbox</Link></div>;
  if (!incident) return <p className="muted">Loading...</p>;

  return (
    <div>
      <div className="breadcrumb"><Link to="/" className="table-link">Incident Inbox</Link><span>›</span><span className="mono">{incident.id}</span></div>
      <div className="page-heading incident-page-heading">
        <div><div className="incident-tags"><span className="mono muted">{incident.id}</span><VerdictBadge verdict={incident.diagnosis?.verdict} /><span className="badge badge-blue">{incident.status}</span></div>
          <h1>{incident.dag_id}.{incident.task_id}</h1>
          <p>{incident.occurred_at.replace("T", " ").slice(0, 19)} · {incident.error_class} · <span className="mono">{incident.fingerprint}</span></p>
        </div>
        <button onClick={compare} disabled={comparing} className="button-secondary">{comparing ? "Running without memory..." : "Compare: memory OFF vs ON"}</button>
      </div>
      {compareError && <p className="inline-error" role="alert">{compareError}</p>}

      <section className="panel incident-meta-grid">
        <div><span>DAG</span><strong>{incident.dag_id}</strong></div>
        <div><span>Task</span><strong>{incident.task_id}</strong></div>
        <div><span>Run</span><strong className="mono">{incident.run_id}</strong></div>
        <div><span>Attempt</span><strong>{incident.try_number}</strong></div>
      </section>

      <div className={withoutMemory ? "diagnosis-grid" : "diagnosis-grid single"} style={{ marginTop: 14 }}>
        {withoutMemory && <DiagnosisCard diagnosis={withoutMemory} title="Memory OFF" />}
        <DiagnosisCard diagnosis={incident.diagnosis} title={withoutMemory ? "Memory ON" : "Diagnosis"} />
      </div>

      <div className="incident-lower-grid" style={{ marginTop: 14 }}>
        <FeedbackForm incident={incident} onSaved={setIncident} />
        <details className="panel log-details">
          <summary>Log tail <span className="muted">· incident output</span></summary>
          <pre className="code-panel">{incident.log_tail}</pre>
        </details>
      </div>

      <div className="incident-context-grid" style={{ marginTop: 14 }}>
        <section className="panel panel-pad">
          <div className="panel-heading-row"><div><h2 className="panel-title">Similar previous incidents</h2><p className="panel-subtitle">Matches by available DAG ID or fingerprint.</p></div><span className="muted">{relatedIncidents.filter((item) => item.id !== incident.id && (item.dag_id === incident.dag_id || item.fingerprint === incident.fingerprint)).length} matches</span></div>
          {relatedIncidents.some((item) => item.id !== incident.id && (item.dag_id === incident.dag_id || item.fingerprint === incident.fingerprint)) ? <ul className="related-list">{relatedIncidents.filter((item) => item.id !== incident.id && (item.dag_id === incident.dag_id || item.fingerprint === incident.fingerprint)).slice(0, 5).map((item) => <li key={item.id}><div><Link className="table-link mono" to={`/incidents/${item.id}`}>{item.id}</Link><strong>{item.dag_id}.{item.task_id}</strong><small>{item.error_class} · {item.occurred_at?.slice(0, 10) || "date unavailable"}</small></div><span className={item.feedback ? "status-diagnosed" : "status-pending"}>{item.feedback ? "Feedback recorded" : item.diagnosis ? "Diagnosed" : "Pending"}</span></li>)}</ul> : <div className="unavailable-state">No matching incident records were returned.</div>}
          <div className="subsection-label">Historical validation</div>
          {incident.feedback ? <p className="validation-note">Feedback is recorded for this incident. The API does not provide a separate historical validation score.</p> : <p className="unavailable-state">No feedback or historical validation record is available for this incident.</p>}
        </section>
        <div className="stack">
          <section className="panel panel-pad"><h2 className="panel-title">Downstream impact</h2><div className="unavailable-state">Downstream dependency and impact data are not included in the incident response.</div></section>
          <section className="panel panel-pad"><h2 className="panel-title">Incident timeline</h2><ol className="incident-timeline"><li><span className="timeline-dot" /><div><strong>Failure recorded</strong><small>{incident.occurred_at?.replace("T", " ").slice(0, 19) || "Timestamp unavailable"}</small></div></li>{incident.diagnosis && <li><span className="timeline-dot" /><div><strong>Diagnosis available</strong><small>The API does not provide a diagnosis timestamp.</small></div></li>}{incident.feedback && <li><span className="timeline-dot" /><div><strong>Feedback recorded</strong><small>Feedback is present; its timestamp is unavailable.</small></div></li>}</ol><div className="subsection-label">Log provenance</div><p className="unavailable-note">The response includes a log tail, but does not identify its source or collection metadata.</p></section>
        </div>
      </div>
    </div>
  );
}
