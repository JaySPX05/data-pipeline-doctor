import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, USE_MOCKS } from "../api/client.js";
import VerdictBadge from "../components/VerdictBadge.jsx";

const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
const formatNumber = (value) => isNumber(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—";
const formatPercent = (value) => isNumber(value) ? `${formatNumber(value)}%` : "—";
const formatMinutes = (value) => isNumber(value) ? `${formatNumber(value)} min` : "—";

function SnapshotValue({ value, formatter }) {
  return isNumber(value) ? formatter(value) : "—";
}

export default function Overview() {
  const [incidents, setIncidents] = useState([]);
  const [loadError, setLoadError] = useState(null);
  const [summary, setSummary] = useState(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState(null);

  useEffect(() => {
    let active = true;
    api.listIncidents().then((rows) => {
      if (active) { setIncidents(rows || []); setLoadError(null); }
    }).catch((error) => { if (active) setLoadError(error.message); });
    api.metricsSummary().then((result) => {
      if (active) { setSummary(result); setSummaryError(null); }
    }).catch((error) => { if (active) setSummaryError(error.message); })
      .finally(() => { if (active) setSummaryLoading(false); });
    return () => { active = false; };
  }, []);

  const evidenceCount = incidents.reduce((count, item) => count + (item.diagnosis?.evidence?.length || 0), 0);
  const pageOnCall = incidents.filter((item) => item.diagnosis?.page_oncall).length;
  const diagnosed = isNumber(summary?.diagnosed) ? summary.diagnosed : null;
  const resolved = isNumber(summary?.resolved) ? summary.resolved : null;
  const mttrTrend = useMemo(() => Array.isArray(summary?.mttr_by_day)
    ? summary.mttr_by_day.filter((point) => point && typeof point.day === "string" && isNumber(point.mttr_minutes))
    : [], [summary]);
  const verdicts = summary?.by_verdict && typeof summary.by_verdict === "object" && !Array.isArray(summary.by_verdict)
    ? Object.entries(summary.by_verdict).filter(([, count]) => isNumber(count))
    : [];
  const metricValue = (value, formatter) => summaryLoading ? "Loading…" : formatter(value);
  const noSummary = !summaryLoading && !summary && !summaryError;

  return <div>
    <div className="page-heading">
      <div><h1>Overview</h1><p>Your pipeline incident workspace at a glance.</p></div>
      <div className="heading-actions"><span className={`badge ${USE_MOCKS ? "badge-amber" : "badge-blue"}`}>{USE_MOCKS ? "Sample dashboard data" : "Live incident data"}</span></div>
    </div>

    {loadError && <p className="inline-error" role="alert">Could not load incidents: {loadError}</p>}
    {summaryError && <p className="inline-error" role="alert">Could not load metrics summary: {summaryError}</p>}
    <section className="kpi-grid" aria-label="Overview metrics" aria-busy={summaryLoading}>
      <div className="kpi"><div className="kpi-label">Total incidents</div><div className="kpi-value">{metricValue(summary?.total_incidents, formatNumber)}</div><div className="kpi-note">{summaryLoading ? "Loading summary…" : `${formatNumber(diagnosed)} diagnosed · ${formatNumber(resolved)} resolved`}</div></div>
      <div className="kpi"><div className="kpi-label">Auto-diagnosed</div><div className="kpi-value">{metricValue(summary?.auto_diagnosed_pct, formatPercent)}</div><div className="kpi-note">Based on {formatNumber(diagnosed)} diagnosed incidents</div></div>
      <div className="kpi"><div className="kpi-label">Avg. diagnosis latency</div><div className="kpi-value">{metricValue(summary?.avg_latency_ms, (value) => isNumber(value) ? `${formatNumber(value)} ms` : "—")}</div><div className="kpi-note">Diagnosis latency from metrics summary</div></div>
      <div className="kpi"><div className="kpi-label">Diagnosis accuracy</div><div className="kpi-value">{metricValue(summary?.accuracy_pct, formatPercent)}</div><div className="kpi-note">{summaryLoading ? "Loading summary…" : `${formatNumber(resolved)} reviewed incidents`}</div></div>
    </section>

    <div className="overview-grid" style={{ marginTop: 14 }}>
      <section className="panel panel-pad">
        <div className="panel-heading-row"><div><h2 className="panel-title">Resolution metrics</h2><p className="panel-subtitle">Mean time to resolution from reviewed incidents.</p></div><strong className="overview-rate">{metricValue(summary?.mttr_minutes, formatMinutes)} <span className="muted">average MTTR</span></strong></div>
        {summaryLoading
          ? <div className="unavailable-state" role="status">Loading metrics summary…</div>
          : summaryError
            ? <div className="unavailable-state">Resolution metrics are unavailable because the summary request failed.</div>
            : mttrTrend.length
              ? <div style={{ width: "100%", height: 190, marginTop: 12 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={mttrTrend} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                    <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
                    <XAxis dataKey="day" tickFormatter={(day) => day.slice(5)} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={{ stroke: "var(--border-strong)" }} tickLine={false} />
                    <YAxis unit=" min" tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
                    <Tooltip contentStyle={{ background: "var(--surface)", borderColor: "var(--border)", borderRadius: 6, color: "var(--text)", fontSize: 11 }} formatter={(value) => [formatMinutes(value), "MTTR"]} />
                    <Line type="monotone" dataKey="mttr_minutes" name="MTTR" stroke="var(--accent)" strokeWidth={2} dot={false} connectNulls />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              : <div className="unavailable-state">No MTTR trend data is available.</div>}
        <div className="memory-stat-row" style={{ marginTop: 12 }}><strong>{metricValue(summary?.pages_avoided, formatNumber)}</strong><span>pages avoided according to the metrics summary</span></div>
        <div className="panel-heading-row" style={{ marginTop: 14 }}><h3 className="panel-title">Incidents by verdict</h3><span className="muted">{metricValue(summary?.total_incidents, formatNumber)} total</span></div>
        {verdicts.length
          ? <ul className="root-cause-list">{verdicts.map(([verdict, count]) => <li key={verdict}><span>{verdict}</span><strong>{formatNumber(count)}</strong></li>)}</ul>
          : <div className="unavailable-note" style={{ marginTop: 8 }}>{summaryLoading ? "Loading verdict counts…" : "Verdict counts are unavailable."}</div>}
        {noSummary && <div className="unavailable-note" style={{ marginTop: 8 }}>{USE_MOCKS ? "Metrics summary is not included in mock fixtures; these values are intentionally left unavailable." : "The metrics summary is unavailable."}</div>}
      </section>
      <section className="panel panel-pad overview-memory">
        <div className="panel-heading-row"><h2 className="panel-title">Incident memory</h2><Link to="/memory" className="table-link">Open memory →</Link></div>
        <p className="panel-subtitle">{USE_MOCKS ? "Sample evidence bundled with the frontend; not learned production memory." : "Memory search is available on the Memory page."}</p>
        <div className="memory-stat-row"><strong>{evidenceCount}</strong><span>evidence items attached to listed diagnoses</span></div>
        <div className="memory-stat-row"><strong>{incidents.filter((item) => item.diagnosis?.memory_used).length}</strong><span>diagnoses marked as memory-assisted</span></div>
        <div className="unavailable-note">Index freshness and feedback accuracy are not provided by the current API.</div>
      </section>
    </div>

    <section className="panel panel-pad" style={{ marginTop: 14 }}>
      <div className="panel-heading-row"><div><h2 className="panel-title">First and latest day</h2><p className="panel-subtitle">Available learning-curve snapshots from the metrics summary.</p></div></div>
      {summaryLoading
        ? <div className="unavailable-state" role="status">Loading day comparisons…</div>
        : summaryError || noSummary
          ? <div className="unavailable-state">Day comparisons are unavailable.</div>
          : <div className="table-wrap"><table className="data-table"><thead><tr><th>Snapshot</th><th>Day</th><th>Auto-diagnosed</th><th>Accuracy</th><th>MTTR</th></tr></thead><tbody>
            {[{ label: "First day", data: summary?.first_day }, { label: "Latest day", data: summary?.latest_day }].map(({ label, data }) => <tr key={label}><td>{label}</td><td className="mono muted">{data?.day || "—"}</td><td><SnapshotValue value={data?.auto_diagnosed_pct} formatter={formatPercent} /></td><td><SnapshotValue value={data?.accuracy_pct} formatter={formatPercent} /></td><td><SnapshotValue value={data?.mttr_minutes} formatter={formatMinutes} /></td></tr>)}
          </tbody></table></div>}
      {noSummary && <p className="unavailable-note" style={{ marginTop: 10 }}>Metrics summary is not included in mock fixtures.</p>}
    </section>

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
