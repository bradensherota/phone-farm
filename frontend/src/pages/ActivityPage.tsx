import { useState } from "react";
import type { Job } from "../lib/api";
import { actionMeta, api, fmtDateTime } from "../lib/api";
import { Pill, PlatformIcon, usePoll } from "../components/common";
import JobDetail from "../components/JobDetail";

export default function ActivityPage() {
  const { data: jobs, reload } = usePoll(() => api.activity(200), 3000);
  const [detail, setDetail] = useState<Job | null>(null);
  const [filter, setFilter] = useState<"all" | "failed" | "running">("all");
  const shown = (jobs || []).filter((j) => filter === "all" ? true : j.status === filter);
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Activity</h1>
          <div className="sub">Everything the phones have done, newest first.</div>
        </div>
        <div className="row">
          {(["all", "running", "failed"] as const).map((f) => (
            <button key={f} className={`btn sm ${filter === f ? "" : "ghost"}`} onClick={() => setFilter(f)}>{f}</button>
          ))}
        </div>
      </div>
      <div className="card">
        {!jobs ? <div className="empty">Loading…</div> : shown.length === 0 ? <div className="empty">Nothing here yet.</div> : (
          <table className="table">
            <thead><tr><th>Finished</th><th>Phone</th><th>Account</th><th>Action</th><th>Media</th><th>Status</th><th>Note</th></tr></thead>
            <tbody>
              {shown.map((j) => (
                <tr key={j.id} className="clickable" onClick={() => setDetail(j)}>
                  <td className="small">{fmtDateTime(j.finished_at || j.started_at)}</td>
                  <td className="small">{j.device_name}</td>
                  <td><span className="row"><PlatformIcon platform={j.platform} /> @{j.handle}</span></td>
                  <td>{actionMeta(j.action).label}</td>
                  <td className="small dim">{j.media?.filename || "—"}</td>
                  <td><Pill kind={j.status}>{j.status}</Pill></td>
                  <td className="small" style={{ color: j.last_error ? "var(--red)" : undefined }}>{j.last_error || (j.attempts > 1 ? `${j.attempts} attempts` : "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {detail && <JobDetail job={detail} onClose={() => setDetail(null)} onChanged={reload} />}
    </div>
  );
}
