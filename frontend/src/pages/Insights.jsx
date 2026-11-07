// Learning curve + Pipeline Health Brief (Hindsight reflect). Owner: P6.
import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client.js";

export default function Insights() {
  const [curve, setCurve] = useState([]);
  const [brief, setBrief] = useState(null);
  const [loadingBrief, setLoadingBrief] = useState(false);

  useEffect(() => {
    api.learningCurve().then(setCurve);
  }, []);

  const runReflect = async () => {
    setLoadingBrief(true);
    setBrief((await api.reflect()).text);
    setLoadingBrief(false);
  };

  return (
    <div className="space-y-6">
      <section className="rounded-lg border bg-white p-5">
        <h2 className="mb-1 font-semibold">The agent gets better every day</h2>
        <p className="mb-4 text-sm text-slate-500">Share of failures diagnosed with confidence (not NEW, at least 70% confident), and verdict accuracy, per day.</p>
        <div className="h-72">
          <ResponsiveContainer>
            <LineChart data={curve}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="day" tickFormatter={(d) => d.slice(5)} />
              <YAxis unit="%" domain={[0, 100]} />
              <Tooltip />
              <Legend />
              <Line type="monotone" dataKey="auto_diagnosed_pct" name="Auto-diagnosed %" stroke="#2563eb" strokeWidth={2} />
              <Line type="monotone" dataKey="accuracy_pct" name="Accuracy %" stroke="#16a34a" strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="rounded-lg border bg-white p-5">
        <div className="mb-3 flex items-center">
          <h2 className="font-semibold">Pipeline Health Brief</h2>
          <button onClick={runReflect} className="ml-auto rounded bg-slate-900 px-3 py-1.5 text-sm text-white">
            {loadingBrief ? "Reflecting..." : "Generate from memory"}
          </button>
        </div>
        {brief ? <p className="whitespace-pre-line text-sm">{brief}</p> : <p className="text-sm text-slate-400">Uses Hindsight reflect over everything the agent has learned.</p>}
      </section>
    </div>
  );
}