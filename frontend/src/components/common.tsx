import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { Media, Platform } from "../lib/api";
import { screenUrl, thumbUrl } from "../lib/api";

// ---- polling ---------------------------------------------------------------
export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms === null) return;
    const id = setInterval(() => ref.current(), ms);
    return () => clearInterval(id);
  }, [ms]);
}

export function usePoll<T>(loader: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    loader().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e.message || e)));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { load(); }, [load]);
  useInterval(load, ms);
  return { data, error, reload: load, setData };
}

// ---- toast -----------------------------------------------------------------
type ToastFn = (msg: string, kind?: "ok" | "err") => void;
const ToastCtx = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [t, setT] = useState<{ msg: string; kind: "ok" | "err" } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show: ToastFn = useCallback((msg, kind = "ok") => {
    setT({ msg, kind });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setT(null), kind === "err" ? 5000 : 2500);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {t && <div className={`toast ${t.kind === "err" ? "err" : ""}`}>{t.msg}</div>}
    </ToastCtx.Provider>
  );
}

// ---- small bits -------------------------------------------------------------
export function PlatformIcon({ platform }: { platform: Platform }) {
  const glyph = platform === "instagram" ? "IG" : platform === "tiktok" ? "TT" : "X";
  return <span className={`plat ${platform}`} title={platform}>{glyph}</span>;
}

export function Pill({ kind, children }: { kind?: string; children: React.ReactNode }) {
  return <span className={`pill ${kind || ""}`}>{children}</span>;
}

export function StatusDot({ connected, busy }: { connected: boolean; busy?: boolean }) {
  const cls = !connected ? "off" : busy ? "busy" : "on";
  return <span className={`dot ${cls}`} />;
}

export function MediaThumb({ media, className }: { media: Media | null | undefined; className?: string }) {
  const url = thumbUrl(media);
  if (!media) return <div className={className}>none</div>;
  return <img className={className} src={url} alt={media.filename} onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = "hidden"; }} />;
}

/** Live phone screen. Polls the screenshot endpoint while mounted. */
export function PhoneScreen({ deviceId, connected, intervalMs = 1500 }: { deviceId: string; connected: boolean; intervalMs?: number }) {
  const [src, setSrc] = useState(() => screenUrl(deviceId));
  const [failed, setFailed] = useState(false);
  // only poll while this window is actually visible; a hidden page hammering the phone's runner is wasted
  useInterval(() => { if (connected && document.visibilityState === "visible") setSrc(screenUrl(deviceId)); }, connected ? intervalMs : null);
  useEffect(() => { setFailed(false); setSrc(screenUrl(deviceId)); }, [deviceId, connected]);
  return (
    <div className="phone-frame">
      {/* the img stays mounted even after a failure so polling keeps going and the screen returns when the phone unlocks */}
      {connected && <img src={src} alt="screen" style={failed ? { display: "none" } : undefined} onError={() => setFailed(true)} onLoad={(e) => setFailed((e.currentTarget as HTMLImageElement).naturalWidth === 0)} />}
      {(!connected || failed) && <div className="offline">{connected ? "screen locked" : "offline"}</div>}
    </div>
  );
}

export function fmtBytes(n: number): string {
  if (n > 1e9) return (n / 1e9).toFixed(1) + " GB";
  if (n > 1e6) return (n / 1e6).toFixed(0) + " MB";
  if (n > 1e3) return (n / 1e3).toFixed(0) + " KB";
  return n + " B";
}
