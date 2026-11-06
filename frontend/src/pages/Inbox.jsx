// Incident inbox. Refreshes every three seconds while replay.py runs.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import VerdictBadge from "../components/VerdictBadge.jsx";

const EMPTY_FILTERS = { search: "", status: "", verdict: "", dag_id: "", diagnosis: "", from: "", to: "" };
const PAGE_SIZE = 10;

function matchesSearch(incident, query) {
  if (!query) return true;
  const diagnosis = incident.diagnosis;
  const fields = [
    incident.id,
    incident.dag_id,
    incident.task_id,
    incident.run_id,
    incident.error_class,
    incident.error_message,
    incident.fingerprint,
    incident.log_tail,
    incident.status,
    diagnosis?.verdict,
    diagnosis?.root_cause,
    diagnosis?.suggested_fix,
    ...(diagnosis?.evidence || []).flatMap((evidence) => [evidence.text, evidence.type, evidence.document_id]),
    incident.feedback?.root_cause,
    incident.feedback?.fix,
    incident.feedback?.verdict,
  ];
  return fields.some((field) => String(field || "").toLowerCase().includes(query));
}

export default function Inbox() {
  const [incidents, setIncidents] = useState([]);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [tab, setTab] = useState("all");
  const [page, setPage] = useState(1);
  const { search, status, verdict, dag_id, diagnosis } = filters;

  useEffect(() => {
    let active = true;
    const load = () => api.listIncidents({ status, verdict, dag_id })
      .then((data) => {
        if (!active) return;
        setIncidents(data);
        setError(null);
      })
      .catch((requestError) => {
        if (active) setError(requestError.message);
      });
    load();
    const timer = setInterval(load, 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [status, verdict, dag_id]);

  if (error) return <div className="panel panel-pad" role="alert"><p style={{ color: "var(--danger)", margin: 0 }}>Could not load incidents: {error}</p></div>;

  const diagnosed = incidents.filter((incident) => incident.diagnosis).length;
  const resolved = incidents.filter((incident) => incident.status === "resolved").length;
  const pending = incidents.length - diagnosed;
  const normalizedSearch = search.trim().toLowerCase();
  const visibleIncidents = incidents.filter((incident) => {
    if (!matchesSearch(incident, normalizedSearch)) return false;
    if (diagnosis === "diagnosed" && !incident.diagnosis) return false;
    if (diagnosis === "pending" && incident.diagnosis) return false;
    if (tab === "active" && incident.status === "resolved") return false;
    if (tab === "resolved" && incident.status !== "resolved") return false;
    const date = incident.occurred_at?.slice(0, 10) || "";
    if (filters.from && date < filters.from) return false;
    if (filters.to && date > filters.to) return false;
    return true;
  });
  const hasActiveFilters = Boolean(search || status || verdict || dag_id || diagnosis || filters.from || filters.to);
  const updateFilter = (key) => (event) => setFilters((current) => ({ ...current, [key]: event.target.value }));
  const pageCount = Math.max(1, Math.ceil(visibleIncidents.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageIncidents = visibleIncidents.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const memoryEvidenceCount = visibleIncidents.reduce((sum, incident) => sum + (incident.diagnosis?.evidence?.length || 0), 0);

  return (
    <div>
      <div className="page-heading">
        <div><h1>Incident Inbox</h1><p>Triage pipeline failures and review agent diagnoses.</p></div>
        <span className="muted" style={{ fontSize: 10 }}>Live · refreshes every 3 seconds</span>
      </div>

      <section className="kpi-grid" aria-label="Incident summary">
        <div className="kpi"><div className="kpi-label">Incidents</div><div className="kpi-value">{incidents.length}</div><div className="kpi-note">Currently listed</div></div>
        <div className="kpi"><div className="kpi-label">Diagnosed</div><div className="kpi-value">{diagnosed}</div><div className="kpi-note">With an available diagnosis</div></div>
        <div className="kpi"><div className="kpi-label">Awaiting diagnosis</div><div className="kpi-value">{pending}</div><div className="kpi-note">No diagnosis attached</div></div>
        <div className="kpi"><div className="kpi-label">Resolved</div><div className="kpi-value">{resolved}</div><div className="kpi-note">Marked resolved</div></div>
      </section>

      <section className="triage-summary" aria-label="Triage summary">
        <span><strong>{incidents.filter((incident) => incident.status !== "resolved").length}</strong> active incidents</span>
        <span><strong>{diagnosed}</strong> diagnosed</span>
        <span><strong>{pending}</strong> awaiting diagnosis</span>
        <span className="muted">Severity is unavailable in the current incident data.</span>
      </section>

      <section className="panel" style={{ marginTop: 16 }}>
        <div className="panel-pad" style={{ paddingBottom: 12 }}>
          <div className="inbox-tabs" role="tablist" aria-label="Incident status tabs">
            {[{ id: "all", label: "All", count: incidents.length }, { id: "active", label: "Active", count: incidents.filter((incident) => incident.status !== "resolved").length }, { id: "resolved", label: "Resolved", count: resolved }].map((item) => <button type="button" role="tab" aria-selected={tab === item.id} key={item.id} className={`inbox-tab${tab === item.id ? " active" : ""}`} onClick={() => { setTab(item.id); setPage(1); }}>{item.label}<span>{item.count}</span></button>)}
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 12 }}>
            <h2 className="panel-title">All incidents <span className="muted" style={{ fontWeight: 400 }}>({visibleIncidents.length})</span></h2>
            <button type="button" className="button-quiet" onClick={() => { setFilters({ ...EMPTY_FILTERS }); setTab("all"); setPage(1); }} disabled={!hasActiveFilters && tab === "all"}>Clear filters</button>
          </div>
          <div className="toolbar" role="search" aria-label="Search and filter incidents">
            <input className="field" type="search" value={search} onChange={updateFilter("search")} placeholder="Search incident ID, task, error…" aria-label="Search incidents" style={{ flex: "1 1 190px" }} />
            <select className="select-field" value={status} onChange={updateFilter("status")} aria-label="Filter by status">
              <option value="">All statuses</option>
              <option value="open">Open</option>
              <option value="diagnosed">Diagnosed</option>
              <option value="resolved">Resolved</option>
            </select>
            <select className="select-field" value={verdict} onChange={updateFilter("verdict")} aria-label="Filter by verdict">
              <option value="">All verdicts</option>
              <option value="KNOWN_ISSUE">Known issue</option>
              <option value="FLAKY">Flaky</option>
              <option value="UPSTREAM_CHANGE">Upstream change</option>
              <option value="NEW">New</option>
            </select>
            <input className="field" type="search" value={dag_id} onChange={updateFilter("dag_id")} placeholder="Filter by DAG ID" aria-label="Filter by DAG ID" style={{ flex: "1 1 140px" }} />
            <select className="select-field" value={diagnosis} onChange={updateFilter("diagnosis")} aria-label="Filter by diagnosis"><option value="">All diagnoses</option><option value="diagnosed">Diagnosed</option><option value="pending">Pending</option></select>
            <label className="date-filter">From <input className="field" type="date" value={filters.from} onChange={updateFilter("from")} aria-label="Filter incidents from date" /></label>
            <label className="date-filter">To <input className="field" type="date" value={filters.to} onChange={updateFilter("to")} aria-label="Filter incidents through date" /></label>
            <select className="select-field" disabled aria-label="Severity filter unavailable"><option>Severity unavailable</option></select>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Incident</th><th>DAG / task</th><th>Error type</th><th>Verdict</th><th>Failed · UTC</th><th>Diagnosis</th></tr></thead>
            <tbody>
              {pageIncidents.map((incident) => (
                <tr key={incident.id}>
                  <td className="mono muted">{incident.id}</td>
                  <td><Link to={`/incidents/${incident.id}`} className="table-link">{incident.dag_id}<br /><span className="muted" style={{ fontWeight: 400 }}>{incident.task_id}</span></Link></td>
                  <td className="mono">{incident.error_class}</td>
                  <td><VerdictBadge verdict={incident.diagnosis?.verdict} /></td>
                  <td className="mono muted">{incident.occurred_at.replace("T", " ").slice(0, 16)}</td>
                  <td><span className={incident.diagnosis ? "status-diagnosed" : "status-pending"}><span aria-hidden="true">{incident.diagnosis ? "●" : "○"}</span> {incident.diagnosis ? "Diagnosed" : "Pending"}</span></td>
                </tr>
              ))}
              {visibleIncidents.length === 0 && <tr><td colSpan={6} className="empty-state">{hasActiveFilters || tab !== "all" || diagnosis || filters.from || filters.to ? "No incidents match the current search and filters. Clear filters to see all incidents." : "No incidents yet. Run scripts/replay.py."}</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="table-footnote pagination-row"><span>Showing {visibleIncidents.length ? (currentPage - 1) * PAGE_SIZE + 1 : 0}–{Math.min(currentPage * PAGE_SIZE, visibleIncidents.length)} of {visibleIncidents.length} filtered incidents ({incidents.length} loaded)</span><div><button type="button" className="button-quiet" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Previous</button><span>Page {currentPage} of {pageCount}</span><button type="button" className="button-quiet" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>Next</button></div></div>
      </section>
      <div className="inbox-memory-footer">◉ Memory context attached to listed diagnoses: {memoryEvidenceCount} evidence items. Availability depends on the incident response.</div>
    </div>
  );
}
