import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./lib/api";
import { useState } from "react";
import { ConfirmProvider, StatusDot, ToastProvider, usePoll } from "./components/common";
import DevicesPage from "./pages/DevicesPage";
import DevicePage from "./pages/DevicePage";
import PlannerPage from "./pages/PlannerPage";
import ActivityPage from "./pages/ActivityPage";
import SettingsPage from "./pages/SettingsPage";

function Rail() {
  const { data: devices } = usePoll(api.devices, 5000);
  const { data: runner } = usePoll(api.runner, 5000);
  return (
    <aside className="rail">
      <div className="brand"><span className="logo">PF</span> Phone Farm</div>
      <nav className="nav">
        <NavLink to="/" end>Phones</NavLink>
        <div className="sub">
          {(devices || []).map((d) => (
            <NavLink key={d.id} to={`/devices/${d.id}`}>
              <StatusDot connected={d.connected} busy={d.busy} /> {d.name}
            </NavLink>
          ))}
        </div>
        <div className="rail-section">Plan</div>
        <NavLink to="/planner">Week planner</NavLink>
        <NavLink to="/activity">Activity</NavLink>
        <div className="rail-section">System</div>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <div className="rail-foot">
        <span>
          <StatusDot connected={!!runner?.enabled} busy={!!runner && runner.busy_devices.length > 0} />{" "}
          Runner {runner ? (runner.enabled ? (runner.busy_devices.length ? `posting on ${runner.busy_devices.length}` : "idle") : "paused") : "…"}
          {runner?.version && <span className="dim" title="Phone Farm version (the date of the zip it was installed from)" style={{ marginLeft: 8, fontSize: 11 }}>v{runner.version}</span>}
        </span>
        {runner?.license?.message && <span style={{ color: runner.license.active ? "var(--amber)" : "var(--red)" }} title={runner.license.message}>{runner.license.message}</span>}
      </div>
    </aside>
  );
}

function PausedOverlay() {
  const { data: runner, reload } = usePoll(api.runner, 5000);
  const [checking, setChecking] = useState(false);
  const [note, setNote] = useState("");
  const lic = runner?.license;
  if (!lic || lic.active) return null;
  const title = lic.state === "expired" ? "Trial Over" : "Service Paused";
  const check = async () => {
    setChecking(true); setNote("");
    try { const r = await api.licenseCheckin(); setNote(r.error ? r.error : r.active ? "Active again. One moment…" : "Still paused."); reload(); }
    catch (e: any) { setNote(e.message); }
    finally { setChecking(false); }
  };
  return (
    <div className="paused-overlay">
      <div className="paused-card">
        <div className="paused-title">{title}</div>
        <div className="paused-msg">{lic.message}</div>
        <div className="small dim" style={{ marginTop: 10 }}>Your phones, accounts and plans are kept. Nothing posts until this Mac is active again.</div>
        <div className="row" style={{ marginTop: 18, justifyContent: "center", gap: 10 }}>
          <button className="btn primary" disabled={checking} onClick={check}>{checking ? "Checking…" : "Check again"}</button>
        </div>
        {note && <div className="small" style={{ marginTop: 10, color: "var(--text-2)" }}>{note}</div>}
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <ConfirmProvider>
      <PausedOverlay />
      <div className="shell">
        <Rail />
        <main className="main">
          <Routes>
            <Route path="/" element={<DevicesPage />} />
            <Route path="/devices/:id" element={<DevicePage />} />
            <Route path="/planner" element={<PlannerPage />} />
            <Route path="/activity" element={<ActivityPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
      </ConfirmProvider>
    </ToastProvider>
  );
}
