import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";

const STATUS_FILTERS = ["All observed", "Active incident", "Resolved incidents"];

function groupByDag(incidents) {
  const groups = new Map();
  incidents.forEach((incident) => {
    if (!incident.dag_id) return;
    const current = groups.get(incident.dag_id) || [];
    current.push(incident);
    groups.set(incident.dag_id, current);
  });
  return Array.from(groups, ([dag_id, records]) => ({
    dag_id,
    incidents: records.sort((a, b) => String(b.occurred_at || "").localeCompare(String(a.occurred_at || ""))),
    hasActive: records.some((incident) => incident.status !== "resolved"),
  }));
}

export default function PipelineMonitor() {
  const [incidents, setIncidents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(STATUS_FILTERS[0]);
  const [selectedDag, setSelectedDag] = useState("");

  useEffect(() => {
    let active = true;
    api.listIncidents().then((rows) => {
      if (active) { setIncidents(rows || []); setError(null); }
    }).catch((requestError) => { if (active) setError(requestError.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const pipelines = useMemo(() => groupByDag(incidents), [incidents]);
  const visible = pipelines.filter((pipeline) => {
    const searchText = `${pipeline.dag_id} ${pipeline.incidents.map((incident) => `${incident.task_id || ""} ${incident.error_class || ""}`).join(" ")}`.toLowerCase();
    if (query && !searchText.includes(query.toLowerCase())) return false;
    if (status === "Active incident" && !pipeline.hasActive) return false;
    if (status === "Resolved incidents" && pipeline.hasActive) return false;
    return true;
  });
  const selected = visible.find((pipeline) => pipeline.dag_id === selectedDag) || visible[0];
  const flakyTasks = new Set(incidents.filter((incident) => incident.diagnosis?.verdict === "FLAKY").map((incident) => `${incident.dag_id}.${incident.task_id}`));
  const activeIncidents = incidents.filter((incident) => incident.status !== "resolved");
  const flakyIncidents = incidents.filter((incident) => incident.diagnosis?.verdict === "FLAKY");

  return <div>
    <div className="page-heading"><div><h1>Pipeline Monitor</h1><p>Review pipeline IDs that appear in incident records.</p></div><span className="badge badge-blue">Incident data only</span></div>
    {error && <p className="inline-error" role="alert">Could not load incident context: {error}</p>}
    <section className="kpi-grid" aria-label="Pipeline monitor metrics">
      <div className="kpi"><div className="kpi-label">Pipeline inventory</div><div className="kpi-value">—</div><div className="kpi-note">No inventory endpoint in the current client</div></div>
      <div className="kpi"><div className="kpi-label">Successful runs</div><div className="kpi-value">—</div><div className="kpi-note">Run history is unavailable</div></div>
      <div className="kpi"><div className="kpi-label">Failed runs</div><div className="kpi-value">—</div><div className="kpi-note">Incidents do not provide run totals</div></div>
      <div className="kpi"><div className="kpi-label">Tasks diagnosed as flaky</div><div className="kpi-value">{flakyTasks.size}</div><div className="kpi-note">Distinct tasks in returned diagnoses</div></div>
    </section>

    <div className="monitor-grid" style={{ marginTop: 14 }}>
      <section className="panel panel-pad"><div className="panel-heading-row"><div><h2 className="panel-title">Run health</h2><p className="panel-subtitle">Successful and failed run history is not present in the incident API response.</p></div></div><div className="unavailable-state">No run-health chart can be derived from incident records alone. The current API client has no pipeline-run history method.</div></section>
      <section className="panel panel-pad"><div className="panel-heading-row"><div><h2 className="panel-title">Flaky task watchlist</h2><p className="panel-subtitle">Tasks marked FLAKY in diagnoses. {activeIncidents.length} active incidents are in the current response.</p></div></div>
        {flakyIncidents.length ? <ul className="watchlist">{flakyIncidents.map((incident) => <li key={incident.id}><span><strong>{incident.dag_id}.{incident.task_id}</strong><small>{incident.error_class} · {incident.occurred_at?.slice(0, 10) || "date unavailable"}</small></span><Link to={`/incidents/${incident.id}`} className="table-link">Incident →</Link></li>)}</ul> : <div className="unavailable-state">No returned diagnosis is marked FLAKY.</div>}
      </section>
    </div>

    <section className="panel monitor-table-panel" style={{ marginTop: 14 }}>
      <div className="panel-pad monitor-toolbar"><div><h2 className="panel-title">Pipelines observed in incidents</h2><p className="panel-subtitle">This list is not a complete pipeline inventory.</p></div><div className="toolbar"><input className="field" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search DAG or task…" aria-label="Search observed pipelines" /><select className="select-field" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter observed pipeline status">{STATUS_FILTERS.map((filter) => <option key={filter}>{filter}</option>)}</select></div></div>
      <div className="table-wrap"><table className="data-table"><thead><tr><th>DAG ID</th><th>Incident context</th><th>Incident status</th><th>Last incident · UTC</th><th>Recent runs</th><th>Owner</th><th>Schedule</th></tr></thead><tbody>
        {visible.map((pipeline) => {
          const latest = pipeline.incidents[0];
          return <tr key={pipeline.dag_id} className={selected?.dag_id === pipeline.dag_id ? "selected-row" : ""} onClick={() => setSelectedDag(pipeline.dag_id)}><td><button type="button" className="table-select">{pipeline.dag_id}</button></td><td>{pipeline.incidents.length} incident{pipeline.incidents.length === 1 ? "" : "s"} · {latest?.task_id || "task unavailable"}</td><td><span className={pipeline.hasActive ? "status-pending" : "status-diagnosed"}>{pipeline.hasActive ? "Active incident" : "Resolved incidents"}</span></td><td className="mono muted">{latest?.occurred_at?.replace("T", " ").slice(0, 16) || "Unavailable"}</td><td className="muted">Unavailable</td><td className="muted">Unavailable</td><td className="muted">Unavailable</td></tr>;
        })}
        {!visible.length && <tr><td colSpan={7} className="empty-state">{loading ? "Loading incident context…" : error ? "Incident context could not be loaded." : pipelines.length ? "No observed pipelines match these filters." : "No incident records include a DAG ID."}</td></tr>}
      </tbody></table></div>
      <div className="table-footnote">Observed DAGs come only from existing incident records. Run history, owner, and schedule require fields not provided by the current API.</div>
    </section>

    <section className="panel panel-pad selected-pipeline" aria-live="polite"><div className="panel-heading-row"><div><span className="section-label">Selected pipeline context</span><h2 className="panel-title">{selected?.dag_id || "No pipeline selected"}</h2></div>{selected?.incidents[0] && <Link to={`/incidents/${selected.incidents[0].id}`} className="table-link">Open latest incident →</Link>}</div><p className="panel-subtitle">{selected ? `${selected.incidents.length} returned incident record${selected.incidents.length === 1 ? "" : "s"} for this DAG. This does not represent pipeline health or full run history.` : "Select an observed DAG to view its related incident context."}</p></section>
  </div>;
}
