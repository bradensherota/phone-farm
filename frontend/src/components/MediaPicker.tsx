import { useEffect, useMemo, useState } from "react";
import type { Media } from "../lib/api";
import { api, fmtDuration, thumbUrl } from "../lib/api";
import { useToast } from "./common";

interface Props {
  deviceId: string;
  deviceName: string;
  kind: "video" | "any";
  selectedId: number | null;
  onSelect: (m: Media) => void;
  onClose: () => void;
}

export const inAlbum = (m: Media, album: string) =>
  !!album && (m.albums || "").split("|").map((t) => t.trim()).includes(album.trim());

export function RollGrid({ items, selectedId, onSelect, storyAlbum }: { items: Media[]; selectedId: number | null; onSelect: (m: Media) => void; storyAlbum?: string }) {
  if (!items.length) return <div className="empty">Nothing here yet. AirDrop content to the phone, then Sync.</div>;
  return (
    <div className="roll">
      {items.map((m) => (
        <div key={m.id} className={`roll-item ${selectedId === m.id ? "sel" : ""}`} onClick={() => onSelect(m)} title={`${m.filename}${m.albums ? `\nAlbums: ${m.albums.split("|").join(", ")}` : ""}`}>
          <div className="th"><img src={thumbUrl(m)} alt="" loading="lazy" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} /></div>
          <span className="badge">{m.kind === "video" ? `▶ ${fmtDuration(m.duration_s)}` : "photo"}</span>
          {m.index !== null && <span className="idx">#{m.index + 1}</span>}
          {storyAlbum && inAlbum(m, storyAlbum) && <span className="badge album">★ {storyAlbum}</span>}
          <div className="meta">
            <span className="fn">{m.filename.split("/").pop()}</span>
            <span>{new Date(m.created_at).toLocaleDateString([], { month: "short", day: "numeric" })}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function MediaPreview({ media, onUse, onClose }: { media: Media; onUse?: (m: Media) => void; onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const src = `/api/media/${media.id}/preview`;
  return (
    <div className="modal-bg" style={{ zIndex: 40 }} onClick={onClose}>
      <div className="modal" style={{ width: "min(560px, 92vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{media.filename.split("/").pop()} <span className="dim small">· {media.kind === "video" ? `video ${fmtDuration(media.duration_s)}` : "photo"} · {new Date(media.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}{media.index !== null ? ` · #${media.index + 1} in Recents` : ""}</span></h2>
          <button className="btn sm ghost" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body" style={{ display: "grid", placeItems: "center", minHeight: 320 }}>
          {loading && !error && <div className="dim small" style={{ position: "absolute" }}>Pulling from the phone and preparing the preview…</div>}
          {error && <div className="small" style={{ color: "var(--red)" }}>{error}</div>}
          {media.kind === "video" ? (
            <video className="preview-media" src={src} controls autoPlay playsInline onCanPlay={() => setLoading(false)} onError={() => { setLoading(false); setError("Could not load the preview from the phone."); }} />
          ) : (
            <img className="preview-media" src={src} alt="" onLoad={() => setLoading(false)} onError={() => { setLoading(false); setError("Could not load the preview from the phone."); }} />
          )}
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>Back</button>
          {onUse && <button className="btn primary" onClick={() => onUse(media)}>Use this</button>}
        </div>
      </div>
    </div>
  );
}

export default function MediaPicker({ deviceId, deviceName, kind, selectedId, onSelect, onClose }: Props) {
  const [items, setItems] = useState<Media[]>([]);
  const [filter, setFilter] = useState<"all" | "video" | "photo">(kind === "video" ? "video" : "all");
  const [storyAlbum, setStoryAlbum] = useState("");
  const [queueAlbum, setQueueAlbum] = useState("");
  const [albumFilter, setAlbumFilter] = useState<"" | "queue" | "story">("");
  const albumOnly = albumFilter !== "";
  const activeAlbum = albumFilter === "queue" ? queueAlbum : albumFilter === "story" ? storyAlbum : "";
  const [syncing, setSyncing] = useState(false);
  const [preview, setPreview] = useState<Media | null>(null);
  const toast = useToast();

  useEffect(() => {
    api.media(deviceId).then(setItems).catch((e) => toast(e.message, "err"));
    api.device(deviceId).then((d) => {
      setStoryAlbum(d.story_album || "");
      setQueueAlbum(d.queue_album || "");
    }).catch(() => {});
  }, [deviceId]);

  // default to the queue album when it has anything usable in it, else the story album
  useEffect(() => {
    if (!items.length) return;
    const usable = (album: string) => !!album && items.some((m) => inAlbum(m, album) && (kind !== "video" || m.kind === "video"));
    setAlbumFilter(usable(queueAlbum) ? "queue" : usable(storyAlbum) ? "story" : "");
  }, [storyAlbum, queueAlbum, items.length]);

  const shown = useMemo(
    () => items.filter((m) => (filter === "all" ? true : m.kind === filter)).filter((m) => (albumOnly ? inAlbum(m, activeAlbum) : true)),
    [items, filter, albumOnly, activeAlbum],
  );

  const sync = () => {
    setSyncing(true);
    api.syncDevice(deviceId, true).then(setItems).catch((e) => toast(e.message, "err")).finally(() => setSyncing(false));
  };

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Camera roll · {deviceName}</h2>
          <div className="tabs compact" title="Show only items that are in this album on the phone">
            <button className={albumFilter === "" ? "active" : ""} onClick={() => setAlbumFilter("")}>All</button>
            {queueAlbum && <button className={albumFilter === "queue" ? "active" : ""} onClick={() => setAlbumFilter("queue")}>{queueAlbum}</button>}
            {storyAlbum && <button className={albumFilter === "story" ? "active" : ""} onClick={() => setAlbumFilter("story")}>★ {storyAlbum}</button>}
          </div>
          {kind === "any" && (
            <select className="select" style={{ width: 130 }} value={filter} onChange={(e) => setFilter(e.target.value as any)}>
              <option value="all">All</option>
              <option value="video">Videos</option>
              <option value="photo">Photos</option>
            </select>
          )}
          {kind === "video" && <span className="pill">videos only</span>}
          <button className="btn sm" onClick={sync} disabled={syncing} title="Re-read the phone's albums (fast; no thumbnails)">{syncing ? "Syncing…" : "Sync albums"}</button>
          <button className="btn sm ghost" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          {albumOnly && shown.length === 0 && <div className="empty">Nothing from "{activeAlbum}" is in the app's copy of the roll yet. Add items to the album on the phone, then press Sync.</div>}
          {(!albumOnly || shown.length > 0) && <RollGrid items={shown} selectedId={selectedId} onSelect={(m) => setPreview(m)} storyAlbum={storyAlbum} />}
        </div>
        <div className="modal-foot">
          <span className="dim small grow">
            Click an item to preview it, then "Use this". Anything you plan must be in the "{queueAlbum || "queue"}" album (or the ★ album) on the phone; that's where the app picks it from.
          </span>
        </div>
      </div>
      {preview && <MediaPreview media={preview} onUse={(m) => { onSelect(m); setPreview(null); onClose(); }} onClose={() => setPreview(null)} />}
    </div>
  );
}
