import { useEffect, useMemo, useRef, useState } from "react";
import type { Account, ActionKey, ActionMeta, Device, Job, JobIn, Media } from "../lib/api";
import { actionsFor, api, fmtDateTime, parseYmd, toLocalInput } from "../lib/api";
import { MediaThumb, Pill, PlatformIcon, usePoll, useToast } from "./common";
import MediaPicker, { inAlbum } from "./MediaPicker";
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
  allAccounts?: boolean;
  allPhones?: boolean; // every Instagram account on every phone
  mirroredFrom?: number | null; // set when this story was created by another account's "all accounts" story
  mirroredLabel?: string; // "@handle" or "@handle (iPhone 17 Pro Max)" for the account it was set from
  jobId?: number;
  status?: Job["status"];
  media: Media | null;
  caption: string;
  link: string;
  link_text: string;
  run_at: string; // local datetime-local value or ""
  run_date: string; // YYYY-MM-DD; change it to move the job to another day
  job?: Job;
}

function draftFrom(meta: ActionMeta, account: Account, date: string, job?: Job): Draft {
  if (job) {
    const origin = job.group_origin_account_id;
    return {
      enabled: true, jobId: job.id, status: job.status, media: job.media, caption: job.caption,
      link: job.link, link_text: job.link_text, run_at: toLocalInput(job.run_at), run_date: job.run_date || date, job,
      allAccounts: origin !== null && origin === account.id,
      allPhones: origin !== null && origin === account.id && job.group_scope === "all",
      mirroredFrom: origin !== null && origin !== account.id ? origin : null,
      mirroredLabel: job.group_origin_handle ? `@${job.group_origin_handle}${job.group_origin_device ? ` (${job.group_origin_device})` : ""}` : "",
    };
  }
  return { enabled: false, media: null, caption: "", link: meta.link ? account.story_link : "", link_text: "", run_date: date, run_at: "" };
}

export default function PlanDrawer({ account, device, date, jobs, onClose, onSaved }: Props) {
  const { data: settings } = usePoll(api.settings, 15000);
  const ttStories = settings?.tiktok_stories === "true";
  const actions = useMemo(() => actionsFor(account.platform).filter((a) => a.action !== "tt_story" || ttStories), [account.platform, ttStories]);
  // one row per planned job; reels and trial reels can have several on a day (they post one per
  // "Run day" press), a story is one a day
  const [drafts, setDrafts] = useState<Record<string, Draft[]>>({});
  const [removed, setRemoved] = useState<number[]>([]);
  const [picker, setPicker] = useState<{ action: ActionKey; idx: number } | null>(null);
  const [detail, setDetail] = useState<Job | null>(null);
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  // Rebuild the form only when the planned content itself changes (a job added, removed, or
  // its media/text edited elsewhere), never because a status ticked over while the phone is
  // posting, and never while there are unsaved edits here: that used to wipe typed text.
  const jobsKey = jobs.map((j) => `${j.id}:${j.media_id}:${j.caption}:${j.link}:${j.link_text}:${j.group_key}`).join(",");
  const dirtyRef = useRef(false);
  useEffect(() => {
    if (dirtyRef.current) return;
    const d: Record<string, Draft[]> = {};
    for (const a of actions) {
      const mine = jobs.filter((j) => j.action === a.action).sort((x, y) => x.sort_order - y.sort_order || x.id - y.id);
      d[a.action] = mine.length ? mine.map((j) => draftFrom(a, account, date, j)) : [draftFrom(a, account, date)];
    }
    setDrafts(d);
    setRemoved([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.id, date, jobsKey, actions]);

  const upd = (k: ActionKey, idx: number, patch: Partial<Draft>) => {
    dirtyRef.current = true;
    setDrafts((d) => ({ ...d, [k]: d[k].map((row, i) => (i === idx ? { ...row, ...patch } : row)) }));
  };
  const addRow = (a: ActionMeta) => {
    dirtyRef.current = true;
    setDrafts((d) => ({ ...d, [a.action]: [...d[a.action], { ...draftFrom(a, account, date), enabled: true }] }));
  };
  const removeRow = (a: ActionMeta, idx: number) => {
    dirtyRef.current = true;
    const row = drafts[a.action][idx];
    if (row?.jobId) setRemoved((r) => [...r, row.jobId!]);
    setDrafts((d) => {
      const rest = d[a.action].filter((_, i) => i !== idx);
      return { ...d, [a.action]: rest.length ? rest : [draftFrom(a, account, date)] };
    });
  };

  const save = async () => {
    const upserts: JobIn[] = [];
    const deletes: number[] = [...removed];
    const moved: string[] = [];
    let problem = "";
    for (const a of actions) {
      const rows = drafts[a.action] || [];
      rows.forEach((d, i) => {
        const label = rows.length > 1 ? `${a.label} ${i + 1}` : a.label;
        const editableAfterPost = d.status === "done" && !!d.allAccounts; // copies on other accounts may still be pending
        const locked = (d.status === "done" && !editableAfterPost) || d.status === "running";
        if (locked) return;
        if (d.mirroredFrom) return; // edited from the origin account
        if (d.enabled) {
          if (!d.media && !(d.jobId && d.job?.origin_media_id)) { problem = problem || `${label} needs media`; return; }
          if (a.link && !d.link) { problem = problem || `${label} needs a link`; return; }
          // a changed Day moves the job (and its all-accounts copies) there; a set time keeps its clock time
          const runDate = d.run_date || date;
          const runAt = d.run_at ? (runDate !== date ? runDate + d.run_at.slice(10) : d.run_at) : "";
          if (runDate !== date) moved.push(`${label} → ${parseYmd(runDate).toLocaleDateString([], { weekday: "short", day: "numeric" })}`);
          upserts.push({
            id: d.jobId, account_id: account.id, action: a.action, media_id: d.media ? d.media.id : null,
            caption: d.caption, link: a.link ? d.link : "", link_text: a.link ? d.link_text : "",
            run_date: runDate, run_at: runAt ? new Date(runAt).toISOString() : null,
            all_accounts: !!d.allAccounts || !!d.allPhones,
            all_phones: !!d.allPhones,
          });
        } else if (d.jobId) {
          deletes.push(d.jobId);
        }
      });
    }
    if (problem) { toast(problem, "err"); return; }
    setSaving(true);
    try {
      if (upserts.length) await api.upsertJobs(upserts);
      for (const id of deletes) await api.deleteJob(id);
      toast(moved.length ? `Saved · ${moved.join(", ")}` : "Saved");
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
            const rows = drafts[a.action];
            if (!rows) return null;
            const many = rows.length > 1;
            return rows.map((d, idx) => {
              const editableAfterPost = d.status === "done" && !!d.allAccounts;
              const locked = (d.status === "done" && !editableAfterPost) || d.status === "running" || !!d.mirroredFrom;
              const originHandle = d.mirroredFrom ? (d.mirroredLabel || (device.accounts.find((x) => x.id === d.mirroredFrom)?.handle ? `@${device.accounts.find((x) => x.id === d.mirroredFrom)!.handle}` : "another account")) : "";
              const fromOtherPhone = !!d.mirroredFrom && !device.accounts.some((x) => x.id === d.mirroredFrom);
              const last = idx === rows.length - 1;
              return (
                <div className="section" key={`${a.action}:${d.jobId ?? `new${idx}`}`}>
                  <div className="sec-head">
                    {idx === 0 ? (
                      <label className="switch">
                        <input type="checkbox" checked={d.enabled} disabled={locked || editableAfterPost} onChange={(e) => upd(a.action, idx, { enabled: e.target.checked })} />
                      </label>
                    ) : (
                      <button className="btn sm ghost" title={`Remove ${a.label.toLowerCase()} ${idx + 1} from this day`} disabled={locked} onClick={() => removeRow(a, idx)}>✕</button>
                    )}
                    <h3>{a.label}{many ? ` ${idx + 1}` : ""} <span className="dim small">· {a.media === "video" ? "video" : "photo or video"}{a.link ? " + link" : ""}{many && idx > 0 ? ` · runs on Run day press ${idx + 1}` : ""}</span></h3>
                    {d.status && <Pill kind={d.status}>{d.status}</Pill>}
                    {d.job?.locked && <span className="small" style={{ color: "var(--amber)" }} title={d.job.locked}>🔒 {d.job.locked}</span>}
                    {d.job && <button className="btn sm ghost" onClick={() => setDetail(d.job!)}>Details</button>}
                  </div>
                  {d.enabled && d.mirroredFrom && d.status !== "done" && (
                    <div className="sec-body" style={{ paddingTop: 0 }}>
                      <div className="row" style={{ alignItems: "center", gap: 8 }}>
                        <span className="small dim">Day</span>
                        <input className="input" type="date" style={{ width: 160 }} value={d.run_date || date} disabled title={`This copy moves with the story on ${originHandle}: change the day there`} />
                        <span className="small dim">moves with {originHandle}'s story — change the day there</span>
                      </div>
                    </div>
                  )}
                  {d.enabled && !locked && !d.mirroredFrom && (
                    <div className="sec-body" style={{ paddingTop: 0 }}>
                      <div className="row" style={{ alignItems: "center", gap: 8 }}>
                        <span className="small dim">Day</span>
                        <input className="input" type="date" style={{ width: 160 }} value={d.run_date || date} onChange={(e) => upd(a.action, idx, { run_date: e.target.value || date })} title="Pick another day and Save: this moves to that day (all-accounts copies move with it)" />
                        {d.run_date && d.run_date !== date && <span className="small" style={{ color: "var(--amber)" }}>moves to {parseYmd(d.run_date).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" })} on Save</span>}
                        {!a.link && (
                          <>
                            <span className="small dim" style={{ marginLeft: 8 }}>Time</span>
                            <input className="input" type="datetime-local" style={{ width: 200 }} value={d.run_at} onChange={(e) => upd(a.action, idx, { run_at: e.target.value })} title="Optional: a fixed time for this one. Leave it empty and it goes out with its Run day press." />
                          </>
                        )}
                      </div>
                    </div>
                  )}
                  {d.enabled && d.mirroredFrom && (
                    <div className="sec-body">
                      <div className="small dim">Set for {fromOtherPhone ? "every phone" : "every account on this phone"} from <b>{originHandle}</b>. Edit or remove it there.</div>
                      {!d.media && <div className="small" style={{ color: "var(--warn, #e8c547)" }}>No copy of this clip on this phone yet: AirDrop it here, add it to the album, then Sync.</div>}
                    </div>
                  )}
                  {d.enabled && !d.mirroredFrom && (
                    <div className="sec-body">
                      {editableAfterPost && <div className="small dim">Already posted here. Changes below apply to the accounts that haven't posted this story yet.</div>}
                      {a.link && (
                        <label className="switch" style={{ alignSelf: "flex-start" }}>
                          <input type="checkbox" checked={!!d.allAccounts || !!d.allPhones} disabled={locked || editableAfterPost || !!d.allPhones} onChange={(e) => upd(a.action, idx, { allAccounts: e.target.checked })} />
                          <span className="small">Post this story on every account on this phone (each with its own link)</span>
                        </label>
                      )}
                      {a.link && account.platform === "instagram" && (
                        <label className="switch" style={{ alignSelf: "flex-start" }}>
                          <input type="checkbox" checked={!!d.allPhones} disabled={locked || editableAfterPost} onChange={(e) => upd(a.action, idx, { allPhones: e.target.checked, allAccounts: e.target.checked || !!d.allAccounts })} />
                          <span className="small">Post this story on every Instagram account on every phone (the same clip is found on each phone by its capture time)</span>
                        </label>
                      )}
                      <div className="media-pick">
                        <MediaThumb media={d.media} className="th" />
                        <div className="grow">
                          <div className="small">{d.media ? d.media.filename : <span className="dim">No media chosen</span>}</div>
                          {d.media && d.media.index !== null && <div className="small dim">#{d.media.index + 1} in Recents</div>}
                          {d.media && !inAlbum(d.media, device.queue_album) && !inAlbum(d.media, device.story_album) && (
                            <div className="small" style={{ color: "var(--amber)" }}>Not in {device.queue_album || "the album"} yet: add it in Photos before it runs, then Sync.</div>
                          )}
                        </div>
                        <button className="btn sm" disabled={locked} onClick={() => setPicker({ action: a.action, idx })}>{d.media ? "Change" : "Choose from roll"}</button>
                      </div>
                      {!a.link && (
                        <div className="field">
                          <label>Caption</label>
                          <textarea className="textarea" disabled={locked} value={d.caption} onChange={(e) => upd(a.action, idx, { caption: e.target.value })} placeholder="Caption, hashtags…" />
                        </div>
                      )}
                      {a.link && (
                        <>
                          <div className="field">
                            <label>Link sticker text</label>
                            <input className="input" disabled={locked} value={d.link_text} onChange={(e) => upd(a.action, idx, { link_text: e.target.value })} placeholder="e.g. Free workout plan" />
                          </div>
                          <div className="field">
                            <label>Link <span className="dim">(defaults to the account's link)</span></label>
                            <input className="input" disabled={locked} value={d.link} onChange={(e) => upd(a.action, idx, { link: e.target.value })} placeholder="https://…" />
                          </div>
                        </>
                      )}
                      {d.job?.last_error && <div className="small" style={{ color: "var(--red)" }}>{d.job.last_error}</div>}
                      {d.job?.finished_at && <div className="small dim">Finished {fmtDateTime(d.job.finished_at)}</div>}
                    </div>
                  )}
                  {!a.link && last && d.enabled && (
                    <div className="sec-body" style={{ paddingTop: 0 }}>
                      <button className="btn sm ghost" style={{ alignSelf: "flex-start" }} onClick={() => addRow(a)} title={`Another ${a.label.toLowerCase()} on this day. It posts on the next "Run day" press, or at a time you set on it.`}>+ Add another {a.label.toLowerCase()}</button>
                    </div>
                  )}
                </div>
              );
            });
          })}
        </div>
        <div className="drawer-foot">
          <span className="small dim grow">Runs in the phone's queue for that day: {actions.map((a) => a.label).join(" → ")}, after earlier accounts. A second reel or trial on the same day waits for the next "Run day" press. One story a day.</span>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
        </div>
      </div>
      {picker && (
        <MediaPicker
          deviceId={device.id}
          deviceName={device.name}
          kind={actions.find((a) => a.action === picker.action)!.media}
          selectedId={drafts[picker.action]?.[picker.idx]?.media?.id ?? null}
          onSelect={(m) => upd(picker.action, picker.idx, { media: m })}
          onClose={() => setPicker(null)}
        />
      )}
      {detail && <JobDetail job={detail} onClose={() => setDetail(null)} onChanged={onSaved} />}
    </>
  );
}
