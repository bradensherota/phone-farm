import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import type { Account, Device, Job, Media, Platform } from "../lib/api";
import { actionMeta, api, fmtDateTime, ymd } from "../lib/api";
import { PhoneScreen, Pill, PlatformIcon, StatusDot, usePoll, useToast } from "../components/common";
import { MediaPreview, RollGrid } from "../components/MediaPicker";
import JobDetail from "../components/JobDetail";

type Tab = "accounts" | "roll" | "schedule";

export default function DevicePage() {
  const { id = "" } = useParams();
  const { data: device, reload } = usePoll(() => api.device(id), 3000, [id]);
  const [tab, setTab] = useState<Tab>("accounts");
  if (!device) return <div className="page"><div className="empty">Loading…</div></div>;
  return (
    <div className="page">
      <DeviceHeader device={device} onChanged={reload} />
      <div className="device-layout">
        <div>
          <PhoneScreen deviceId={device.id} connected={device.connected} intervalMs={1200} />
          <div className="small dim" style={{ marginTop: 10, textAlign: "center" }}>
            {device.busy ? "Posting now" : device.connected ? "Idle" : "Not connected"}
          </div>
        </div>
        <div>
          <div className="tabs">
            <button className={tab === "accounts" ? "active" : ""} onClick={() => setTab("accounts")}>Accounts ({device.accounts.length})</button>
            <button className={tab === "roll" ? "active" : ""} onClick={() => setTab("roll")}>Camera roll ({device.media_count})</button>
            <button className={tab === "schedule" ? "active" : ""} onClick={() => setTab("schedule")}>Schedule</button>
          </div>
          {device.kind === "ios" && <WakeTestCard device={device} />}
          {tab === "accounts" && <AccountsTab device={device} onChanged={reload} />}
          {tab === "roll" && <RollTab device={device} onChanged={reload} />}
          {tab === "schedule" && <ScheduleTab device={device} />}
        </div>
      </div>
    </div>
  );
}

function DeviceHeader({ device, onChanged }: { device: Device; onChanged: () => void }) {
  const toast = useToast();
  const nav = useNavigate();
  const [form, setForm] = useState({ name: device.name, routine_start: device.routine_start, gap_min_s: device.gap_min_s, gap_max_s: device.gap_max_s, story_album: device.story_album, queue_album: device.queue_album, prepare_lead_min: device.prepare_lead_min });
  useEffect(() => setForm({ name: device.name, routine_start: device.routine_start, gap_min_s: device.gap_min_s, gap_max_s: device.gap_max_s, story_album: device.story_album, queue_album: device.queue_album, prepare_lead_min: device.prepare_lead_min }), [device.id, device.name, device.routine_start, device.gap_min_s, device.gap_max_s, device.story_album, device.queue_album, device.prepare_lead_min]);
  const [passcode, setPasscode] = useState("");
  const dirty = passcode !== "" || form.name !== device.name || form.routine_start !== device.routine_start || form.gap_min_s !== device.gap_min_s || form.gap_max_s !== device.gap_max_s || form.story_album !== device.story_album || form.queue_album !== device.queue_album || form.prepare_lead_min !== device.prepare_lead_min;
  const save = async () => {
    try { await api.patchDevice(device.id, passcode ? ({ ...form, passcode } as Partial<Device> & { passcode: string }) : form); setPasscode(""); toast("Saved"); onChanged(); } catch (e: any) { toast(e.message, "err"); }
  };
  const togglePause = async () => {
    await api.patchDevice(device.id, { paused: !device.paused });
    toast(device.paused ? "Phone resumed" : "Phone paused. Its queue will wait.");
    onChanged();
  };
  const [preparing, setPreparing] = useState(false);
  const prepare = async () => {
    setPreparing(true);
    try { const r = await api.prepareDevice(device.id); toast(r.message); onChanged(); }
    catch (e: any) { toast(e.message, "err"); }
    finally { setPreparing(false); }
  };
  const [clearing, setClearing] = useState(false);
  const clearQueue = async () => {
    if (!confirm(`Empty the "${device.queue_album}" album on ${device.name}? Media stays in the library.`)) return;
    setClearing(true);
    try { const r = await api.clearQueue(device.id); toast(r.cleared ? "Queue album emptied" : "Could not empty the album", r.cleared ? "ok" : "err"); onChanged(); }
    catch (e: any) { toast(e.message, "err"); }
    finally { setClearing(false); }
  };
  const reconnect = async () => {
    await api.connectDevice(device.id);
    toast("Starting the phone's runner… takes up to a minute");
    onChanged();
  };
  const remove = async () => {
    if (!confirm(`Remove ${device.name} and all of its accounts and planned posts?`)) return;
    try { await api.deleteDevice(device.id); toast("Removed"); nav("/"); } catch (e: any) { toast(e.message, "err"); }
  };
  return (
    <div className="page-head">
      <div>
        <div className="row">
          <StatusDot connected={device.connected} busy={device.busy} />
          <h1>{device.name}</h1>
          {device.paused && <Pill kind="paused">paused</Pill>}
          {device.connecting && <Pill kind="running">connecting…</Pill>}
        </div>
        <div className="sub">{device.model} · {device.kind === "sim" ? "simulated phone" : "USB"} · {device.id}</div>
        {device.error && !device.connecting && <div className="sub" style={{ color: "var(--red)" }}>{device.error}</div>}
      </div>
      <div className="row">
        {device.kind === "ios" && !device.connected && (
          <div className="field"><label>&nbsp;</label><button className="btn primary" disabled={device.connecting} onClick={reconnect}>{device.connecting ? "Connecting…" : "Connect"}</button></div>
        )}
        {device.kind === "ios" && device.connected && (
          <div className="field"><label>Screen</label><div className="row">
            <button className="btn sm" title="Lock the screen, exactly what the runner does after the day's last job" onClick={async () => { toast("Putting the phone to sleep…"); const r = await api.sleepDevice(device.id); toast(r.locked ? "Asleep (screen locked)" : `Could not sleep: ${r.error || "screen did not lock"}`, r.locked ? "ok" : "err"); }}>Sleep</button>
            <button className="btn sm" onClick={async () => { toast("Waking…"); const r = await api.wakeDevice(device.id); toast(r.locked ? "Still locked" : "Awake and unlocked", r.locked ? "err" : "ok"); }} title="Wake and unlock, exactly what the runner does before the first job">Wake</button>
          </div></div>
        )}
        <div className="field"><label>&nbsp;</label><button className="btn ghost danger" onClick={remove}>Remove</button></div>
        <div className="field"><label>Queue album</label><div className="row"><input className="input" style={{ width: 120 }} value={form.queue_album} placeholder="Farm Queue" onChange={(e) => setForm({ ...form, queue_album: e.target.value })} title="Album the app fills with scheduled items and empties after posting" /><input className="input" type="number" style={{ width: 70 }} value={form.prepare_lead_min} onChange={(e) => setForm({ ...form, prepare_lead_min: +e.target.value })} title="Minutes before the routine to fill it" /><span className="dim small">min before</span></div></div>
        <div className="field"><label>&nbsp;</label><button className="btn" disabled={preparing || device.kind === "sim"} onClick={prepare}>{preparing ? "Preparing…" : "Prepare album now"}</button></div>
        <div className="field"><label>&nbsp;</label><button className="btn ghost" disabled={clearing || device.kind === "sim"} onClick={clearQueue}>{clearing ? "Emptying…" : "Empty queue album"}</button></div>
        <div className="field"><label>Passcode {device.has_passcode ? <span className="dim">(set)</span> : <span className="dim">(none)</span>}</label><input className="input" type="password" style={{ width: 100 }} placeholder={device.has_passcode ? "••••••" : "none"} autoComplete="off" onChange={(e) => setPasscode(e.target.value)} value={passcode} title="Lock-screen passcode the runner types to wake this phone. Stored only in this Mac's local database." /></div>
        <div className="field"><label>Story album</label><input className="input" style={{ width: 120 }} value={form.story_album} placeholder="Stories" onChange={(e) => setForm({ ...form, story_album: e.target.value })} title="Photos album on this phone; items in it are picked from the album instead of Recents" /></div>
        <div className="field"><label>Routine start</label><input className="input" type="time" style={{ width: 120 }} value={form.routine_start} onChange={(e) => setForm({ ...form, routine_start: e.target.value })} /></div>
        <div className="field"><label>Gap between actions (s)</label>
          <div className="row">
            <input className="input" type="number" style={{ width: 80 }} value={form.gap_min_s} onChange={(e) => setForm({ ...form, gap_min_s: +e.target.value })} />
            <span className="dim">to</span>
            <input className="input" type="number" style={{ width: 80 }} value={form.gap_max_s} onChange={(e) => setForm({ ...form, gap_max_s: +e.target.value })} />
          </div>
        </div>
        <div className="field"><label>&nbsp;</label><button className="btn" disabled={!dirty} onClick={save}>Save</button></div>
        <div className="field"><label>&nbsp;</label><button className={`btn ${device.paused ? "primary" : ""}`} onClick={togglePause}>{device.paused ? "Resume phone" : "Pause phone"}</button></div>
      </div>
    </div>
  );
}

function WakeTestCard({ device }: { device: Device }) {
  const toast = useToast();
  const { data: t, reload } = usePoll(() => api.wakeTest(device.id), 10000, [device.id]);
  const [steps, setSteps] = useState("15, 30, 60, 180");
  if (!t) return null;
  const parsed = steps.split(/[,\s]+/).map((x) => parseInt(x, 10)).filter((n) => n > 0);
  const start = async () => {
    if (!confirm(`Lock ${device.name} now, then wake and re-lock it after ${parsed.join(", ")} minutes? Nothing is posted.`)) return;
    try { await api.startWakeTest(device.id, parsed); toast("Wake test started; phone locked"); reload(); } catch (e: any) { toast(e.message, "err"); }
  };
  const cancel = async () => { await api.cancelWakeTest(device.id); toast("Wake test stopped"); reload(); };
  const fmt = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const running = t.status === "running";
  // planned times per step, from the recorded events or the next_at for the upcoming one
  const eventsFor = (step: number) => t.events.filter((e) => e.step === step && (e.phase === "wake" || e.phase === "sleep"));
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <strong>Wake test</strong>
        <span className="small dim grow">Locks the phone, then wakes and re-locks it after each interval. Shows whether the runner survives long stretches locked.</span>
        {running ? <Pill kind="running">running · next {t.next_at ? fmt(t.next_at) : ""}</Pill> : t.status === "done" ? <Pill kind="done">finished</Pill> : t.status === "cancelled" ? <Pill kind="cancelled">stopped</Pill> : null}
      </div>
      <div className="card-body">
        {!running && (
          <div className="row" style={{ marginBottom: t.events.length ? 12 : 0 }}>
            <span className="small dim">Minutes between wakes</span>
            <input className="input" style={{ width: 160 }} value={steps} onChange={(e) => setSteps(e.target.value)} />
            <button className="btn sm primary" disabled={!device.connected || !parsed.length} onClick={start}>Start test</button>
          </div>
        )}
        {(running || t.events.length > 0) && (
          <table className="table">
            <thead><tr><th style={{ width: 60 }}>Step</th><th style={{ width: 110 }}>After</th><th style={{ width: 90 }}>Time</th><th>Result</th></tr></thead>
            <tbody>
              {t.events.filter((e) => e.phase === "start").map((e, i) => (
                <tr key={"s" + i}><td>—</td><td>start</td><td>{fmt(e.at)}</td><td><span className={e.ok ? "" : "danger"} style={{ color: e.ok ? "var(--green)" : "var(--red)" }}>{e.ok ? "✓" : "✗"}</span> {e.detail}</td></tr>
              ))}
              {t.steps.map((m, i) => {
                const evs = eventsFor(i + 1);
                const upcoming = running && i === t.index;
                return (
                  <tr key={i} style={{ opacity: !evs.length && !upcoming ? 0.5 : 1 }}>
                    <td>{i + 1}</td>
                    <td>{m} min locked</td>
                    <td>{evs.length ? fmt(evs[0].at) : upcoming && t.next_at ? fmt(t.next_at) : "—"}</td>
                    <td>
                      {evs.length === 0 && (upcoming ? <span className="dim">waiting…</span> : <span className="dim">not yet</span>)}
                      {evs.map((e, j) => <div key={j}><span style={{ color: e.ok ? "var(--green)" : "var(--red)" }}>{e.ok ? "✓" : "✗"}</span> <span className="dim small">{e.phase}:</span> {e.detail}</div>)}
                    </td>
                  </tr>
                );
              })}
              {t.events.filter((e) => e.phase === "done" || e.phase === "cancel").map((e, i) => (
                <tr key={"d" + i}><td>—</td><td>{e.phase === "done" ? "end" : "stopped"}</td><td>{fmt(e.at)}</td><td>{e.detail}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        {running && <div className="row" style={{ marginTop: 10 }}><button className="btn sm ghost danger" onClick={cancel}>Stop test</button><span className="small dim">The phone stays locked. Scheduled posts still run at their time.</span></div>}
      </div>
    </div>
  );
}

function AccountsTab({ device, onChanged }: { device: Device; onChanged: () => void }) {
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<{ platform: Platform; handle: string; story_link: string }>({ platform: "instagram", handle: "", story_link: "" });
  const [edit, setEdit] = useState<Record<number, Partial<Account>>>({});

  const add = async () => {
    if (!draft.handle.trim()) return;
    try {
      await api.createAccount({ device_id: device.id, ...draft, handle: draft.handle.trim() });
      setDraft({ platform: "instagram", handle: "", story_link: "" });
      setAdding(false);
      toast("Account added");
      onChanged();
    } catch (e: any) { toast(e.message, "err"); }
  };
  const patch = async (id: number, body: Partial<Account>) => {
    try { await api.patchAccount(id, body); onChanged(); } catch (e: any) { toast(e.message, "err"); }
  };
  const remove = async (a: Account) => {
    if (!confirm(`Remove @${a.handle} and all of its planned posts?`)) return;
    await api.deleteAccount(a.id);
    toast("Removed");
    onChanged();
  };

  return (
    <div className="card">
      <table className="table">
        <thead><tr><th style={{ width: 40 }}></th><th>Handle</th><th>Status</th><th>Story link</th><th style={{ width: 80 }}></th></tr></thead>
        <tbody>
          {device.accounts.map((a) => {
            const e = edit[a.id] || {};
            const link = e.story_link ?? a.story_link;
            return (
              <tr key={a.id}>
                <td><PlatformIcon platform={a.platform} /></td>
                <td>@{a.handle}</td>
                <td>
                  <select className="select" style={{ width: 110 }} value={a.status} onChange={(ev) => patch(a.id, { status: ev.target.value as Account["status"] })}>
                    <option value="active">active</option>
                    <option value="paused">paused</option>
                    <option value="banned">banned</option>
                  </select>
                </td>
                <td>
                  {a.platform !== "x" ? (
                    <div className="row">
                      <input className="input" value={link} placeholder="https://…" onChange={(ev) => setEdit({ ...edit, [a.id]: { story_link: ev.target.value } })} />
                      {link !== a.story_link && <button className="btn sm" onClick={() => { patch(a.id, { story_link: link }); setEdit({ ...edit, [a.id]: {} }); }}>Save</button>}
                    </div>
                  ) : <span className="dim">—</span>}
                </td>
                <td><button className="btn sm ghost danger" onClick={() => remove(a)}>Remove</button></td>
              </tr>
            );
          })}
          {adding && (
            <tr>
              <td>
                <select className="select" value={draft.platform} onChange={(e) => setDraft({ ...draft, platform: e.target.value as Platform })}>
                  <option value="instagram">IG</option><option value="tiktok">TT</option><option value="x">X</option>
                </select>
              </td>
              <td><input className="input" autoFocus placeholder="handle" value={draft.handle} onChange={(e) => setDraft({ ...draft, handle: e.target.value })} onKeyDown={(e) => e.key === "Enter" && add()} /></td>
              <td><span className="dim">active</span></td>
              <td>{draft.platform !== "x" && <input className="input" placeholder="https://…" value={draft.story_link} onChange={(e) => setDraft({ ...draft, story_link: e.target.value })} />}</td>
              <td><div className="row"><button className="btn sm primary" onClick={add}>Add</button><button className="btn sm ghost" onClick={() => setAdding(false)}>✕</button></div></td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="card-body">
        {!adding && <button className="btn sm" onClick={() => setAdding(true)}>+ Add account</button>}
        <span className="small dim" style={{ marginLeft: 12 }}>Accounts post in this order each morning. A banned or paused account is skipped in the planner.</span>
      </div>
    </div>
  );
}

function RollTab({ device, onChanged }: { device: Device; onChanged: () => void }) {
  const [items, setItems] = useState<Media[] | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [preview, setPreview] = useState<Media | null>(null);
  const toast = useToast();
  const load = () => api.media(device.id).then(setItems).catch((e) => toast(e.message, "err"));
  useEffect(() => { load(); }, [device.id]);
  const sync = () => {
    setSyncing(true);
    api.syncDevice(device.id).then((m) => { setItems(m); onChanged(); toast(`Synced ${m.length} items`); }).catch((e) => toast(e.message, "err")).finally(() => setSyncing(false));
  };
  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="small dim grow">
          {device.kind === "sim" ? <>Simulated roll: drop files into <span className="mono">sim_media/{device.id}/</span> then Sync.</> : "Read directly from the phone over USB. AirDrop content, then Sync."}
        </span>
        <button className="btn sm" onClick={sync} disabled={syncing}>{syncing ? "Syncing…" : "Sync from phone"}</button>
      </div>
      {items ? <RollGrid items={items} selectedId={null} onSelect={(m) => setPreview(m)} storyAlbum={device.story_album} /> : <div className="empty">Loading…</div>}
      {preview && <MediaPreview media={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function ScheduleTab({ device }: { device: Device }) {
  const today = ymd(new Date());
  const { data: jobs, reload } = usePoll(() => api.jobs({ device_id: device.id, start: today }), 3000, [device.id]);
  const [detail, setDetail] = useState<Job | null>(null);
  if (!jobs) return <div className="empty">Loading…</div>;
  if (!jobs.length) return <div className="empty">Nothing planned from today on. Use the week planner.</div>;
  return (
    <div className="card">
      <table className="table">
        <thead><tr><th>When</th><th>Account</th><th>Action</th><th>Media</th><th>Status</th></tr></thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id} className="clickable" onClick={() => setDetail(j)}>
              <td className="small">{j.run_date}{j.run_at ? ` · ${fmtDateTime(j.run_at)}` : " · routine"}</td>
              <td><span className="row"><PlatformIcon platform={j.platform} /> @{j.handle}</span></td>
              <td>{actionMeta(j.action).label}</td>
              <td className="small dim">{j.media?.filename || "—"}</td>
              <td><Pill kind={j.status}>{j.status}</Pill></td>
            </tr>
          ))}
        </tbody>
      </table>
      {detail && <JobDetail job={detail} onClose={() => setDetail(null)} onChanged={reload} />}
    </div>
  );
}
