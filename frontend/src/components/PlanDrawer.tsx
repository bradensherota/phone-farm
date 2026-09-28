import { useEffect, useMemo, useState } from "react";
import type { Account, ActionKey, ActionMeta, Device, Job, JobIn, Media } from "../lib/api";
import { actionsFor, api, fmtDateTime, parseYmd, toLocalInput } from "../lib/api";
import { MediaThumb, Pill, PlatformIcon, useToast } from "./common";
import MediaPicker from "./MediaPicker";
import JobDetail from "./JobDetail";

interface Props {
  account: Account;
  device: Device;
  date: string; // YYYY-MM-DD
  jobs: Job[]; // existing jobs for this account + date
  onClose: () => void;
  onSaved: () => void;
}

interface Draft {
  enabled: boolean;
  jobId?: number;
  status?: Job["status"];
  media: Media | null;
  caption: string;
  link: string;
  link_text: string;
  run_at: string; // local datetime-local value or ""
  job?: Job;
}

function draftFrom(meta: ActionMeta, account: Account, job?: Job): Draft {
  if (job) {
    return {
      enabled: true, jobId: job.id, status: job.status, media: job.media, caption: job.caption,
      link: job.link, link_text: job.link_text, run_at: toLocalInput(job.run_at), job,
    };
  }
  return { enabled: false, media: null, caption: "", link: meta.link ? account.story_link : "", link_text: "", run_at: "" };
}

export default function PlanDrawer({ account, device, date, jobs, onClose, onSaved }: Props) {
  const actions = useMemo(() => actionsFor(account.platform), [account.platform]);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [picker, setPicker] = useState<ActionKey | null>(null);
  const [detail, setDetail] = useState<Job | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  // Only rebuild drafts when the underlying jobs actually change (the planner polls, so
  // the `jobs` array identity changes every few seconds even when nothing did).
  const jobsKey = jobs.map((j) => `${j.id}:${j.status}:${j.attempts}`).join(",");
  useEffect(() => {
    const d: Record<string, Draft> = {};
    for (const a of actions) d[a.action] = draftFrom(a, account, jobs.find((j) => j.action === a.action));
    setDrafts(d);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.id, date, jobsKey, actions]);

  const upd = (k: ActionKey, patch: Partial<Draft>) => setDrafts((d) => ({ ...d, [k]: { ...d[k], ...patch } }));

  const save = async () => {
    const upserts: JobIn[] = [];
    const deletes: number[] = [];
    for (const a of actions) {
      const d = drafts[a.action];
      if (!d) continue;
      const locked = d.status === "done" || d.status === "running";
      if (locked) continue;
      if (d.enabled) {
        if (!d.media) { toast(`${a.label} needs media`, "err"); return; }
        if (a.link && !d.link) { toast(`${a.label} needs a link`, "err"); return; }
        upserts.push({
          id: d.jobId, account_id: account.id, action: a.action, media_id: d.media.id,
          caption: d.caption, link: a.link ? d.link : "", link_text: a.link ? d.link_text : "",
          run_date: date, run_at: d.run_at ? new Date(d.run_at).toISOString() : null,
        });
      } else if (d.jobId) {
        deletes.push(d.jobId);
      }
    }
    setSaving(true);
    try {
      if (upserts.length) await api.upsertJobs(upserts);
      for (const id of deletes) await api.deleteJob(id);
      toast("Saved");
      onSaved();
      onClose();
    } catch (e: any) {
      toast(e.message, "err");
    } finally {
      setSaving(false);
    }
  };

  const dateLabel = parseYmd(date).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });

  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <div className="drawer">
        <div className="drawer-head">
          <PlatformIcon platform={account.platform} />
          <div className="grow">
            <h2>@{account.handle}</h2>
            <div className="small dim">{device.name} · {dateLabel}</div>
          </div>
          <button className="btn sm ghost" onClick={onClose}>✕</button>
        </div>
        <div className="drawer-body">
          {actions.map((a) => {
            const d = drafts[a.action];
            if (!d) return null;
            const locked = d.status === "done" || d.status === "running";
            return (
              <div className="section" key={a.action}>
                <div className="sec-head">
                  <label className="switch">
                    <input type="checkbox" checked={d.enabled} disabled={locked} onChange={(e) => upd(a.action, { enabled: e.target.checked })} />
                  </label>
                  <h3>{a.label} <span className="dim small">· {a.media === "video" ? "video" : "photo or video"}{a.link ? " + link" : ""}</span></h3>
                  {d.status && <Pill kind={d.status}>{d.status}</Pill>}
                  {d.job && <button className="btn sm ghost" onClick={() => setDetail(d.job!)}>Details</button>}
                </div>
                {d.enabled && (
                  <div className="sec-body">
                    <div className="media-pick">
                      <MediaThumb media={d.media} className="th" />
                      <div className="grow">
                        <div className="small">{d.media ? d.media.filename : <span className="dim">No media chosen</span>}</div>
                        {d.media && d.media.index !== null && <div className="small dim">#{d.media.index + 1} in Recents</div>}
                      </div>
                      <button className="btn sm" disabled={locked} onClick={() => setPicker(a.action)}>{d.media ? "Change" : "Choose from roll"}</button>
                    </div>
                    {!a.link && (
                      <div className="field">
                        <label>Caption</label>
                        <textarea className="textarea" disabled={locked} value={d.caption} onChange={(e) => upd(a.action, { caption: e.target.value })} placeholder="Caption, hashtags…" />
                      </div>
                    )}
                    {a.link && (
                      <>
                        <div className="field">
                          <label>Link sticker text</label>
                          <input className="input" disabled={locked} value={d.link_text} onChange={(e) => upd(a.action, { link_text: e.target.value })} placeholder="e.g. Free workout plan" />
                        </div>
                        <div className="field">
                          <label>Link <span className="dim">(defaults to the account's link)</span></label>
                          <input className="input" disabled={locked} value={d.link} onChange={(e) => upd(a.action, { link: e.target.value })} placeholder="https://…" />
                        </div>
                      </>
                    )}
                    {d.job?.last_error && <div className="small" style={{ color: "var(--red)" }}>{d.job.last_error}</div>}
                    {d.job?.finished_at && <div className="small dim">Finished {fmtDateTime(d.job.finished_at)}</div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div className="drawer-foot">
          <span className="small dim grow">Runs in the phone's queue for that day, starting at the time set above the column: {actions.map((a) => a.label).join(" → ")}, after earlier accounts.</span>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </div>
      {picker && (
        <MediaPicker
          deviceId={device.id}
          deviceName={device.name}
          kind={actions.find((a) => a.action === picker)!.media}
          selectedId={drafts[picker]?.media?.id ?? null}
          onSelect={(m) => upd(picker, { media: m })}
          onClose={() => setPicker(null)}
        />
      )}
      {detail && <JobDetail job={detail} onClose={() => setDetail(null)} onChanged={onSaved} />}
    </>
  );
}
