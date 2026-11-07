
import { useState } from "react";
import { api } from "../api/client.js";

const EXAMPLES = [
  "Why does load_orders fail?",
  "Which tasks are flaky?",
  "What did the Payments team change?"
];

export default function Memory() {
  const [q, setQ] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const search = async (query) => {
    const searchQuery = query.trim();

    if (!searchQuery) {
      setError("Please enter a question before searching.");
      return;
    }

    setQ(searchQuery);
    setLoading(true);
    setError("");
    setResults(null);

    try {
      const data = await api.searchMemory(searchQuery);
      setResults(data);
    } catch (err) {
      setError("Unable to retrieve memories. Please check the server and try again.");
      console.error("Memory search failed:", err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">
          What the agent remembers
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Search previous incidents, failures and team decisions.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          search(q);
        }}
        className="flex gap-2"
      >
        <input
          className="flex-1 rounded-lg border bg-white p-3 outline-none focus:ring-2 focus:ring-blue-400"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask the team's incident memory..."
        />

        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-slate-900 px-5 py-2 text-white disabled:opacity-50"
        >
          {loading ? "Searching..." : "Recall"}
        </button>
      </form>

      <div>
        <p className="mb-2 text-sm font-medium text-slate-600">
          Suggested questions
        </p>

        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              disabled={loading}
              onClick={() => search(ex)}
              className="rounded-full border bg-white px-3 py-2 text-xs hover:bg-slate-100 disabled:opacity-50"
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <p className="rounded-lg bg-blue-50 p-3 text-sm text-blue-700">
          Searching incident memories...
        </p>
      )}

      {error && (
        <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {results && (
        <div className="space-y-3">
          <h2 className="font-semibold">
            Memory Search Results ({results.length})
          </h2>

          {results.length === 0 ? (
            <p className="rounded-lg border bg-white p-4 text-sm text-slate-500">
              No matching memories found.
            </p>
          ) : (
            <ul className="space-y-3">
              {results.map((m, i) => (
                <li
                  key={m.memory_id || i}
                  className="rounded-lg border bg-white p-4 shadow-sm"
                >
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
                      {m.type || "Memory"}
                    </span>

                    {m.occurred_at && (
                      <span className="text-xs text-slate-500">
                        {m.occurred_at.slice(0, 10)}
                      </span>
                    )}
                  </div>

                  <p className="text-sm leading-6 text-slate-700">
                    {m.text || "No memory description available."}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
