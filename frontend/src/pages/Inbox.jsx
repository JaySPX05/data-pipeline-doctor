// Incident inbox. Owner: P5.  TODO: filters (status, verdict, DAG), auto-refresh during replay.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client.js";
import VerdictBadge from "../components/VerdictBadge.jsx";

export default function Inbox() {
  const [incidents, setIncidents] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    const load = () => api.listIncidents().then(setIncidents).catch((e) => setError(e.message));
    load();
    const t = setInterval(load, 3000); // live updates while replay.py runs
    return () => clearInterval(t);
  }, []);

  if (error) return <p className="text-red-600">Could not load incidents: {error}</p>;

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Incidents</h1>
      <table className="w-full overflow-hidden rounded-lg border bg-white text-sm">
        <thead className="bg-slate-100 text-left text-xs uppercase text-slate-500">
          <tr>
            <th className="px-4 py-2">When</th>
            <th className="px-4 py-2">Task</th>
            <th className="px-4 py-2">Error</th>
            <th className="px-4 py-2">Verdict</th>
            <th className="px-4 py-2">Confidence</th>
            <th className="px-4 py-2">Status</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => (
            <tr key={i.id} className="border-t hover:bg-slate-50">
              <td className="px-4 py-2 font-mono text-xs">{i.occurred_at.replace("T", " ").slice(0, 16)}</td>
              <td className="px-4 py-2">
                <Link to={`/incidents/${i.id}`} className="text-blue-700 hover:underline">
                  {i.dag_id}.{i.task_id}
                </Link>
              </td>
              <td className="px-4 py-2 font-mono text-xs">{i.error_class}</td>
              <td className="px-4 py-2"><VerdictBadge verdict={i.diagnosis?.verdict} /></td>
              <td className="px-4 py-2">{i.diagnosis ? `${Math.round(i.diagnosis.confidence * 100)}%` : "-"}</td>
              <td className="px-4 py-2 text-slate-500">{i.status}</td>
            </tr>
          ))}
          {incidents.length === 0 && (
            <tr>
              <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                No incidents yet. Run scripts/replay.py.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
