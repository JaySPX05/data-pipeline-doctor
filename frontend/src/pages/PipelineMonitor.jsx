import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, USE_MOCKS } from "../api/client.js";

const STATUS_FILTERS = ["All observed", "Active incident", "Resolved incidents"];
const isCount = (value) => typeof value === "number" && Number.isFinite(value);

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
  }));
}

function formatList(value, emptyLabel = "None") {
  if (Array.isArray(value)) {
    if (!value.length) return emptyLabel;
    return value.map((item) => typeof item === "string" ? item : item?.task_id || item?.id || JSON.stringify(item)).join(", ");
  }
  if (typeof value === "string") return value || emptyLabel;
  if (isCount(value)) return String(value);
  return "—";
}

function formatVerdicts(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "—";
  const entries = Object.entries(value).filter(([, count]) => isCount(count));
  return entries.length ? entries.map(([verdict, count]) => `${verdict}: ${count}`).join(", ") : "None";
}

function formatLastFailure(value) {
  if (!value || typeof value !== "string") return "—";
  return value.replace("T", " ").replace(/Z$/, " UTC");
}

export default function PipelineMonitor() {
  const [incidents, setIncidents] = useState([]);
  const [incidentError, setIncidentError] = useState(null);
  const [pipelines, setPipelines] = useState(null);
  const [company, setCompany] = useState(null);
  const [pipelineLoading, setPipelineLoading] = useState(true);
  const [pipelineError, setPipelineError] = useState(null);
  const [incidentLoading, setIncidentLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState(STATUS_FILTERS[0]);
  const [selectedDag, setSelectedDag] = useState("");

  useEffect(() => {
    let active = true;
    api.listIncidents().then((rows) => {
      if (active) { setIncidents(rows || []); setIncidentError(null); }
    }).catch((requestError) => { if (active) setIncidentError(requestError.message); })
      .finally(() => { if (active) setIncidentLoading(false); });
    api.listPipelines().then((response) => {
      if (!active) return;
      if (USE_MOCKS && response === null) {
        setPipelines(null);
        setCompany(null);
        setPipelineError(null);
      } else if (response && Array.isArray(response.pipelines)) {
        setPipelines(response.pipelines);
        setCompany(typeof response.company === "string" ? response.company : null);
        setPipelineError(null);
      } else {
        setPipelines(null);
        setCompany(null);
        setPipelineError("The response did not contain a pipeline list.");
      }
    }).catch((requestError) => { if (active) setPipelineError(requestError.message); })
      .finally(() => { if (active) setPipelineLoading(false); });
    return () => { active = false; };
  }, []);

  const pipelineRows = Array.isArray(pipelines) ? pipelines : [];
  const visible = useMemo(() => pipelineRows.filter((pipeline) => {
    const taskSearchText = [pipeline.tasks, pipeline.failing_tasks, pipeline.flaky_tasks]
      .map((tasks) => Array.isArray(tasks) ? tasks.join(" ") : String(tasks || ""))
      .join(" ");
    const searchText = `${pipeline.dag_id || ""} ${taskSearchText}`.toLowerCase();
    if (query && !searchText.includes(query.toLowerCase())) return false;
    if (status === "Active incident" && !(isCount(pipeline.open_incidents) && pipeline.open_incidents > 0)) return false;
    if (status === "Resolved incidents" && !(isCount(pipeline.incidents) && pipeline.incidents > 0 && pipeline.open_incidents === 0)) return false;
    return true;
  }), [pipelineRows, query, status]);
  const selected = visible.find((pipeline) => pipeline.dag_id === selectedDag) || visible[0];
  const incidentPipelines = useMemo(() => groupByDag(incidents), [incidents]);
  const flakyIncidents = incidents.filter((incident) => incident.diagnosis?.verdict === "FLAKY");
  const activeIncidents = incidents.filter((incident) => incident.status !== "resolved");
  const selectedIncident = incidentPipelines.find((pipeline) => pipeline.dag_id === selected?.dag_id)?.incidents[0];

  let emptyMessage = "";
  if (pipelineLoading) emptyMessage = "Loading pipeline inventory…";
  else if (pipelineError) emptyMessage = "Pipeline inventory could not be loaded.";
  else if (USE_MOCKS && pipelines === null) emptyMessage = "Pipeline inventory is not included in mock data.";
  else if (!pipelineRows.length) emptyMessage = "The API returned no pipelines.";
  else emptyMessage = "No pipelines match these filters.";

  return <div>
    <div className="page-heading"><div><h1>Pipeline Monitor</h1><p>Review DAGs, tasks, and incident context{company ? ` for ${company}` : ""}.</p></div><span className={`badge ${USE_MOCKS ? "badge-amber" : "badge-blue"}`}>{USE_MOCKS ? "Inventory unavailable in mock mode" : "API pipeline inventory"}</span></div>
    {pipelineError && <p className="inline-error" role="alert">Could not load pipeline inventory: {pipelineError}</p>}
    {incidentError && <p className="inline-error" role="alert">Could not load incident watchlist: {incidentError}</p>}

    <section className="panel panel-pad" style={{ marginTop: 14 }}>
      <div className="panel-heading-row"><div><h2 className="panel-title">Flaky task watchlist</h2><p className="panel-subtitle">Incident diagnoses marked FLAKY. {incidentLoading ? "Loading incident context…" : `${activeIncidents.length} active incidents are in the current response.`}</p></div></div>
      {flakyIncidents.length ? <ul className="watchlist">{flakyIncidents.map((incident) => <li key={incident.id}><span><strong>{incident.dag_id}.{incident.task_id}</strong><small>{incident.error_class} · {incident.occurred_at?.slice(0, 10) || "date unavailable"}</small></span><Link to={`/incidents/${incident.id}`} className="table-link">Incident →</Link></li>)}</ul> : <div className="unavailable-state">{incidentLoading ? "Loading incident context…" : "No returned diagnosis is marked FLAKY."}</div>}
    </section>

    <section className="panel monitor-table-panel" style={{ marginTop: 14 }}>
      <div className="panel-pad monitor-toolbar"><div><h2 className="panel-title">Pipeline inventory</h2><p className="panel-subtitle">DAGs and tasks from the pipeline API, including pipelines with no incidents.</p></div><div className="toolbar"><input className="field" type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search DAG or task…" aria-label="Search pipelines and tasks" /><select className="select-field" value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter pipelines by incident status">{STATUS_FILTERS.map((filter) => <option key={filter}>{filter}</option>)}</select></div></div>
      <div className="table-wrap"><table className="data-table"><thead><tr><th>DAG ID</th><th>Tasks</th><th>Total incidents</th><th>Open incidents</th><th>Last failure · UTC</th><th>Failing tasks</th><th>Flaky tasks</th><th>Verdicts</th></tr></thead><tbody>
        {visible.map((pipeline) => <tr key={pipeline.dag_id} className={selected?.dag_id === pipeline.dag_id ? "selected-row" : ""} onClick={() => setSelectedDag(pipeline.dag_id)}>
          <td><button type="button" className="table-select">{pipeline.dag_id || "—"}</button></td>
          <td className="mono">{formatList(pipeline.tasks)}</td>
          <td>{isCount(pipeline.incidents) ? pipeline.incidents : "—"}</td>
          <td>{isCount(pipeline.open_incidents) ? pipeline.open_incidents : "—"}</td>
          <td className="mono muted">{formatLastFailure(pipeline.last_failure_at)}</td>
          <td>{formatList(pipeline.failing_tasks)}</td>
          <td>{formatList(pipeline.flaky_tasks)}</td>
          <td>{formatVerdicts(pipeline.verdicts)}</td>
        </tr>)}
        {!visible.length && <tr><td colSpan={8} className="empty-state">{emptyMessage}</td></tr>}
      </tbody></table></div>
      <div className="table-footnote">{pipelineLoading ? "Loading inventory…" : pipelineError ? "Pipeline inventory is unavailable." : USE_MOCKS && pipelines === null ? "Enable real API mode to load the backend inventory." : `${visible.length} of ${pipelineRows.length} pipelines shown.`}</div>
    </section>

    <section className="panel panel-pad selected-pipeline" aria-live="polite"><div className="panel-heading-row"><div><span className="section-label">Selected pipeline context</span><h2 className="panel-title">{selected?.dag_id || "No pipeline selected"}</h2></div>{selectedIncident && <Link to={`/incidents/${selectedIncident.id}`} className="table-link">Open latest incident →</Link>}</div>
      {selected
        ? <><p className="panel-subtitle">{formatList(selected.tasks)} · {isCount(selected.incidents) ? selected.incidents : "—"} total incidents · {isCount(selected.open_incidents) ? selected.open_incidents : "—"} open</p><p className="panel-subtitle">Failing tasks: {formatList(selected.failing_tasks)} · Flaky tasks: {formatList(selected.flaky_tasks)} · Verdicts: {formatVerdicts(selected.verdicts)}</p></>
        : <p className="panel-subtitle">{pipelineLoading ? "Loading pipeline details…" : "Select a pipeline to view its tasks and incident summary."}</p>}
    </section>
  </div>;
}
