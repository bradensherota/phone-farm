import { useMemo, useState } from "react";
import type { Account, Device, Job } from "../lib/api";
import { actionMeta, addDays, api, fmtHHMM, scheduleApi, startOfWeek, thumbUrl, TIME_SLOTS, ymd } from "../lib/api";
import type { DayStart } from "../lib/api";
import { Pill, PlatformIcon, StatusDot, useConfirm, usePoll, useToast } from "../components/common";
import PlanDrawer from "../components/PlanDrawer";

export default function PlannerPage() {
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(weekStart, i)), [weekStart]);
  const start = ymd(days[0]);
  const end = ymd(days[6]);
  const today = ymd(new Date());

  const { data: devices } = usePoll(api.devices, 5000);
  const { data: settings } = usePoll(api.settings, 10000);
  const autoStart = settings?.auto_start === "true";
  const { data: jobs, reload } = usePoll(() => api.jobs({ start, end }), 4000, [start, end]);
  const { data: starts, reload: reloadStarts } = usePoll(() => scheduleApi.get(start, end), 10000, [start, end]);
  const startFor = (deviceId: string, day: string) => (starts || []).find((x: DayStart) => x.device_id === deviceId && x.run_date === day)?.start || "";
  const setStart = async (deviceId: string, day: string, value: string) => { await scheduleApi.put(deviceId, day, value); reloadStarts(); };
  const [sel, setSel] = useState<{ account: Account; device: Device; date: string } | null>(null);
  const toast = useToast();
  const ask = useConfirm();
  const runDay = async (device: Device, day: string, count: number) => {
    if (!(await ask(`Start ${device.name}'s whole queue for ${day} now?\n${count} action(s) will run in order, real posts. The phone is woken first.`, "Run the day"))) return;
    try { const r = await api.runDay(device.id, day); toast(`Started ${r.started} action(s) on ${device.name}`); reload(); }
    catch (e: any) { toast(e.message, "err"); }
  };

  const byCell = useMemo(() => {
    const m = new Map<string, Job[]>();
    for (const j of jobs || []) {
      const k = `${j.account_id}:${j.run_date}`;
      m.set(k, [...(m.get(k) || []), j]);
    }
    for (const arr of m.values()) arr.sort((a, b) => actionMeta(a.action).rank - actionMeta(b.action).rank);
    return m;
  }, [jobs]);

  const weekLabel = `${days[0].toLocaleDateString([], { month: "short", day: "numeric" })} – ${days[6].toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}`;
  const planned = (jobs || []).length;
  const done = (jobs || []).filter((j) => j.status === "done").length;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Week planner</h1>
          <div className="sub">Click any cell to plan that account's day. {planned ? `${done}/${planned} actions done this week.` : ""}</div>
        </div>
        <div className="week-nav">
          <button className="btn" onClick={() => setWeekStart(addDays(weekStart, -7))}>‹</button>
          <span className="label">{weekLabel}</span>
          <button className="btn" onClick={() => setWeekStart(addDays(weekStart, 7))}>›</button>
          <button className="btn ghost" onClick={() => setWeekStart(startOfWeek(new Date()))}>Today</button>
        </div>
      </div>

      <div className="planner">
        <table>
          <thead>
            <tr>
              <th>Account</th>
              {days.map((d) => {
                const k = ymd(d);
                return <th key={k} className={k === today ? "today" : ""}>{d.toLocaleDateString([], { weekday: "short" })} <span className="dim">{d.getDate()}</span></th>;
              })}
            </tr>
          </thead>
          <tbody>
            {(devices || []).map((dev) => (
              <DeviceRows key={dev.id} device={dev} days={days} today={today} byCell={byCell} onPick={(account, date) => setSel({ account, device: dev, date })} startFor={startFor} setStart={setStart} runDay={runDay} autoStart={autoStart} />
            ))}
          </tbody>
        </table>
        {devices && devices.length === 0 && <div className="empty">No phones yet.</div>}
      </div>

      {sel && (
        <PlanDrawer
          account={sel.account}
          device={sel.device}
          date={sel.date}
          jobs={byCell.get(`${sel.account.id}:${sel.date}`) || []}
          onClose={() => setSel(null)}
          onSaved={reload}
        />
      )}
    </div>
  );
}

function DeviceRows({ device, days, today, byCell, onPick, startFor, setStart, runDay, autoStart }: {
  device: Device; days: Date[]; today: string; byCell: Map<string, Job[]>;
  onPick: (a: Account, date: string) => void;
  startFor: (deviceId: string, day: string) => string;
  setStart: (deviceId: string, day: string, value: string) => Promise<void>;
  runDay: (device: Device, day: string, count: number) => Promise<void>;
  autoStart: boolean;
}) {
  const accounts = device.accounts.filter((a) => a.status !== "banned");
  return (
    <>
      <tr className="dev-row">
        <td>
          <span className="row"><StatusDot connected={device.connected} busy={device.busy} /> {device.name}
            <span className="meta">{device.paused ? "paused" : autoStart ? `default ${fmtHHMM(device.routine_start)}` : "manual start"}</span>
          </span>
        </td>
        {days.map((d) => {
          const k = ymd(d);
          const v = startFor(device.id, k);
          const pending = accounts.reduce((n, a) => n + (byCell.get(`${a.id}:${k}`) || []).filter((j) => j.status !== "done" && j.status !== "running").length, 0);
          return (
            <td key={k} className="start-cell">
              {autoStart && (
                <select className={`start-select ${v ? "set" : ""}`} value={v} title="When this phone's queue starts on this day. Jobs run down the column, one account after another." onChange={(e) => setStart(device.id, k, e.target.value)}>
                  <option value="">{fmtHHMM(device.routine_start)} (default)</option>
                  {TIME_SLOTS.map(([hhmm, label]) => <option key={hhmm} value={hhmm}>{label}</option>)}
                </select>
              )}
              {pending > 0 && device.kind !== "sim" && (
                <button className="btn sm run-day" title="Run this day's whole queue on this phone now, in order" onClick={() => runDay(device, k, pending)}>▶ Run day ({pending})</button>
              )}
            </td>
          );
        })}
      </tr>
      {accounts.map((a) => (
        <tr key={a.id}>
          <td>
            <div className="acct-cell">
              <PlatformIcon platform={a.platform} />
              <span className="h">@{a.handle}</span>
              {a.status === "paused" && <Pill kind="paused">paused</Pill>}
            </div>
          </td>
          {days.map((d) => {
            const k = ymd(d);
            const cellJobs = byCell.get(`${a.id}:${k}`) || [];
            return (
              <td key={k}>
                <div className={`cell ${k === today ? "today" : k < today ? "past" : ""}`} onClick={() => onPick(a, k)}>
                  {cellJobs.map((j) => (
                    <div key={j.id} className={`chip ${j.status} ${j.media ? "" : "missing"}`} title={`${actionMeta(j.action).label} · ${j.status}${j.last_error ? ` · ${j.last_error}` : ""}`}>
                      {j.media && thumbUrl(j.media) ? <img className="th" src={thumbUrl(j.media)} alt="" /> : <span className="th" />}
                      <span className="t">{actionMeta(j.action).label}{j.group_origin_account_id !== null ? (j.group_origin_account_id === a.id ? " · all accounts" : " · ↳") : ""}{j.link_text ? ` · ${j.link_text}` : j.caption ? ` · ${j.caption.slice(0, 30)}` : ""}</span>
                    </div>
                  ))}
                  {cellJobs.length === 0 && <span className="add">+</span>}
                </div>
              </td>
            );
          })}
        </tr>
      ))}
      {accounts.length === 0 && <tr><td colSpan={8} className="dim small" style={{ padding: 10 }}>No active accounts on this phone.</td></tr>}
    </>
  );
}
