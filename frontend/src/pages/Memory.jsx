// Memory explorer: ask what the agent knows. Owner: P6.
import { useState } from "react";
import { api } from "../api/client.js";

const EXAMPLES = ["Why does load_orders fail?", "Which tasks are flaky?", "What did the Payments team change?"];

export default function Memory() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);

  const search = async (query) => {
    setQ(query);
    setResults(await api.searchMemory(query));
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">What the agent remembers</h1>
      <form onSubmit={(e) => { e.preventDefault(); search(q); }} className="flex gap-2">
        <input className="flex-1 rounded border bg-white p-2" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask the team's incident memory..." />
        <button className="rounded bg-slate-900 px-4 text-white">Recall</button>
      </form>
      <div className="flex gap-2">
        {EXAMPLES.map((ex) => (
          <button key={ex} onClick={() => search(ex)} className="rounded-full border bg-white px-3 py-1 text-xs hover:bg-slate-100">{ex}</button>
        ))}
      </div>
      {results && (
        <ul className="space-y-2">
          {results.map((m, i) => (
            <li key={m.memory_id || i} className="rounded border bg-white p-3 text-sm">
              <span className="mr-2 rounded bg-slate-100 px-1.5 text-xs text-slate-500">{m.type || "memory"}</span>
              {m.occurred_at && <span className="mr-2 font-mono text-xs text-slate-500">{m.occurred_at.slice(0, 10)}</span>}
              {m.text}
            </li>
          ))}
          {results.length === 0 && <li className="text-sm text-slate-400">Nothing found.</li>}
        </ul>
      )}
    </div>
  );
}
