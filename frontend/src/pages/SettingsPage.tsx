import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useToast } from "../components/common";

const FIELDS: { key: string; label: string; help: string; type: "bool" | "number" }[] = [
  { key: "runner_enabled", label: "Runner enabled", help: "Master switch. Off = nothing posts anywhere.", type: "bool" },
  { key: "auto_start", label: "Start days automatically at their time", help: "Off: nothing runs until you press Start today on the Phones page (or Run day / Run now). On: each phone's queue starts by itself at the day's start time.", type: "bool" },
  { key: "tiktok_stories", label: "TikTok stories", help: "Off: TikTok stories can't be planned and any planned ones are cancelled. Turn on again once link stickers work by hand on the phone.", type: "bool" },
  { key: "sleep_after_queue", label: "Lock the phone after its day is done", help: "Off: the phone stays unlocked after posting (Auto-Lock should be Never). On: the app locks the screen when the queue finishes. The runner has died twice after an hour or so locked, so leave this off until an overnight test passes.", type: "bool" },
  { key: "queue_auto", label: "App manages the queue album", help: "Off: you add each planned clip to the phone's queue album in Photos yourself and remove it after. On: the app fills the album before the day starts and empties it after posting (slower and less reliable).", type: "bool" },
  { key: "dry_run", label: "Dry run", help: "Every flow runs all the way to the final Post / Share button and stops there without posting. Turn off for real posting.", type: "bool" },
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
