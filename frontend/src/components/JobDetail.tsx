import { useEffect, useState } from "react";
import type { Job, JobLog, Stage } from "../lib/api";
import { STAGES, actionMeta, api, fmtDateTime, shotUrl } from "../lib/api";
import { MediaThumb, Pill, PlatformIcon, useInterval, useToast } from "./common";

interface Props {
  job: Job;
  onClose: () => void;
  onChanged: () => void;
}

export default function JobDetail({ job: initial, onClose, onChanged }: Props) {
  const [job, setJob] = useState(initial);
  const [logs, setLogs] = useState<JobLog[]>([]);
  const [shot, setShot] = useState(job.has_screenshot ? shotUrl(job.id) : "");
  const toast = useToast();

  const refresh = () => {
    api.jobLogs(job.id).then(setLogs).catch(() => {});
    api.jobs({ device_id: job.device_id, start: job.run_date, end: job.run_date }).then((rows) => {
      const j = rows.find((r) => r.id === job.id);
      if (j) { setJob(j); if (j.has_screenshot) setShot(shotUrl(j.id)); }
    }).catch(() => {});
  };
  useEffect(refresh, [job.id]);
  useInterval(refresh, job.status === "running" || job.status === "scheduled" ? 1500 : null);

  const act = async (fn: () => Promise<unknown>, msg: string) => {
    try { await fn(); toast(msg); onChanged(); refresh(); }
    catch (e: any) { toast(e.message, "err"); }
  };
  const meta = actionMeta(job.action);

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" style={{ width: "min(900px, 92vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <PlatformIcon platform={job.platform} />
          <h2>{meta.label} · @{job.handle} <span className="dim">· {job.device_name}</span></h2>
          <Pill kind={job.status}>{job.status}</Pill>
          <button className="btn sm ghost" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body" style={{ display: "grid", gridTemplateColumns: "1fr 260px", gap: 20 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div className="kv small">
              <span className="dim">Planned for</span><span>{job.run_date}{job.run_at ? ` at ${fmtDateTime(job.run_at)}` : " (morning routine)"}</span>
              {job.next_run && <><span className="dim">Next run</span><span>{fmtDateTime(job.next_run)}</span></>}
              {job.started_at && <><span className="dim">Started</span><span>{fmtDateTime(job.started_at)}</span></>}
              {job.finished_at && <><span className="dim">Finished</span><span>{fmtDateTime(job.finished_at)}</span></>}
              <span className="dim">Attempts</span><span>{job.attempts}</span>
              {job.media && <><span className="dim">Media</span><span className="row"><MediaThumb media={job.media} className="kv-thumb" /> {job.media.filename}</span></>}
              {meta.link && <><span className="dim">Link</span><span>{job.link_text ? `"${job.link_text}" → ` : ""}{job.link || <span className="dim">none</span>}</span></>}
              {job.caption && <><span className="dim">Caption</span><span style={{ whiteSpace: "pre-wrap", userSelect: "text" }}>{job.caption}</span></>}
              <span className="dim">Stages</span>
              <span className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                {STAGES.map((st) => {
                  const li = job.last_stage ? STAGES.indexOf(job.last_stage) : -1;
                  const done = job.status === "done" || STAGES.indexOf(st) <= li;
                  const failed = job.failed_stage === st;
                  return (
                    <span key={st} className="row" style={{ gap: 4 }}>
                      <Pill kind={done ? "done" : failed ? "failed" : undefined}>{done ? "✓ " : failed ? "✗ " : ""}{st}</Pill>
                      {job.status !== "running" && <button className="btn sm ghost" title="Run only this stage" onClick={() => act(() => api.runStage(job.id, st as Stage, "only"), `Running stage ${st}`)}>run</button>}
                      {job.status !== "running" && <button className="btn sm ghost" title="Run this stage and everything after it" onClick={() => act(() => api.runStage(job.id, st as Stage, "from"), `Continuing from ${st}`)}>from here ▸</button>}
                    </span>
                  );
                })}
              </span>
              {job.last_error && <><span className="dim">Error</span><span style={{ color: "var(--red)" }}>{job.last_error}</span></>}
            </div>
            <div>
              <div className="dim small" style={{ marginBottom: 6 }}>Log</div>
              <div className="log">
                {logs.length === 0 && <span className="dim">No activity yet.</span>}
                {logs.map((l) => (
                  <div key={l.id} className={`l ${l.level}`}>
                    <span className="ts">{new Date(l.ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}</span>
                    <span>{l.message}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div>
            <div className="dim small" style={{ marginBottom: 6 }}>Screen at finish</div>
            {shot ? <img className="shot" src={shot} alt="screenshot" /> : <div className="empty small">No screenshot yet</div>}
          </div>
        </div>
        <div className="modal-foot">
          {job.status !== "running" && (
            <button className="btn" title="Start over from the first stage" onClick={() => act(() => api.runNow(job.id), "Running now")}>Run now (from the start)</button>
          )}
          {(job.status === "scheduled") && (
            <button className="btn" onClick={() => act(() => api.cancelJob(job.id), "Cancelled")}>Cancel</button>
          )}
          {job.status !== "running" && (
            <button className="btn danger" onClick={() => act(async () => { await api.deleteJob(job.id); onClose(); }, "Deleted")}>Delete</button>
          )}
        </div>
      </div>
    </div>
  );
}
