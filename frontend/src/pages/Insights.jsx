import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client.js";

export default function Insights() {
  const [curve, setCurve] = useState([]);
  const [curveError, setCurveError] = useState(null);
  const [brief, setBrief] = useState(null);
  const [loadingBrief, setLoadingBrief] = useState(false);
  const [briefError, setBriefError] = useState(null);
  const [incidents, setIncidents] = useState([]);
  const [memoryStatus, setMemoryStatus] = useState(null);
  const [memoryStatusError, setMemoryStatusError] = useState(null);
  const [knowledgeQuery, setKnowledgeQuery] = useState("");
  const [knowledgeResults, setKnowledgeResults] = useState(null);
  const [knowledgeLoading, setKnowledgeLoading] = useState(false);
  const [knowledgeError, setKnowledgeError] = useState(null);
  const [knowledgeType, setKnowledgeType] = useState("");

  useEffect(() => {
    api.learningCurve().then(setCurve).catch((error) => setCurveError(error.message || "Could not load learning metrics."));
    api.listIncidents().then((rows) => setIncidents(rows || [])).catch(() => {});
    api.memoryStatus().then(setMemoryStatus).catch((error) => setMemoryStatusError(error.message));
  }, []);

  const searchKnowledge = async (event) => {
    event.preventDefault();
    const query = knowledgeQuery.trim();
    if (!query) { setKnowledgeError("Enter a search term to find incident knowledge."); setKnowledgeResults(null); return; }
    setKnowledgeLoading(true);
    setKnowledgeError(null);
    try { setKnowledgeResults(await api.searchMemory(query)); setKnowledgeType(""); }
    catch (error) { setKnowledgeError(error.message || "Could not search incident knowledge."); setKnowledgeResults(null); }
    finally { setKnowledgeLoading(false); }
  };

  const roots = incidents.reduce((groups, incident) => {
    const root = incident.diagnosis?.root_cause;
    if (root) groups.set(root, (groups.get(root) || 0) + 1);
    return groups;
  }, new Map());
  const feedbackIncidents = incidents.filter((incident) => incident.feedback);
  const rootCauseData = Array.from(roots, ([cause, count], index) => ({ label: `Cause ${index + 1}`, cause, count }));
  const knowledgeTypes = Array.from(new Set((knowledgeResults || []).map((item) => item.type || "memory")));
  const filteredKnowledge = (knowledgeResults || []).filter((item) => !knowledgeType || (item.type || "memory") === knowledgeType);
  const memoryLabel = memoryStatusError ? "Status unavailable" : memoryStatus?.mock ? "Mock mode" : memoryStatus?.enabled ? "Enabled" : memoryStatus?.enabled === false ? "Disabled" : "Status unknown";

  const runReflect = async () => {
    setLoadingBrief(true);
    setBriefError(null);
    try { setBrief((await api.reflect()).text); }
    catch (error) { setBriefError(error.message || "Could not generate the Pipeline Health Brief."); }
    finally { setLoadingBrief(false); }
  };

  return (
    <div className="insights-page">
      <div className="page-heading">
        <div><h1>Memory &amp; Insights</h1><p>Review incident knowledge, learning trends, and memory-backed diagnosis context.</p></div>
      </div>

      <section className="panel panel-pad">
        <div><h2 className="panel-title">The agent gets better every day</h2><p className="panel-subtitle">Share of failures diagnosed with confidence (not NEW, at least 70% confident), and verdict accuracy, per day.</p></div>
        {curveError ? <div className="unavailable-state" role="alert">Could not load learning metrics: {curveError}</div> : <div className="chart-box" aria-label="Learning trend chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={curve} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" />
              <XAxis dataKey="day" tickFormatter={(day) => day.slice(5)} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={{ stroke: "var(--border-strong)" }} tickLine={false} />
              <YAxis unit="%" domain={[0, 100]} tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip contentStyle={{ background: "var(--surface)", borderColor: "var(--border)", borderRadius: 6, color: "var(--text)", fontSize: 11 }} />
              <Legend wrapperStyle={{ color: "var(--muted)", fontSize: 10 }} />
              <Line type="monotone" dataKey="auto_diagnosed_pct" name="Auto-diagnosed %" stroke="var(--accent)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
              <Line type="monotone" dataKey="accuracy_pct" name="Accuracy %" stroke="#42a879" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </LineChart>
          </ResponsiveContainer>
        </div>}
      </section>

      <section className="kpi-grid insights-memory-kpis" aria-label="Memory and learning metrics">
        <div className="kpi"><div className="kpi-label">Memory service</div><div className="kpi-value compact-value">{memoryLabel}</div><div className="kpi-note">{memoryStatusError || (memoryStatus?.mock ? "No Hindsight service is queried in mock mode" : "From the existing memory status endpoint")}</div></div>
        <div className="kpi"><div className="kpi-label">Incidents in current response</div><div className="kpi-value">{incidents.length}</div><div className="kpi-note">Not a complete memory index count</div></div>
        <div className="kpi"><div className="kpi-label">Root causes represented</div><div className="kpi-value">{roots.size}</div><div className="kpi-note">Derived from returned diagnoses</div></div>
        <div className="kpi"><div className="kpi-label">Feedback records</div><div className="kpi-value">{feedbackIncidents.length}</div><div className="kpi-note">Present on returned incidents</div></div>
      </section>

      <div className="insights-detail-grid">
        <section className="panel panel-pad"><div className="panel-heading-row"><div><h2 className="panel-title">Incident knowledge search</h2><p className="panel-subtitle">Uses the existing memory search integration when you submit a query.</p></div><span className="badge badge-blue">{memoryStatus?.mock ? "Sample fixtures" : "Hindsight search"}</span></div>
          <form className="search-row knowledge-search" onSubmit={searchKnowledge}><input className="field" value={knowledgeQuery} onChange={(event) => setKnowledgeQuery(event.target.value)} placeholder="Search incident knowledge…" aria-label="Search incident knowledge" /><button className="button-primary" disabled={knowledgeLoading}>{knowledgeLoading ? "Searching…" : "Search"}</button></form>
          {memoryStatus?.mock && <p className="unavailable-note">Mock status only: results come from bundled sample memory, not learned production records.</p>}
          {knowledgeError && <p className="inline-error" role="alert">{knowledgeError}</p>}
          {knowledgeLoading && <p className="unavailable-note" role="status">Searching existing memory source…</p>}
          {knowledgeResults && !knowledgeLoading && (knowledgeResults.length ? <><div className="knowledge-filter-row"><span className="section-label">Incident knowledge table</span><select className="select-field" value={knowledgeType} onChange={(event) => setKnowledgeType(event.target.value)} aria-label="Filter knowledge by type"><option value="">All returned types</option>{knowledgeTypes.map((type) => <option key={type} value={type}>{type}</option>)}</select></div><div className="table-wrap"><table className="data-table"><thead><tr><th>Type</th><th>Occurred</th><th>Source</th><th>Knowledge</th></tr></thead><tbody>{filteredKnowledge.map((item, index) => <tr key={item.memory_id || index}><td>{item.type || "memory"}</td><td className="mono muted">{item.occurred_at?.slice(0, 10) || "Unavailable"}</td><td className="mono muted">{item.document_id || item.memory_id || "Unavailable"}</td><td className="knowledge-cell">{item.text || "No text provided"}</td></tr>)}{!filteredKnowledge.length && <tr><td colSpan={4} className="empty-state">No knowledge records match this type.</td></tr>}</tbody></table></div></> : <p className="unavailable-state">No matching knowledge was returned for this query.</p>)}
          <div className="subsection-label">Index freshness</div><p className="unavailable-note">The current API does not report an index timestamp or freshness window.</p>
        </section>
        <section className="panel panel-pad"><h2 className="panel-title">Recurring root causes</h2><p className="panel-subtitle">Counts from diagnoses in the current incident response.</p>
          {roots.size ? <><div className="root-cause-chart"><ResponsiveContainer width="100%" height="100%"><BarChart data={rootCauseData} layout="vertical" margin={{ top: 4, right: 14, left: 0, bottom: 0 }}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" horizontal={false} /><XAxis type="number" allowDecimals={false} tick={{ fill: "var(--muted)", fontSize: 9 }} axisLine={{ stroke: "var(--border-strong)" }} tickLine={false} /><YAxis type="category" dataKey="label" width={65} tick={{ fill: "var(--muted)", fontSize: 9 }} axisLine={false} tickLine={false} /><Tooltip contentStyle={{ background: "var(--surface)", borderColor: "var(--border)", color: "var(--text)" }} formatter={(value) => [`${value} incident${Number(value) === 1 ? "" : "s"}`, "Returned diagnoses"]} labelFormatter={(label) => rootCauseData.find((item) => item.label === label)?.cause || label} /><Bar dataKey="count" name="Returned diagnoses" fill="var(--accent)" radius={[0, 3, 3, 0]} /></BarChart></ResponsiveContainer></div><ul className="root-cause-list">{rootCauseData.map(({ label, cause, count }) => <li key={cause}><span><strong>{label}</strong> · {cause}</span><strong>{count}</strong></li>)}</ul></> : <div className="unavailable-state">No diagnosis root causes are available.</div>}
          <div className="subsection-label">Flaky task patterns</div>{incidents.filter((incident) => incident.diagnosis?.verdict === "FLAKY").length ? <ul className="root-cause-list">{incidents.filter((incident) => incident.diagnosis?.verdict === "FLAKY").map((incident) => <li key={incident.id}><span>{incident.dag_id}.{incident.task_id}</span><strong>Flaky</strong></li>)}</ul> : <p className="unavailable-note">No returned diagnosis is marked flaky.</p>}
        </section>
      </div>
      <div className="insights-detail-grid secondary-insights-grid">
        <section className="panel panel-pad"><h2 className="panel-title">Feedback and learning history</h2><p className="panel-subtitle">Feedback attached to incidents returned by the current API.</p>{feedbackIncidents.length ? <ul className="related-list">{feedbackIncidents.map((incident) => <li key={incident.id}><div><strong>{incident.dag_id}.{incident.task_id}</strong><small>{incident.feedback.root_cause || incident.feedback.fix || "Feedback recorded"}</small></div><span className="status-diagnosed">Recorded</span></li>)}</ul> : <div className="unavailable-state">No feedback records are present in the current incident response.</div>}</section>
        <section className="panel panel-pad"><h2 className="panel-title">Memory retention</h2><p className="unavailable-state">Retention policy and index coverage are not exposed by the current frontend API.</p><div className="subsection-label">Memory currently used in diagnosis</div><p className="unavailable-note">{incidents.filter((incident) => incident.diagnosis?.memory_used).length} returned diagnoses indicate memory usage. This reflects incident responses, not total memory-bank contents.</p></section>
      </div>

      <section className="panel panel-pad brief-panel">
        <div className="panel-heading-row"><div><h2 className="panel-title">Pipeline Health Brief</h2><p className="panel-subtitle">A summary generated from the agent's incident memory.</p></div>
          <button onClick={runReflect} disabled={loadingBrief} className="button-primary">{loadingBrief ? "Reflecting..." : "Generate from memory"}</button>
        </div>
        {briefError && <p className="inline-error" role="alert">{briefError}</p>}
        {brief ? <p className="brief-text">{brief}</p> : <p className="empty-hint">Uses Hindsight reflect over everything the agent has learned.</p>}
      </section>
    </div>
  );
}
