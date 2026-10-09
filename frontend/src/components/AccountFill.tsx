import { useEffect, useMemo, useRef, useState } from "react";
import type { Account, ActionKey, Device, Job, JobIn, Media } from "../lib/api";
import { actionsFor, addDays, api, fmtDuration, parseYmd, thumbUrl, ymd } from "../lib/api";
import { PlatformIcon, useConfirm, usePoll, useToast } from "./common";
import { HoverThumb, inAlbum } from "./MediaPicker";

// Fill an account's coming days in one sitting: pick clips in the order they should go out,
// type the caption under each, and every pick lands on the next day that has nothing of its
// kind planned for this account. Save once; the day cells stay for edits.

interface Props {
  account: Account;
  device: Device;
  weekStart: Date;
  next: { account: Account; device: Device } | null;
  onSwitch: (account: Account, device: Device) => void;
  onClose: () => void;
  onSaved: () => void;
}

interface Pick { media: Media; action: ActionKey; caption: string }

type Slot = "reel" | "trial" | "story";
const slotOf = (action: ActionKey): Slot => (action === "ig_trial" ? "trial" : action === "ig_story" || action === "tt_story" ? "story" : "reel");
const LETTER: Record<Slot, string> = { reel: "R", trial: "T", story: "S" };
const dayLabel = (d: string) => parseYmd(d).toLocaleDateString([], { weekday: "short", day: "numeric" });

export default function AccountFill({ account, device, weekStart, next, onSwitch, onClose, onSaved }: Props) {
  const toast = useToast();
  const ask = useConfirm();
  const { data: settings } = usePoll(api.settings, 60000);
  const ttStories = settings?.tiktok_stories === "true";
  const requireBlocks = (settings?.require_blocks ?? "true") !== "false";
  const blockDays = Math.max(1, Number(settings?.block_days || 4) || 4);
  const modes = useMemo(() => actionsFor(account.platform).filter((a) => a.action !== "tt_story" || ttStories), [account.platform, ttStories]);

  const [items, setItems] = useState<Media[]>([]);
  const [existing, setExisting] = useState<Job[]>([]);
  const [mode, setMode] = useState<ActionKey>(modes[0].action);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [albumFilter, setAlbumFilter] = useState<"" | "queue" | "story">("queue");
  const [albumTouched, setAlbumTouched] = useState(false);
  const [allAccounts, setAllAccounts] = useState(false);
  const [allPhones, setAllPhones] = useState(false);
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [lastAdded, setLastAdded] = useState<number | null>(null);
  const [batchLink, setBatchLink] = useState("");
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const today = ymd(new Date());
  const [fillFrom, setFillFrom] = useState(() => (ymd(weekStart) > today ? ymd(weekStart) : today));

  const queueAlbum = device.queue_album || "";
  const storyAlbum = device.story_album || "";
  const albumSorts = device.album_sorts || {};
  const activeAlbum = albumFilter === "queue" ? queueAlbum : albumFilter === "story" ? storyAlbum : "";
  const curSlot = slotOf(mode);

  const loadExisting = () =>
    api.jobs({ start: fillFrom, end: ymd(addDays(parseYmd(fillFrom), 120)), device_id: device.id })
      .then((rows) => setExisting(rows.filter((j) => j.account_id === account.id)))
      .catch((e) => toast(e.message, "err"));

  useEffect(() => {
    setPicks([]); setPreviewId(null); setAlbumTouched(false);
    api.media(device.id).then(setItems).catch((e) => toast(e.message, "err"));
  }, [account.id, device.id]);
  useEffect(() => { loadExisting(); }, [account.id, device.id, fillFrom]);

  // the album follows the mode unless you picked one yourself: clips for reels, the ★ album for stories
  useEffect(() => {
    if (albumTouched || !items.length) return;
    const usable = (album: string, video: boolean) => !!album && items.some((m) => inAlbum(m, album) && (!video || m.kind === "video"));
    if (curSlot === "story") setAlbumFilter(usable(storyAlbum, false) ? "story" : "queue");
    else setAlbumFilter("queue");
  }, [curSlot, items.length, albumTouched, storyAlbum, queueAlbum]);

  const videosOnly = curSlot !== "story";
  const shown = useMemo(() => {
    const list = items.filter((m) => (videosOnly ? m.kind === "video" : true)).filter((m) => (activeAlbum ? inAlbum(m, activeAlbum) : true));
    if (!activeAlbum) return list;
    const asc = albumSorts[activeAlbum.trim()]?.ascending ?? true;
    return [...list].sort((a, b) => { const d = new Date(a.created_at).getTime() - new Date(b.created_at).getTime(); return asc ? d : -d; });
  }, [items, videosOnly, activeAlbum, albumSorts]);
  const orderNote = activeAlbum ? ((albumSorts[activeAlbum.trim()]?.ascending ?? true) ? "oldest first, as on the phone" : "newest first, as on the phone") : "";

  // where each pick lands: per kind, the next day from `fillFrom` with nothing of that kind yet
  const taken = useMemo(() => {
    const m = new Map<string, Set<ActionKey>>();
    for (const j of existing) { if (!m.has(j.run_date)) m.set(j.run_date, new Set()); m.get(j.run_date)!.add(j.action); }
    return m;
  }, [existing]);
  const plan = useMemo(() => {
    const out = new Map<number, { day: string; n: number }>();
    for (const a of modes) {
      let cursor = parseYmd(fillFrom);
      let n = 0;
      for (const p of picks) {
        if (p.action !== a.action) continue;
        while (taken.get(ymd(cursor))?.has(a.action)) cursor = addDays(cursor, 1);
        out.set(p.media.id, { day: ymd(cursor), n: ++n });
        cursor = addDays(cursor, 1);
      }
    }
    return out;
  }, [picks, taken, fillFrom, modes]);
  const existingByMedia = useMemo(() => {
    const m = new Map<number, Job>();
    for (const j of [...existing].sort((a, b) => a.run_date.localeCompare(b.run_date))) if (j.media_id !== null && !m.has(j.media_id)) m.set(j.media_id, j);
    return m;
  }, [existing]);

  // the strip: from the start day to the last day anything lands on (at least a week)
  const days = useMemo(() => {
    let last = ymd(addDays(parseYmd(fillFrom), 6));
    for (const v of plan.values()) if (v.day > last) last = v.day;
    for (const j of existing) if (j.run_date > last && j.run_date <= ymd(addDays(parseYmd(fillFrom), 27))) last = j.run_date;
    const out: string[] = [];
    for (let d = parseYmd(fillFrom); ymd(d) <= last; d = addDays(d, 1)) out.push(ymd(d));
    return out;
  }, [fillFrom, plan, existing]);
  const newOn = useMemo(() => {
    const m = new Map<string, Set<ActionKey>>();
    for (const p of picks) { const v = plan.get(p.media.id); if (!v) continue; if (!m.has(v.day)) m.set(v.day, new Set()); m.get(v.day)!.add(p.action); }
    return m;
  }, [picks, plan]);

  // blocks: per kind, the days it is (or will be) planned on; a day is "complete" when it sits in a
  // run of at least `blockDays` consecutive days. New picks can't be saved until their run is complete.
  const daysByKind = useMemo(() => {
    const m = new Map<ActionKey, Set<string>>();
    for (const a of modes) m.set(a.action, new Set());
    for (const j of existing) if (j.status !== "cancelled" && m.has(j.action)) m.get(j.action)!.add(j.run_date);
    for (const p of picks) { const v = plan.get(p.media.id); if (v) m.get(p.action)?.add(v.day); }
    return m;
  }, [existing, picks, plan, modes]);
  const runLen = (days: Set<string>, d: string) => {
    if (!days.has(d)) return 0;
    let n = 1;
    for (let x = addDays(parseYmd(d), -1); days.has(ymd(x)); x = addDays(x, -1)) n++;
    for (let x = addDays(parseYmd(d), 1); days.has(ymd(x)); x = addDays(x, 1)) n++;
    return n;
  };
  const complete = (action: ActionKey, d: string) => !requireBlocks || runLen(daysByKind.get(action) || new Set(), d) >= blockDays;
  const shortfall = useMemo(() => {
    const out = new Map<ActionKey, number>();
    if (!requireBlocks) return out;
    for (const a of modes) {
      const first = picks.find((p) => p.action === a.action);
      if (!first) continue;
      const n = runLen(daysByKind.get(a.action) || new Set(), plan.get(first.media.id)!.day);
      if (n < blockDays) out.set(a.action, blockDays - n);
    }
    return out;
  }, [picks, plan, daysByKind, modes, requireBlocks, blockDays]);
  const canSave = picks.length > 0 && shortfall.size === 0;

  const toggle = (m: Media) => {
    const cur = picks.find((p) => p.media.id === m.id);
    if (cur && cur.action === mode) { setPicks(picks.filter((p) => p.media.id !== m.id)); return; }
    if (cur) { setPicks(picks.map((p) => (p.media.id === m.id ? { ...p, action: mode } : p))); setLastAdded(m.id); return; }
    if (videosOnly && m.kind !== "video") { toast("Reels need a video", "err"); return; }
    setPicks([...picks, { media: m, action: mode, caption: "" }]);
    setLastAdded(m.id);
  };
  const setCaption = (id: number, caption: string) => setPicks(picks.map((p) => (p.media.id === id ? { ...p, caption } : p)));

  const defaultLink = account.platform === "instagram" ? settings?.default_link_instagram || "" : account.platform === "tiktok" ? settings?.default_link_tiktok || "" : "";
  const storyLink = (account.story_link || defaultLink || batchLink).trim();
  const storyPicks = picks.filter((p) => slotOf(p.action) === "story").length;
  const needLink = storyPicks > 0 && !storyLink;

  const save = async (andNext: boolean) => {
    if (!picks.length) { if (andNext && next) onSwitch(next.account, next.device); else onClose(); return; }
    if (needLink) { toast("Stories need a link; add one above the grid", "err"); return; }
    const body: JobIn[] = picks.map((p) => {
      const story = slotOf(p.action) === "story";
      return {
        account_id: account.id, action: p.action, media_id: p.media.id,
        caption: story ? "" : p.caption, link: story ? storyLink : "", link_text: story ? p.caption : "",
        run_date: plan.get(p.media.id)!.day, run_at: null,
        all_accounts: story && (allAccounts || allPhones), all_phones: story && allPhones,
      };
    });
    setSaving(true);
    try {
      await api.upsertJobs(body);
      toast(`Saved ${picks.length} action${picks.length === 1 ? "" : "s"} for @${account.handle}`);
      setPicks([]);
      onSaved();
      if (andNext && next) onSwitch(next.account, next.device);
      else { await loadExisting(); api.media(device.id).then(setItems).catch(() => {}); }
    } catch (e: any) { toast(e.message, "err"); }
    finally { setSaving(false); }
  };
  const close = async () => {
    if (picks.length && !(await ask(`Discard ${picks.length} unsaved pick${picks.length === 1 ? "" : "s"} for @${account.handle}?`, "Discard"))) return;
    onClose();
  };
  const sync = () => {
    setSyncing(true);
    api.syncDevice(device.id, true).then(setItems).catch((e) => toast(e.message, "err")).finally(() => setSyncing(false));
  };
  const fromOptions = useMemo(() => { const o: string[] = []; for (let i = 0; i < 21; i++) o.push(ymd(addDays(new Date(), i))); if (!o.includes(fillFrom)) o.unshift(fillFrom); return o; }, [fillFrom]);
  const count = (a: ActionKey) => picks.filter((p) => p.action === a).length;

  return (
    <div className="modal-bg" onClick={close}>
      <div className="modal fill-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <PlatformIcon platform={account.platform} />
          <h2 style={{ whiteSpace: "nowrap", flex: "0 1 auto" }}>@{account.handle} <span className="dim small">· {device.name}</span></h2>
          <div className="tabs compact" title="The app only works with these two albums on the phone">
            {queueAlbum && <button className={albumFilter === "queue" ? "active" : ""} onClick={() => { setAlbumTouched(true); setAlbumFilter("queue"); }}>{queueAlbum}</button>}
            {storyAlbum && <button className={albumFilter === "story" ? "active" : ""} onClick={() => { setAlbumTouched(true); setAlbumFilter("story"); }}>★ {storyAlbum}</button>}
          </div>
          {orderNote && <span className="small dim">{orderNote}</span>}
          {videosOnly && <span className="pill">videos only</span>}
          <button className="btn sm" onClick={sync} disabled={syncing} title="Re-read the phone's albums (fast; no thumbnails)">{syncing ? "Syncing…" : "Sync albums"}</button>
          <button className="btn sm ghost" onClick={close}>✕</button>
        </div>

        <div className="fill-bar">
          {modes.map((a) => (
            <button key={a.action} className={`fill-mode ${slotOf(a.action)} ${mode === a.action ? "active" : ""}`} onClick={() => setMode(a.action)} title={`Tap clips to plan them as ${a.label.toLowerCase()}s, in the order you tap${requireBlocks ? `. ${a.label}s run only in blocks of ${blockDays} days in a row` : ""}`}>
              {a.label} <span className="n">{count(a.action)}</span>
              {shortfall.has(a.action) && <span className="need">pick {shortfall.get(a.action)} more</span>}
            </button>
          ))}
          {curSlot === "story" && (
            <>
              <label className="switch small" title="Also plan this story on the other accounts of this phone (each with its own link)">
                <input type="checkbox" checked={allAccounts || allPhones} disabled={allPhones} onChange={(e) => setAllAccounts(e.target.checked)} />
                <span>every account on this phone</span>
              </label>
              {account.platform === "instagram" && (
                <label className="switch small" title="Also plan it on every Instagram account on every phone (the same clip is found on each phone by its capture time)">
                  <input type="checkbox" checked={allPhones} onChange={(e) => { setAllPhones(e.target.checked); if (e.target.checked) setAllAccounts(true); }} />
                  <span>every phone</span>
                </label>
              )}
              {needLink && <input className="input" style={{ width: 260 }} placeholder="Link for these stories (https://…)" value={batchLink} onChange={(e) => setBatchLink(e.target.value)} />}
            </>
          )}
          <span className="grow" />
          <label className="small dim row" style={{ gap: 6 }}>from
            <select className="select" style={{ width: 130 }} value={fillFrom} onChange={(e) => setFillFrom(e.target.value)}>
              {fromOptions.map((d) => <option key={d} value={d}>{d === today ? `today, ${dayLabel(d)}` : dayLabel(d)}</option>)}
            </select>
          </label>
        </div>

        <div className="fill-days">
          {days.map((d) => (
            <span key={d} className={`day ${d === today ? "today" : ""}`} title={`${d}: what lands on this day for @${account.handle}`}>
              <b>{dayLabel(d)}</b>
              {modes.map((a) => {
                const s = slotOf(a.action);
                const isNew = newOn.get(d)?.has(a.action);
                const has = taken.get(d)?.has(a.action);
                const hollow = (isNew || has) && !complete(a.action, d);
                return <span key={a.action} className={`l ${isNew ? s : has ? `${s} existing` : "none"} ${hollow ? "hollow" : ""}`} title={hollow ? `${a.label}s on ${dayLabel(d)} are locked until this account has ${blockDays} days of them in a row` : undefined}>{isNew || has ? LETTER[s] : "·"}</span>;
              })}
            </span>
          ))}
        </div>

        <div className="modal-body">
          {shown.length === 0 && <div className="empty">Nothing from "{activeAlbum || queueAlbum}" is in the app's copy of the roll yet. Add items to the album on the phone, then press Sync.</div>}
          <div className="roll">
            {shown.map((m) => {
              const pick = picks.find((p) => p.media.id === m.id);
              const where = pick ? plan.get(m.id) : undefined;
              const ex = !pick ? existingByMedia.get(m.id) : undefined;
              const cls = pick ? `fill-${slotOf(pick.action)}` : ex ? "fill-existing" : "";
              return (
                <div key={m.id} className={`roll-item ${cls} ${m.posted_at ? "posted" : ""}`} onClick={() => toggle(m)}
                     onMouseEnter={() => setHoverId(m.id)} onMouseLeave={() => setHoverId((h) => (h === m.id ? null : h))}
                     title={`${m.filename}${m.posted_on ? `\nPosted on ${m.posted_on}` : ""}${m.scheduled_on ? `\nPlanned: ${m.scheduled_on}` : ""}`}>
                  <div className="th">
                    {previewId === m.id ? (
                      m.kind === "video"
                        ? <video src={`/api/media/${m.id}/preview`} autoPlay controls playsInline onClick={(e) => e.stopPropagation()} />
                        : <img className="full" src={`/api/media/${m.id}/preview`} alt="" onClick={(e) => e.stopPropagation()} />
                    ) : (
                      <HoverThumb media={m} playing={hoverId === m.id && !pick} />
                    )}
                  </div>
                  <button className="badge" title={previewId === m.id ? "Stop the preview" : "Preview it right here"} onClick={(e) => { e.stopPropagation(); setPreviewId(previewId === m.id ? null : m.id); }}>
                    {previewId === m.id ? "■ stop" : m.kind === "video" ? `▶ ${fmtDuration(m.duration_s)}` : "photo"}
                  </button>
                  {pick && where && <span className={`fill-badge ${slotOf(pick.action)}`}>{LETTER[slotOf(pick.action)]}{where.n} · {dayLabel(where.day)}</span>}
                  {ex && <span className={`fill-badge existing ${slotOf(ex.action)}`} title={`Already planned: ${ex.status}`}>{LETTER[slotOf(ex.action)]} · {dayLabel(ex.run_date)}</span>}
                  {!pick && !ex && m.index !== null && <span className="idx">#{m.index + 1}</span>}
                  {storyAlbum && inAlbum(m, storyAlbum) && <span className="badge album">★ {storyAlbum}</span>}
                  {(m.posted_at || m.scheduled_for) && (
                    <span className="tags">
                      {m.posted_at && <span className="badge posted" title={m.posted_on ? `Posted on ${m.posted_on}` : "Posted"}>Posted {new Date(m.posted_at).toLocaleDateString([], { month: "short", day: "numeric" })}</span>}
                      {m.scheduled_for && <span className="badge scheduled" title={m.scheduled_on ? `Planned: ${m.scheduled_on}` : "Planned"}>Scheduled {new Date(m.scheduled_for + "T00:00:00").toLocaleDateString([], { month: "short", day: "numeric" })}</span>}
                    </span>
                  )}
                  <div className="meta">
                    <span className="fn">{m.filename.split("/").pop()}</span>
                    <span>{new Date(m.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}</span>
                  </div>
                  {pick && (
                    <div className="cap" onClick={(e) => e.stopPropagation()}>
                      {!inAlbum(m, queueAlbum) && !inAlbum(m, storyAlbum) && (
                        <div className="noalbum" title="The app only picks from the queue or story album on the phone. Add this clip to the album in Photos before it runs.">Not in {queueAlbum || "the album"} yet: add it in Photos before it runs</div>
                      )}
                      <input value={pick.caption} autoFocus={lastAdded === m.id} placeholder={slotOf(pick.action) === "story" ? "Sticker text…" : "Caption…"}
                             onChange={(e) => setCaption(m.id, e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="modal-foot">
          <span className="dim small grow">
            {shortfall.size > 0
              ? <b style={{ color: "var(--amber)" }}>{[...shortfall.entries()].map(([a, n]) => `${modes.find((m) => m.action === a)?.label}s: pick ${n} more`).join(" · ")} — {blockDays} days in a row unlock a kind.</b>
              : <>Tap a clip to plan it as the active kind; tap again to remove it. Each kind fills its own empty days from {dayLabel(fillFrom)} on. Dashed tiles are already planned.{requireBlocks ? ` Hollow letters are locked until ${blockDays} days in a row.` : ""}</>}
            {(() => { const n = picks.filter((p) => !inAlbum(p.media, queueAlbum) && !inAlbum(p.media, storyAlbum)).length; return n ? <b style={{ color: "var(--amber)" }}> {n} pick{n === 1 ? " isn't" : "s aren't"} in {queueAlbum || "the album"} yet.</b> : null; })()}
          </span>
          <button className="btn ghost" onClick={close}>Cancel</button>
          <button className="btn primary" disabled={saving || !canSave} title={shortfall.size ? `Complete the block first: ${[...shortfall.entries()].map(([a, n]) => `${n} more ${modes.find((m) => m.action === a)?.label.toLowerCase()}(s)`).join(", ")}` : undefined} onClick={() => save(false)}>{saving ? "Saving…" : `Save ${picks.length || ""}`.trim()}</button>
          {next && <button className="btn" disabled={saving || (picks.length > 0 && !canSave)} onClick={() => save(true)} title={`Save, then open @${next.account.handle}`}>{picks.length ? "Save & next" : "Next"} → @{next.account.handle}</button>}
        </div>
      </div>
    </div>
  );
}
