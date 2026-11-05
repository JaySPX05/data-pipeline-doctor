import { NavLink, Route, Routes } from "react-router-dom";
import Inbox from "./pages/Inbox.jsx";
import Incident from "./pages/Incident.jsx";
import Memory from "./pages/Memory.jsx";
import Insights from "./pages/Insights.jsx";
import { USE_MOCKS } from "./api/client.js";

const link = ({ isActive }) =>
  `px-3 py-1.5 rounded-md text-sm ${isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-200"}`;

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
          <span className="font-semibold">Pipeline Failure Doctor</span>
          <nav className="flex gap-1">
            <NavLink to="/" end className={link}>Incidents</NavLink>
            <NavLink to="/insights" className={link}>Insights</NavLink>
            <NavLink to="/memory" className={link}>Memory</NavLink>
          </nav>
          {USE_MOCKS && <span className="ml-auto rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">mock data</span>}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-6">
        <Routes>
          <Route path="/" element={<Inbox />} />
          <Route path="/incidents/:id" element={<Incident />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/memory" element={<Memory />} />
        </Routes>
      </main>
    </div>
  );
}
