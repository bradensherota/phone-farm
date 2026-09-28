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
          <div className="th">{thumbUrl(m) ? <img src={thumbUrl(m)} alt="" loading="lazy" /> : m.kind}</div>
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

export default function MediaPicker({ deviceId, deviceName, kind, selectedId, onSelect, onClose }: Props) {
  const [items, setItems] = useState<Media[]>([]);
  const [filter, setFilter] = useState<"all" | "video" | "photo">(kind === "video" ? "video" : "all");
  const [storyAlbum, setStoryAlbum] = useState("");
  const [albumOnly, setAlbumOnly] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const toast = useToast();

  useEffect(() => {
    api.media(deviceId).then(setItems).catch((e) => toast(e.message, "err"));
    api.device(deviceId).then((d) => {
      setStoryAlbum(d.story_album || "");
    }).catch(() => {});
  }, [deviceId]);

  // default to the story album when it has anything usable in it
  useEffect(() => {
    if (!storyAlbum || !items.length) return;
    setAlbumOnly(items.some((m) => inAlbum(m, storyAlbum) && (kind !== "video" || m.kind === "video")));
  }, [storyAlbum, items.length]);

  const shown = useMemo(
    () => items.filter((m) => (filter === "all" ? true : m.kind === filter)).filter((m) => (albumOnly ? inAlbum(m, storyAlbum) : true)),
    [items, filter, albumOnly, storyAlbum],
  );

  const sync = () => {
    setSyncing(true);
    api.syncDevice(deviceId).then(setItems).catch((e) => toast(e.message, "err")).finally(() => setSyncing(false));
  };

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Camera roll · {deviceName}</h2>
          {storyAlbum && (
            <label className="switch" title={`Only items in the "${storyAlbum}" album on the phone`}>
              <input type="checkbox" checked={albumOnly} onChange={(e) => setAlbumOnly(e.target.checked)} />
              <span className="small">★ {storyAlbum} album only</span>
            </label>
          )}
          {kind === "any" && (
            <select className="select" style={{ width: 130 }} value={filter} onChange={(e) => setFilter(e.target.value as any)}>
              <option value="all">All</option>
              <option value="video">Videos</option>
              <option value="photo">Photos</option>
            </select>
          )}
          {kind === "video" && <span className="pill">videos only</span>}
          <button className="btn sm" onClick={sync} disabled={syncing}>{syncing ? "Syncing…" : "Sync"}</button>
          <button className="btn sm ghost" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <RollGrid items={shown} selectedId={selectedId} onSelect={(m) => { onSelect(m); onClose(); }} storyAlbum={storyAlbum} />
        </div>
        <div className="modal-foot">
          <span className="dim small grow">
            Items in the ★ album are picked from that album on the phone (short list, no scrolling). Anything else is found by its position in Recents.
          </span>
        </div>
      </div>
    </div>
  );
}
