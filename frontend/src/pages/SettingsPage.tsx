import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useConfirm, usePoll, useToast } from "../components/common";

function UpdatesCard() {
  const toast = useToast();
  const ask = useConfirm();
  const { data: runner } = usePoll(api.runner, 5000);
  const [check, setCheck] = useState<{ installed_from_git: boolean; local: string; remote: string; behind: boolean; error: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [updating, setUpdating] = useState<string | null>(null);  // version at the time Update was pressed
  const doCheck = async () => { setChecking(true); try { setCheck(await api.updateCheck()); } catch (e: any) { toast(e.message, "err"); } finally { setChecking(false); } };
  useEffect(() => { doCheck(); }, []);
  useEffect(() => { if (updating && runner?.version && runner.version !== updating) { setUpdating(null); toast(`Updated to ${runner.version}`); doCheck(); } }, [runner?.version]);
  useEffect(() => {  // same version after the restart (already current): release the button after the install has had time to finish
    if (!updating) return;
    const t = window.setTimeout(() => { setUpdating(null); doCheck(); toast("Update finished"); }, 180000);
    return () => window.clearTimeout(t);
  }, [updating]);
  const update = async () => {
    if (!(await ask("Update Phone Farm from GitHub now? The app restarts itself; takes a minute or two. Nothing is lost.", "Update"))) return;
    try { await api.updateNow(); setUpdating(runner?.version || "?"); toast("Updating… the page will come back on its own"); }
    catch (e: any) { toast(e.message, "err"); }
  };
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="card-body">
        <div className="row" style={{ marginBottom: 6 }}>
          <b className="grow">Updates</b>
          <span className="small dim">installed: v{runner?.version || "…"}</span>
          <button className="btn sm" onClick={doCheck} disabled={checking}>{checking ? "Checking…" : "Check for updates"}</button>
          <button className="btn sm primary" onClick={update} disabled={!check?.installed_from_git || !!updating || !!(runner && runner.busy_devices.length)} title={runner?.busy_devices.length ? "A phone is posting; update when it's idle" : "Pull the latest from GitHub and reinstall"}>{updating ? "Updating…" : "Update now"}</button>
        </div>
        <div className="small dim">
          {check === null ? "Checking GitHub…" : check.error ? check.error : check.behind ? `A newer version is on GitHub (${check.remote}). Press Update now.` : "You're on the latest version."}
        </div>
      </div>
    </div>
  );
}

const FIELDS: { key: string; label: string; help: string; type: "bool" | "number" | "text" }[] = [
  { key: "runner_enabled", label: "Runner enabled", help: "Master switch. Off = nothing posts anywhere.", type: "bool" },
  { key: "your_name", label: "Your name", help: "Shown to Braden next to this Mac in his list of installs, so he knows whose it is.", type: "text" },
  { key: "apple_team_id", label: "Apple Team ID", help: "10 characters, used to sign the runner on the phones. Leave blank to use the Apple ID signed into Xcode (Xcode → Settings → Accounts). Find it in Xcode: select that account, the team name is listed; or at developer.apple.com → Membership.", type: "text" },
  { key: "default_link_instagram", label: "Default Instagram story link", help: "Given to a new Instagram account when you don't enter a link. Each account's link can still be changed on its phone page.", type: "text" },
  { key: "default_link_tiktok", label: "Default TikTok story link", help: "Same, for new TikTok accounts.", type: "text" },
  { key: "auto_start", label: "Start days automatically at their time", help: "Off: nothing runs until you press Start today on the Phones page (or Run day / Run now). On: each phone's queue starts by itself at the day's start time.", type: "bool" },
  { key: "tiktok_stories", label: "TikTok stories", help: "Off: TikTok stories can't be planned and any planned ones are cancelled. Turn on again once link stickers work by hand on the phone.", type: "bool" },
  { key: "disconnect_after_queue", label: "Disconnect the runner after its day is done", help: "On: when a phone's queue finishes, its runner is stopped and the Automation Running overlay goes away. Connect, Start today or Run day start it again. Off: the runner stays on all day.", type: "bool" },
  { key: "sleep_after_queue", label: "Lock the phone after its day is done", help: "Off: the phone stays unlocked after posting (Auto-Lock should be Never). On: the app locks the screen when the queue finishes. The runner has died twice after an hour or so locked, so leave this off until an overnight test passes.", type: "bool" },
  { key: "queue_auto", label: "App manages the queue album", help: "Off: you add each planned clip to the phone's queue album in Photos yourself and remove it after. On: the app fills the album before the day starts and empties it after posting (slower and less reliable).", type: "bool" },
  { key: "dry_run", label: "Dry run", help: "Every flow runs all the way to the final Post / Share button and stops there without posting. Turn off for real posting.", type: "bool" },
  { key: "pace_multiplier", label: "Pace multiplier", help: "Multiplies every phone's gap between actions. 1.0 = as configured per phone. Use 0.05 while testing simulated phones.", type: "number" },
  { key: "require_blocks", label: "Require blocks of days", help: "On: a kind of post on an account (reels, trial reels, stories) only runs when that account has it planned on the block length of consecutive days. Plan four days of stories in a row and those stories unlock; a kind you don't plan isn't required. The fill screen won't save a partial block.", type: "bool" },
  { key: "block_days", label: "Block length (days)", help: "How many consecutive days of a kind unlock it. Default 4.", type: "number" },
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
  // switches take effect the moment they're flipped; only the number fields wait for Save
  const flip = async (key: string, label: string, on: boolean) => {
    const v = on ? "true" : "false";
    setValues((cur) => ({ ...cur, [key]: v }));
    try { const s = await api.putSettings({ [key]: v }); setValues(s); setSaved(s); toast(`${label}: ${on ? "on" : "off"}`); }
    catch (e: any) { toast(e.message, "err"); }
  };
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Settings</h1><div className="sub">Local to this Mac. Stored in data/farm.db.</div></div>
        <button className="btn primary" disabled={!dirty} onClick={save} title="Switches save on their own; this saves the number fields">Save numbers</button>
      </div>
      <UpdatesCard />
      <div className="settings-grid">
        {FIELDS.map((f) => (
          <div className="card" key={f.key}>
            <div className="card-body">
              <div className="row" style={{ marginBottom: 6 }}>
                <b className="grow">{f.label}</b>
                {f.type === "bool" ? (
                  <label className="switch" title="Takes effect immediately"><input type="checkbox" checked={values[f.key] === "true"} onChange={(e) => flip(f.key, f.label, e.target.checked)} /></label>
                ) : f.type === "text" ? (
                  <span className="row">
                    <input className="input" style={{ width: 260 }} type="text" value={values[f.key] ?? ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                    {(f.key === "default_link_instagram" || f.key === "default_link_tiktok") && (
                      <button className="btn sm" disabled={values[f.key] !== saved[f.key] || !(values[f.key] || "").trim()} title={values[f.key] !== saved[f.key] ? "Save first, then apply" : "Put this link on every account of this platform and on their stories that haven't posted yet"}
                              onClick={async () => { try { const r = await api.applyDefaultLink(f.key === "default_link_instagram" ? "instagram" : "tiktok"); toast(`${r.link} set on ${r.accounts} account(s) and ${r.pending_stories} pending stor${r.pending_stories === 1 ? "y" : "ies"}`); } catch (e: any) { toast(e.message, "err"); } }}>Apply to all accounts</button>
                    )}
                  </span>
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
            <b>How settings save</b>
            <div className="small dim" style={{ marginTop: 6 }}>
              Switches take effect the moment you flip them. Number fields are saved with the button at the top.
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
