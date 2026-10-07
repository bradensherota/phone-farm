import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { PhoneScreen, Pill, PlatformIcon, StatusDot, usePoll, useToast } from "../components/common";

export default function DevicesPage() {
  const { data: devices, reload } = usePoll(api.devices, 3000);
  const { data: settings, reload: reloadSettings } = usePoll(api.settings, 5000);
  const nav = useNavigate();
  const toast = useToast();
  const enabled = settings?.runner_enabled === "true";

  const toggleRunner = async () => {
    await api.putSettings({ runner_enabled: enabled ? "false" : "true" });
    toast(enabled ? "Runner paused. Nothing will post." : "Runner resumed.");
    reloadSettings();
  };

  const startToday = async () => {
    try {
      const r = await api.startToday();
      const started = r.filter((x) => x.started > 0);
      const skipped = r.filter((x) => x.started === 0 && x.note && !x.note.startsWith("nothing"));
      const msg = [
        started.length ? `Started ${started.map((x) => `${x.name} (${x.started})`).join(", ")}` : "Nothing to start",
        skipped.length ? `Skipped ${skipped.map((x) => `${x.name}: ${x.note}`).join("; ")}` : "",
      ].filter(Boolean).join(". ");
      toast(msg, started.length ? "ok" : "err");
      reload();
    } catch (e: any) { toast(e.message, "err"); }
  };

  const totals = (devices || []).reduce(
    (a, d) => ({ total: a.total + d.today.total, done: a.done + d.today.done, failed: a.failed + d.today.failed }),
    { total: 0, done: 0, failed: 0 },
  );

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Phones</h1>
          <div className="sub">{new Date().toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })}</div>
        </div>
        <div className="stats">
          <div className="stat"><span className="dim small">Today</span><b>{totals.done}/{totals.total}</b></div>
          <div className="stat"><span className="dim small">Failed</span><b style={{ color: totals.failed ? "var(--red)" : undefined }}>{totals.failed}</b></div>
          {settings && (settings.dry_run === "true" ? <Pill kind="running" title="Flows stop before the final Post / Share">DRY RUN</Pill> : <Pill kind="failed" title="Flows post for real">LIVE</Pill>)}
          <button className="btn primary start-today" title="Run every connected phone's queue for today, in order. Plug the phones in and unlock them first." onClick={startToday}>▶ Start today</button>
          <button className="btn" onClick={async () => { await api.discoverDevices(); toast("Looking for phones over USB…"); reload(); }}>Find phones</button>
          <button className={`btn ${enabled ? "" : "primary"}`} onClick={toggleRunner}>{enabled ? "Pause runner" : "Resume runner"}</button>
        </div>
      </div>

      {!devices ? <div className="empty">Loading…</div> : (
        <div className="phones">
          {devices.map((d) => {
            const t = d.today;
            const pct = (n: number) => (t.total ? (n / t.total) * 100 : 0);
            return (
              <div className="card phone-card" key={d.id} onClick={() => nav(`/devices/${d.id}`)}>
                <PhoneScreen deviceId={d.id} connected={d.connected} intervalMs={2500} />
                <div>
                  <div className="row">
                    <StatusDot connected={d.connected} busy={d.busy} parked={d.parked} />
                    <h3>{d.name}</h3>
                    {d.paused && <Pill kind="paused">paused</Pill>}
                    {d.kind === "ios" && (d.connected || d.connecting ? (
                      <button className="btn sm ghost" disabled={d.busy || d.connecting} title="Stop the runner on the phone (clears the Automation Running overlay). Connect, Start today or Run day bring it back." onClick={async (e) => { e.stopPropagation(); try { await api.disconnectDevice(d.id); toast(`${d.name}: runner stopped`); reload(); } catch (err: any) { toast(err.message, "err"); } }}>{d.connecting ? "Connecting…" : "Disconnect"}</button>
                    ) : (
                      <button className="btn sm" title="Start the runner on the phone (plug it in and unlock it first)" onClick={async (e) => { e.stopPropagation(); try { await api.connectDevice(d.id); toast(`${d.name}: starting the runner… up to a minute`); reload(); } catch (err: any) { toast(err.message, "err"); } }}>Connect</button>
                    ))}
                  </div>
                  <div className="small dim">{d.model} · {d.kind === "sim" ? "simulated" : "USB"}{settings?.auto_start === "true" ? ` · starts ${d.routine_start}` : " · manual start"}</div>
                  {d.connecting && <div className="small" style={{ color: "var(--amber)" }}>Starting runner…</div>}
                  {d.parked && !d.connected && !d.connecting && <div className="small dim">Disconnected by you · press Connect, or Start today / Run day</div>}
                  {d.error && !d.connecting && <div className="small" style={{ color: "var(--red)" }}>{d.error}</div>}
                  <div className="small dim">{d.media_count} in {d.queue_album || "Farm Queue"}{d.story_album ? ` / ${d.story_album}` : ""}</div>
                  <div className="acct-list">
                    {d.accounts.map((a) => (
                      <div className="acct" key={a.id}>
                        <PlatformIcon platform={a.platform} />
                        <span className="h">@{a.handle}</span>
                        {a.status !== "active" && <Pill kind={a.status}>{a.status}</Pill>}
                      </div>
                    ))}
                    {d.accounts.length === 0 && <span className="dim small">No accounts yet</span>}
                  </div>
                  <div className="progress" title={`${t.done} done, ${t.failed} failed, ${t.scheduled} to go`}>
                    <span className="d" style={{ width: pct(t.done) + "%" }} />
                    <span className="r" style={{ width: pct(t.running) + "%" }} />
                    <span className="f" style={{ width: pct(t.failed) + "%" }} />
                  </div>
                  <div className="small dim" style={{ marginTop: 4 }}>
                    {t.total === 0 ? "Nothing planned today" : d.busy ? `Posting now · ${t.done}/${t.total}` : `${t.done}/${t.total} done today${t.failed ? `, ${t.failed} failed` : ""}`}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <div style={{ height: 0 }} onClick={reload} />
    </div>
  );
}
