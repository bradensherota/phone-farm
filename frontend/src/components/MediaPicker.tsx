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

export function RollGrid({ items, selectedId, onSelect }: { items: Media[]; selectedId: number | null; onSelect: (m: Media) => void }) {
  if (!items.length) return <div className="empty">Nothing in the camera roll yet. AirDrop content to the phone, then Sync.</div>;
  return (
    <div className="roll">
      {items.map((m) => (
        <div key={m.id} className={`roll-item ${selectedId === m.id ? "sel" : ""}`} onClick={() => onSelect(m)} title={m.filename}>
          <div className="th">{thumbUrl(m) ? <img src={thumbUrl(m)} alt="" loading="lazy" /> : m.kind}</div>
          <span className="badge">{m.kind === "video" ? `▶ ${fmtDuration(m.duration_s)}` : "photo"}</span>
          {m.index !== null && <span className="idx">#{m.index + 1}</span>}
          <div className="meta">
            <span className="fn">{m.filename}</span>
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
  const [syncing, setSyncing] = useState(false);
  const toast = useToast();

  useEffect(() => { api.media(deviceId).then(setItems).catch((e) => toast(e.message, "err")); }, [deviceId]);

  const shown = useMemo(() => items.filter((m) => filter === "all" ? true : m.kind === filter), [items, filter]);

  const sync = () => {
    setSyncing(true);
    api.syncDevice(deviceId).then(setItems).catch((e) => toast(e.message, "err")).finally(() => setSyncing(false));
  };

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Camera roll · {deviceName}</h2>
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
          <RollGrid items={shown} selectedId={selectedId} onSelect={(m) => { onSelect(m); onClose(); }} />
        </div>
        <div className="modal-foot">
          <span className="dim small grow">#n is the item's position in the phone's Recents grid, newest first. That's how the phone finds it when posting.</span>
        </div>
      </div>
    </div>
  );
}
