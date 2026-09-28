import { useState } from "react";
import type { Device, Job, Stage } from "../lib/api";
import { STAGES, actionMeta, api, ymd } from "../lib/api";
import { Pill, PlatformIcon, useConfirm, usePoll, useToast } from "./common";
import JobDetail from "./JobDetail";

const APP: Record<string, string> = { instagram: "Instagram", tiktok: "TikTok", x: "X" };
const STAGE_LABEL: Record<Stage, string> = { open: "Open app & account", pick: "Pick media", compose: "Caption / link", post: "Post" };

type Mark = "done" | "failed" | "running" | "todo";

function stageMark(job: Job, stage: Stage): Mark {
  if (job.status === "done") return "done";
  const li = job.last_stage ? STAGES.indexOf(job.last_stage) : -1;
  const si = STAGES.indexOf(stage);
  if (si <= li) return "done";
  if (job.failed_stage === stage) return "failed";
  if (job.status === "running" && si === li + 1) return "running";
  return "todo";
}

const Icon = ({ m }: { m: Mark }) => (
  <span style={{ display: "inline-block", width: 18, color: m === "done" ? "var(--green)" : m === "failed" ? "var(--red)" : m === "running" ? "var(--amber)" : "var(--text-3)" }}>
    {m === "done" ? "✓" : m === "failed" ? "✗" : m === "running" ? "▶" : "·"}
  </span>
);

/** The day's queue on one phone as a checklist: wake, queue album, every job split into its
 *  stages, sleep. Each line can be run on its own or used as the point to continue from. */
export default function DayChecklist({ device }: { device: Device }) {
  const toast = useToast();
  const ask = useConfirm();
  const [day, setDay] = useState(ymd(new Date()));
  const [detail, setDetail] = useState<Job | null>(null);
  const [busyLine, setBusyLine] = useState("");
  const { data: jobs, reload } = usePoll(() => api.jobs({ device_id: device.id, start: day, end: day }), 3000, [device.id, day]);
  const rows = (jobs || []).filter((j) => j.status !== "cancelled").sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);

  const sys = async (line: string, fn: () => Promise<{ locked?: boolean; message?: string }>, okMsg: (r: any) => string) => {
    setBusyLine(line);
    try { const r = await fn(); toast(okMsg(r)); } catch (e: any) { toast(e.message, "err"); } finally { setBusyLine(""); }
  };
  const runStage = async (job: Job, stage: Stage, mode: "only" | "from") => {
    const what = mode === "only" ? `only "${STAGE_LABEL[stage]}"` : `from "${STAGE_LABEL[stage]}" to the end`;
    const warn = stage === "post" || mode === "from" ? " This can post for real (unless Dry run is on)." : "";
    if (!(await ask(`${actionMeta(job.action).label} @${job.handle}: run ${what}?${warn}`, "Run"))) return;
    try { await api.runStage(job.id, stage, mode); toast("Running"); reload(); } catch (e: any) { toast(e.message, "err"); }
  };
  const StageBtns = ({ job, stage }: { job: Job; stage: Stage }) => (
    <span className="row" style={{ gap: 4, marginLeft: "auto" }}>
      <button className="btn sm ghost" disabled={job.status === "running" || device.busy} title="Run only this stage; the phone must already be where the previous stage left it" onClick={() => runStage(job, stage, "only")}>Run</button>
      <button className="btn sm ghost" disabled={job.status === "running" || device.busy} title="Run this stage and everything after it" onClick={() => runStage(job, stage, "from")}>Continue from here ▸</button>
    </span>
  );

  let prevApp = "", prevHandle = "";
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="card-head">
        <strong>Day checklist</strong>
        <input className="input" type="date" style={{ width: 150 }} value={day} onChange={(e) => setDay(e.target.value)} />
        <span className="small dim grow">Each line is a checkpoint. {device.paused ? "Phone is paused: lines only run when you press them." : "Phone is live: after a line finishes, the queue keeps going on its own."}</span>
        {device.busy && <Pill kind="running">running</Pill>}
        {device.busy && <button className="btn sm stop" onClick={async () => { try { await api.stopDevice(device.id); toast("Stopping after the current step. Phone paused."); reload(); } catch (e: any) { toast(e.message, "err"); } }}>■ Stop</button>}
      </div>
      <div className="card-body checklist">
        <div className="ck-line">
          <Icon m="todo" /><span>Wake &amp; unlock</span>
          <button className="btn sm ghost" style={{ marginLeft: "auto" }} disabled={busyLine !== ""} onClick={() => sys("wake", () => api.wakeDevice(device.id), (r) => (r.locked ? "Still locked" : "Awake and unlocked"))}>{busyLine === "wake" ? "…" : "Run"}</button>
        </div>
        <div className="ck-line">
          <Icon m="todo" /><span>Clips for the day are in the "{device.queue_album}" album on the phone</span>
          <span className="small dim" style={{ marginLeft: "auto" }}>you add and remove them in Photos</span>
        </div>
        {rows.length === 0 && <div className="ck-line dim small">Nothing planned on this day.</div>}
        {rows.map((job) => {
          const meta = actionMeta(job.action);
          const app = APP[job.platform] || job.platform;
          const switchLine = app !== prevApp || job.handle !== prevHandle;
          const verb = app === prevApp ? "Switch" : "Open";
          prevApp = app; prevHandle = job.handle;
          return (
            <div key={job.id} className="ck-job">
              {switchLine && (
                <div className="ck-line">
                  <Icon m={stageMark(job, "open")} />
                  <PlatformIcon platform={job.platform} />
                  <span>{verb} {app} as @{job.handle}</span>
                  <StageBtns job={job} stage="open" />
                </div>
              )}
              <div className="ck-line" style={{ cursor: "pointer" }} onClick={() => setDetail(job)}>
                <Icon m={job.status === "done" ? "done" : job.status === "failed" ? "failed" : job.status === "running" ? "running" : "todo"} />
                <strong>{meta.label}</strong><span className="dim">@{job.handle}</span>
                {job.media && <span className="dim small">· {job.media.filename.split("/").pop()}</span>}
                <Pill kind={job.status}>{job.status}</Pill>
                {job.last_error && <span className="small" style={{ color: "var(--red)" }}>{job.last_error.slice(0, 80)}</span>}
                <span className="small dim" style={{ marginLeft: "auto" }}>details ›</span>
              </div>
              {(["pick", "compose", "post"] as Stage[]).map((st) => (
                <div key={st} className="ck-line ck-sub">
                  <Icon m={stageMark(job, st)} /><span>{STAGE_LABEL[st]}</span>
                  <StageBtns job={job} stage={st} />
                </div>
              ))}
            </div>
          );
        })}
        <div className="ck-line">
          <Icon m="todo" /><span>Sleep (lock the screen)</span>
          <button className="btn sm ghost" style={{ marginLeft: "auto" }} disabled={busyLine !== ""} onClick={() => sys("sleep", () => api.sleepDevice(device.id), (r) => (r.locked ? "Asleep" : `Could not sleep: ${r.error || ""}`))}>{busyLine === "sleep" ? "…" : "Run"}</button>
        </div>
      </div>
      {detail && <JobDetail job={detail} onClose={() => setDetail(null)} onChanged={reload} />}
    </div>
  );
}
