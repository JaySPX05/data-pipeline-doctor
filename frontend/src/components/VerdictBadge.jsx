const STYLES = {
  KNOWN_ISSUE: "bg-blue-100 text-blue-800",
  FLAKY: "bg-amber-100 text-amber-800",
  UPSTREAM_CHANGE: "bg-purple-100 text-purple-800",
  NEW: "bg-red-100 text-red-800",
};

export default function VerdictBadge({ verdict }) {
  if (!verdict) return <span className="text-xs text-slate-400">pending</span>;
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STYLES[verdict] || "bg-slate-100"}`}>
      {verdict.replace("_", " ")}
    </span>
  );
}
