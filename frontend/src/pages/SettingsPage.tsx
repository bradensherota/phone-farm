import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useToast } from "../components/common";

const FIELDS: { key: string; label: string; help: string; type: "bool" | "number" }[] = [
  { key: "runner_enabled", label: "Runner enabled", help: "Master switch. Off = nothing posts anywhere.", type: "bool" },
  { key: "pace_multiplier", label: "Pace multiplier", help: "Multiplies every phone's gap between actions. 1.0 = as configured per phone. Use 0.05 while testing simulated phones.", type: "number" },
  { key: "max_attempts", label: "Max attempts", help: "How many tries before an action is marked failed (retries wait ~3 minutes).", type: "number" },
  { key: "sim_step_seconds", label: "Simulated step seconds", help: "How long each fake tap takes on simulated phones.", type: "number" },
  { key: "sim_failure_rate", label: "Simulated failure rate", help: "0 to 1. Inject random failures on simulated phones to test retries and the activity view.", type: "number" },
];

export default function SettingsPage() {
  const [values, setValues] = useState<Record<string, string>>({});
  const [saved, setSaved] = useState<Record<string, string>>({});
  const toast = useToast();
  useEffect(() => { api.settings().then((s) => { setValues(s); setSaved(s); }); }, []);
  const dirty = JSON.stringify(values) !== JSON.stringify(saved);
  const save = async () => {
    try { const s = await api.putSettings(values); setValues(s); setSaved(s); toast("Settings saved"); }
    catch (e: any) { toast(e.message, "err"); }
  };
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Settings</h1><div className="sub">Local to this Mac. Stored in data/farm.db.</div></div>
        <button className="btn primary" disabled={!dirty} onClick={save}>Save</button>
      </div>
      <div className="settings-grid">
        {FIELDS.map((f) => (
          <div className="card" key={f.key}>
            <div className="card-body">
              <div className="row" style={{ marginBottom: 6 }}>
                <b className="grow">{f.label}</b>
                {f.type === "bool" ? (
                  <label className="switch"><input type="checkbox" checked={values[f.key] === "true"} onChange={(e) => setValues({ ...values, [f.key]: e.target.checked ? "true" : "false" })} /></label>
                ) : (
                  <input className="input" style={{ width: 110 }} type="number" step="any" value={values[f.key] ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                )}
              </div>
              <div className="small dim">{f.help}</div>
            </div>
          </div>
        ))}
        <div className="card">
          <div className="card-body">
            <b>Real phones</b>
            <div className="small dim" style={{ marginTop: 6 }}>
              iPhone drivers are not wired in yet. When the new Mac with Xcode is set up, WebDriverAgent goes on each phone and the simulated phones here get replaced by the real ones. The planner, schedule, and activity log carry over unchanged.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
