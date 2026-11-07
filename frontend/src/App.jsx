import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import Inbox from "./pages/Inbox.jsx";
import Incident from "./pages/Incident.jsx";
import Memory from "./pages/Memory.jsx";
import Insights from "./pages/Insights.jsx";
import Overview from "./pages/Overview.jsx";
import PipelineMonitor from "./pages/PipelineMonitor.jsx";
import { USE_MOCKS } from "./api/client.js";

const navItems = [
  { to: "/overview", label: "Overview", icon: "◫" },
  { to: "/", label: "Incident Inbox", icon: "▤", end: true },
  { to: "/pipelines", label: "Pipeline Monitor", icon: "⌁" },
  { to: "/insights", label: "Memory & Insights", icon: "◉" },
];

function ThemeButton({ theme, onToggle }) {
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button type="button" className="theme-toggle" onClick={onToggle} aria-label={`Switch to ${next} mode`} title={`Switch to ${next} mode`}>
      <span aria-hidden="true">{theme === "dark" ? "☼" : "◐"}</span>
      <span className="theme-toggle-label">{theme === "dark" ? "Dark" : "Light"}</span>
    </button>
  );
}

export default function App() {
  const [theme, setTheme] = useState(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    try {
      localStorage.setItem("pipeline-doctor-theme", theme);
    } catch {
      // The theme still works for this session when storage is unavailable.
    }
  }, [theme]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-mark" aria-hidden="true">P</div>
          <div className="brand-copy">
            <span className="brand-name">Pipeline Failure Doctor</span>
            <span className="brand-tag">BETA</span>
          </div>
        </div>
        <div className="workspace-label">
          <span className="workspace-avatar">N</span>
          <span><strong>Data pipelines</strong><small>QuickBasket (demo)</small></span>
        </div>
        <nav className="primary-nav" aria-label="Main navigation">
          {navItems.map(({ to, label, icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
              <span className="nav-icon" aria-hidden="true">{icon}</span>{label}
              {to === "/" && USE_MOCKS && <span className="nav-indicator" aria-label="Mock data active">•</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-section-label">ENVIRONMENT</div>
        <div className="environment-row"><span className="status-dot" />QuickBasket (demo)</div>
        <div className="environment-detail">Pipeline incident workspace</div>
        {USE_MOCKS && <div className="sidebar-mode">Mock data enabled</div>}
        <div className="sidebar-spacer" />
        <div className="sidebar-footer">Pipeline Failure Doctor</div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title">Incident operations</div>
          <div className="topbar-actions">
            {USE_MOCKS && <span className="mock-pill">Mock data</span>}
            <ThemeButton theme={theme} onToggle={() => setTheme((value) => value === "dark" ? "light" : "dark")} />
          </div>
        </header>
        <main className="page-content">
          <Routes>
            <Route path="/overview" element={<Overview />} />
            <Route path="/" element={<Inbox />} />
            <Route path="/incidents/:id" element={<Incident />} />
            <Route path="/pipelines" element={<PipelineMonitor />} />
            <Route path="/insights" element={<Insights />} />
            <Route path="/memory" element={<Memory />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
