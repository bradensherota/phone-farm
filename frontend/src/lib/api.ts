export type Platform = "instagram" | "tiktok" | "x";
export type ActionKey = "ig_reel" | "ig_story" | "tt_post" | "tt_story" | "x_post";
export type JobStatus = "scheduled" | "running" | "done" | "failed" | "cancelled";

export interface Account {
  id: number;
  device_id: string;
  platform: Platform;
  handle: string;
  status: "active" | "paused" | "banned";
  story_link: string;
  notes: string;
  sort_order: number;
}

export interface Media {
  id: number;
  device_id: string;
  filename: string;
  kind: "photo" | "video";
  duration_s: number;
  size_bytes: number;
  created_at: string;
  added_at: string | null;
  albums: string;
  present: boolean;
  has_thumb: boolean;
  index: number | null;
}

export interface Device {
  id: string;
  name: string;
  model: string;
  kind: "sim" | "ios";
  routine_start: string;
  gap_min_s: number;
  gap_max_s: number;
  paused: boolean;
  sort_order: number;
  story_album: string;
  connected: boolean;
  connecting: boolean;
  error: string;
  busy: boolean;
  current_job_id: number | null;
  accounts: Account[];
  media_count: number;
  today: { total: number; done: number; failed: number; scheduled: number; running: number };
}

export interface Job {
  id: number;
  account_id: number;
  device_id: string;
  action: ActionKey;
  media_id: number | null;
  media: Media | null;
  caption: string;
  link: string;
  link_text: string;
  run_date: string;
  run_at: string | null;
  sort_order: number;
  status: JobStatus;
  attempts: number;
  last_error: string;
  has_screenshot: boolean;
  started_at: string | null;
  finished_at: string | null;
  next_run: string | null;
  handle: string;
  platform: Platform;
  device_name: string;
}

export interface JobIn {
  id?: number;
  account_id: number;
  action: ActionKey;
  media_id: number | null;
  caption: string;
  link: string;
  link_text: string;
  run_date: string;
  run_at: string | null;
}

export interface JobLog {
  id: number;
  ts: string;
  level: string;
  message: string;
}

export interface ActionMeta {
  action: ActionKey;
  platform: Platform;
  label: string;
  media: "video" | "any";
  link: boolean;
  rank: number;
}

export const ACTIONS: ActionMeta[] = [
  { action: "ig_reel", platform: "instagram", label: "Reel", media: "video", link: false, rank: 0 },
  { action: "ig_story", platform: "instagram", label: "Story", media: "any", link: true, rank: 1 },
  { action: "tt_post", platform: "tiktok", label: "Post", media: "video", link: false, rank: 0 },
  { action: "tt_story", platform: "tiktok", label: "Story", media: "any", link: true, rank: 1 },
  { action: "x_post", platform: "x", label: "Post", media: "any", link: false, rank: 0 },
];

export function actionsFor(platform: Platform): ActionMeta[] {
  return ACTIONS.filter((a) => a.platform === platform);
}

export function actionMeta(action: ActionKey): ActionMeta {
  return ACTIONS.find((a) => a.action === action)!;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = j.detail || JSON.stringify(j);
    } catch {}
    throw new ApiError(res.status, msg);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  devices: () => req<Device[]>("/devices"),
  device: (id: string) => req<Device>(`/devices/${id}`),
  patchDevice: (id: string, body: Partial<Device>) =>
    req<Device>(`/devices/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  syncDevice: (id: string) => req<Media[]>(`/devices/${id}/sync`, { method: "POST" }),
  discoverDevices: () => req<Device[]>("/devices/discover", { method: "POST" }),
  connectDevice: (id: string) => req<Device>(`/devices/${id}/connect`, { method: "POST" }),
  deleteDevice: (id: string) => req<{ ok: true }>(`/devices/${id}`, { method: "DELETE" }),
  media: (deviceId: string) => req<Media[]>(`/devices/${deviceId}/media`),

  createAccount: (body: { device_id: string; platform: Platform; handle: string; story_link?: string }) =>
    req<Account>("/accounts", { method: "POST", body: JSON.stringify(body) }),
  patchAccount: (id: number, body: Partial<Account>) =>
    req<Account>(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteAccount: (id: number) => req<{ ok: true }>(`/accounts/${id}`, { method: "DELETE" }),

  jobs: (params: { start?: string; end?: string; device_id?: string; status?: string }) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v && q.set(k, v));
    return req<Job[]>(`/jobs?${q.toString()}`);
  },
  activity: (limit = 100) => req<Job[]>(`/jobs/activity?limit=${limit}`),
  upsertJobs: (body: JobIn[]) => req<Job[]>("/jobs", { method: "POST", body: JSON.stringify(body) }),
  deleteJob: (id: number) => req<{ ok: true }>(`/jobs/${id}`, { method: "DELETE" }),
  runNow: (id: number) => req<Job>(`/jobs/${id}/run-now`, { method: "POST" }),
  cancelJob: (id: number) => req<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  jobLogs: (id: number) => req<JobLog[]>(`/jobs/${id}/logs`),

  settings: () => req<Record<string, string>>("/settings"),
  putSettings: (body: Record<string, string>) => req<Record<string, string>>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  runner: () => req<{ enabled: boolean; busy_devices: string[]; current: Record<string, number> }>("/runner"),
};

export const thumbUrl = (m: Media | null | undefined) => (m && m.has_thumb ? `/api/media/${m.id}/thumb.jpg` : "");
export const screenUrl = (deviceId: string) => `/api/devices/${deviceId}/screen.png?t=${Date.now()}`;
export const shotUrl = (jobId: number) => `/api/jobs/${jobId}/screenshot.png?t=${Date.now()}`;

// ---- date helpers (local time, no libraries) --------------------------------
export function ymd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
export function parseYmd(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}
export function startOfWeek(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const day = (x.getDay() + 6) % 7; // Monday = 0
  x.setDate(x.getDate() - day);
  return x;
}
export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
export function fmtDuration(s: number): string {
  if (!s) return "";
  const m = Math.floor(s / 60);
  const sec = Math.round(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
