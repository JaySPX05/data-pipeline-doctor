import { useEffect, useRef, useState } from "react";
import { api, USE_MOCKS } from "../api/client.js";

const EXAMPLES = USE_MOCKS
  ? ["Why does load_orders fail?", "Which column was renamed?", "FreshFarm order feed"]
  : ["Why does load_orders fail?", "Which tasks are flaky?", "What did the Payments team change?"];

export default function Memory() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [searchError, setSearchError] = useState(null);
  const [memoryStatus, setMemoryStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState(null);
  const latestSearch = useRef(0);

  useEffect(() => {
    let active = true;
    api.memoryStatus()
      .then((status) => { if (active) setMemoryStatus(status); })
      .catch(() => { if (active) setStatusError("Could not read memory service status from the backend."); })
      .finally(() => { if (active) setStatusLoading(false); });
    return () => { active = false; };
  }, []);

  const search = async (query) => {
    const searchId = ++latestSearch.current;
    const normalizedQuery = String(query || "").trim();
    setQ(query);
    setSearchError(null);

    if (!normalizedQuery) {
      setResults(null);
      setLoading(false);
      setSearchError("Enter a question or keyword to search memory.");
      return;
    }

    setLoading(true);
    setResults(null);
    try {
      const matches = await api.searchMemory(normalizedQuery);
      if (searchId !== latestSearch.current) return;
      setResults(Array.isArray(matches) ? matches : []);

      if (!USE_MOCKS) {
        try {
          setMemoryStatus(await api.memoryStatus());
          setStatusError(null);
        } catch {
          setStatusError("Could not refresh memory service status after recall.");
        }
      }
    } catch (error) {
      if (searchId === latestSearch.current) {
        setSearchError(error instanceof Error ? error.message : "Memory search failed. Please try again.");
      }
    } finally {
      if (searchId === latestSearch.current) setLoading(false);
    }
  };

  const statusMessage = USE_MOCKS
    ? "Mock mode is on. Results are sample data from the frontend; no learned memory service is queried."
    : statusLoading
      ? "Checking memory service status…"
      : statusError
        ? statusError
        : memoryStatus?.enabled === false
          ? "Memory is disabled in the backend. Enable MEMORY_ENABLED to use Hindsight recall."
          : memoryStatus?.last_error
            ? "Hindsight reported a recall error. Check that the configured service and bank are available; details are in the backend logs."
            : memoryStatus?.enabled
              ? "Memory is enabled. Service errors are checked again after each recall."
              : "Memory service status is unavailable.";

  const emptyMessage = USE_MOCKS
    ? "No sample memories match this query. Try another keyword; mock mode contains only the bundled examples."
    : memoryStatus?.enabled === false
      ? "Memory is disabled in the backend, so no learned memories can be recalled."
      : memoryStatus?.last_error
        ? "Hindsight could not complete recall. Check the memory service status and backend logs."
        : "No memories matched this query in the configured Hindsight bank. Only memories already retained in that bank can be found.";

  return (
    <div className="memory-page">
      <div className="page-heading"><div><h1>What the agent remembers</h1><p>Search incident history and retained team knowledge.</p></div></div>
      <section className="panel panel-pad memory-search-panel">
        <form onSubmit={(event) => { event.preventDefault(); search(q); }} className="search-row">
          <input className="field" value={q} onChange={(event) => setQ(event.target.value)} placeholder="Ask the team's incident memory..." aria-label="Search incident memory" aria-describedby="memory-status" />
          <button className="button-primary" disabled={loading}>{loading ? "Recalling…" : "Recall"}</button>
        </form>
        <div id="memory-status" className="muted" role="status" aria-live="polite" style={{ fontSize: 10 }}>{statusMessage}</div>
        {searchError && <p role="alert" style={{ color: "var(--danger)", margin: 0, fontSize: 10 }}>{searchError}</p>}
        <div className="memory-examples"><span className="section-label">Try an example</span><div className="example-list">
          {EXAMPLES.map((example) => <button type="button" key={example} onClick={() => search(example)} className="example-chip">{example}</button>)}
        </div></div>
      </section>

      {loading && <section className="panel panel-pad" role="status" aria-live="polite" style={{ marginTop: 14, color: "var(--muted)", fontSize: 10 }}>Searching memory…</section>}
      {!loading && results && <section className="panel memory-results">
        <div className="panel-pad memory-results-heading"><div><h2 className="panel-title">Memory results{USE_MOCKS && <span className="badge badge-amber" style={{ marginLeft: 7 }}>Sample data</span>}</h2><p className="panel-subtitle">{results.length} result{results.length === 1 ? "" : "s"}</p></div></div>
        {results.length ? <ul className="result-list">{results.map((memory, i) => (
          <li key={memory.memory_id || i} className="result-item">
            <span className="result-type">{memory.type || "memory"}</span>
            {memory.occurred_at && <span className="mono muted" style={{ marginRight: 8 }}>{memory.occurred_at.slice(0, 10)}</span>}
            {memory.text}
          </li>
        ))}</ul> : <p className="empty-state">{emptyMessage}</p>}
      </section>}
    </div>
  );
}
