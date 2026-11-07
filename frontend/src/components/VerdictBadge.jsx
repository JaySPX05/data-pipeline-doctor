const STYLES = {
  KNOWN_ISSUE: "badge-blue",
  FLAKY: "badge-amber",
  UPSTREAM_CHANGE: "badge-purple",
  NEW: "badge-red",
};

export default function VerdictBadge({ verdict }) {
  if (!verdict) return <span className="muted" style={{ fontSize: 9 }}>Pending</span>;
  return <span className={`badge ${STYLES[verdict] || "badge-blue"}`}>{verdict.replaceAll("_", " ")}</span>;
}
