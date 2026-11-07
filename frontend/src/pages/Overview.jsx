import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, USE_MOCKS } from "../api/client.js";
import VerdictBadge from "../components/VerdictBadge.jsx";

export default function Overview() {
  const [incidents, setIncidents] = useState([]);
  const [loadError, setLoadError] = useState(null);

  useEffect(() => {
    let active = true;
    api.listIncidents().then((rows) => {
      if (active) { setIncidents(rows || []); setLoadError(null); }
    }).catch((error) => { if (active) setLoadError(error.message); });
    return () => { active = false; };
  }, []);

  const metrics = useMemo(() => {
    const active = incidents.filter((item) => item.status !== "resolved").length;
    const diagnosed = incidents.filter((item) => Boolean(item.diagnosis)).length;
    const rate = incidents.length ? Math.round((diagnosed / incidents.length) * 100) : null;
    const flakyTasks = new Set(incidents.filter((item) => item.diagnosis?.verdict === "FLAKY").map((item) => `${item.dag_id}.${item.task_id}`));
    return { active, diagnosed, rate, flaky: flakyTasks.size };
  }, [incidents]);

  const evidenceCount = incidents.reduce((count, item) => count + (item.diagnosis?.evidence?.length || 0), 0);
  const pageOnCall = incidents.filter((item) => item.diagnosis?.page_oncall).length;

  return <div>
    <div className="page-heading">
      <div><h1>Overview</h1><p>Your pipeline incident workspace at a glance.</p></div>
      <div className="heading-actions"><select className="select-field" disabled aria-label="Date range unavailable"><option>Date range unavailable</option></select><span className={`badge ${USE_MOCKS ? "badge-amber" : "badge-blue"}`}>{USE_MOCKS ? "Sample dashboard data" : "Live incident data"}</span></div>
    </div>

    {loadError && <p className="inline-error" role="alert">Could not load incidents: {loadError}</p>}
    <section className="kpi-grid" aria-label="Overview metrics">
      <div className="kpi"><div className="kpi-label">Active incidents</div><div className="kpi-value">{incidents.length ? metrics.active : "—"}</div><div className="kpi-note">From the incident list</div></div>
      <div className="kpi"><div className="kpi-label">AI diagnosed</div><div className="kpi-value">{metrics.rate == null ? "—" : `${metrics.rate}%`}</div><div className="kpi-note">{metrics.diagnosed} of {incidents.length} listed</div></div>
      <div className="kpi"><div className="kpi-label">Avg. diagnosis time</div><div className="kpi-value">—</div><div className="kpi-note">Not provided by the current API</div></div>
      <div className="kpi"><div className="kpi-label">Flaky tasks</div><div className="kpi-value">{metrics.flaky}</div><div className="kpi-note">Distinct tasks marked FLAKY in returned diagnoses</div></div>
    </section>

    <div className="overview-grid" style={{ marginTop: 14 }}>
      <section className="panel panel-pad">
        <div className="panel-heading-row"><div><h2 className="panel-title">Pipeline health</h2><p className="panel-subtitle">Successful-run trend and run totals require pipeline run data.</p></div><strong className="overview-rate">— <span className="muted">successful runs</span></strong></div>
        <div className="unavailable-state">The existing API client provides incidents and learning metrics, but no pipeline inventory or run-health history. No run chart is available from the current data source.</div>
        <div className="chart-note"><span>{incidents.length} incident records available for context</span><Link to="/pipelines" className="table-link">Open Pipeline Monitor →</Link></div>
      </section>
      <section className="panel panel-pad overview-memory">
        <div className="panel-heading-row"><h2 className="panel-title">Incident memory</h2><Link to="/memory" className="table-link">Open memory →</Link></div>
        <p className="panel-subtitle">{USE_MOCKS ? "Sample evidence bundled with the frontend; not learned production memory." : "Memory search is available on the Memory page."}</p>
        <div className="memory-stat-row"><strong>{evidenceCount}</strong><span>evidence items attached to listed diagnoses</span></div>
        <div className="memory-stat-row"><strong>{incidents.filter((item) => item.diagnosis?.memory_used).length}</strong><span>diagnoses marked as memory-assisted</span></div>
        <div className="unavailable-note">Index freshness and feedback accuracy are not provided by the current API.</div>
      </section>
    </div>

    <section className="panel overview-incidents" style={{ marginTop: 14 }}>
      <div className="panel-pad panel-heading-row"><div><h2 className="panel-title">Recent incidents</h2><p className="panel-subtitle">Incident records returned by the current data source.</p></div><Link to="/" className="table-link">View inbox →</Link></div>
      <div className="table-wrap"><table className="data-table"><thead><tr><th>Incident</th><th>DAG / task</th><th>Error type</th><th>Verdict</th><th>Failed · UTC</th><th>Diagnosis</th></tr></thead><tbody>
        {incidents.slice(0, 5).map((incident) => <tr key={incident.id}><td className="mono muted">{incident.id}</td><td><Link to={`/incidents/${incident.id}`} className="table-link">{incident.dag_id}<br /><span className="muted">{incident.task_id}</span></Link></td><td className="mono">{incident.error_class}</td><td><VerdictBadge verdict={incident.diagnosis?.verdict} /></td><td className="mono muted">{incident.occurred_at?.replace("T", " ").slice(0, 16) || "—"}</td><td>{incident.diagnosis ? <span className="status-diagnosed">Diagnosed</span> : <span className="status-pending">Pending</span>}</td></tr>)}
        {!incidents.length && <tr><td className="empty-state" colSpan={6}>{loadError ? "Incident data could not be loaded." : "No incident records are available."}</td></tr>}
      </tbody></table></div>
      <div className="table-footnote">{USE_MOCKS ? "Bundled incident records are sample fixtures, not production records." : `On-call flags in returned diagnoses: ${pageOnCall}`}</div>
    </section>
    <section className="panel panel-pad overview-footer"><div><h2 className="panel-title">On-call status</h2><p className="panel-subtitle">{pageOnCall} returned diagnoses are flagged to page on-call. This is not an on-call roster.</p></div><span>{USE_MOCKS ? "Sample data" : "Live API data"}</span></section>
  </div>;
}
