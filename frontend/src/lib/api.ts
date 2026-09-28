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

export interface WakeTestEvent { at: string; step: number; phase: "start" | "wake" | "sleep" | "done" | "cancel"; ok: boolean; detail: string }
export interface WakeTest { device_id: string; status: "none" | "running" | "done" | "cancelled"; steps: number[]; index: number; started_at?: string; next_at: string | null; events: WakeTestEvent[] }

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
  queue_album: string;
  prepare_lead_min: number;
  has_passcode: boolean;
  connected: boolean;
  connecting: boolean;
  error: string;
  busy: boolean;
  current_job_id: number | null;
  accounts: Account[];
  media_count: number;
  today: { total: number; done: number; failed: number; scheduled: number; running: number };
}

export type Stage = "open" | "pick" | "compose" | "post";
export const STAGES: Stage[] = ["open", "pick", "compose", "post"];

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
  group_key: string;
  group_origin_account_id: number | null;
  last_stage: Stage | "";
  failed_stage: Stage | "";
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
  all_accounts?: boolean;
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
  patchDevice: (id: string, body: Partial<Device> & { passcode?: string }) =>
    req<Device>(`/devices/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  syncDevice: (id: string) => req<Media[]>(`/devices/${id}/sync`, { method: "POST" }),
  discoverDevices: () => req<Device[]>("/devices/discover", { method: "POST" }),
  connectDevice: (id: string) => req<Device>(`/devices/${id}/connect`, { method: "POST" }),
  prepareDevice: (id: string, days = 7) => req<{ album: string; added: number; pending: number; message: string }>(`/devices/${id}/prepare?days=${days}`, { method: "POST" }),
  runDay: (id: string, run_date: string) => req<{ started: number }>(`/devices/${id}/run-day?run_date=${run_date}`, { method: "POST" }),
  wakeTest: (id: string) => req<WakeTest>(`/devices/${id}/wake-test`),
  startWakeTest: (id: string, steps: number[]) => req<WakeTest>(`/devices/${id}/wake-test`, { method: "POST", body: JSON.stringify({ steps }) }),
  cancelWakeTest: (id: string) => req<WakeTest>(`/devices/${id}/wake-test`, { method: "DELETE" }),
  wakeDevice: (id: string) => req<{ locked: boolean }>(`/devices/${id}/wake`, { method: "POST" }),
  sleepDevice: (id: string) => req<{ locked: boolean; error?: string }>(`/devices/${id}/sleep`, { method: "POST" }),
  clearQueue: (id: string) => req<{ cleared: boolean }>(`/devices/${id}/queue/clear`, { method: "POST" }),
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
  runStage: (id: number, stage: Stage, mode: "only" | "from") => req<Job>(`/jobs/${id}/run-stage?stage=${stage}&mode=${mode}`, { method: "POST" }),
  cancelJob: (id: number) => req<Job>(`/jobs/${id}/cancel`, { method: "POST" }),
  jobLogs: (id: number) => req<JobLog[]>(`/jobs/${id}/logs`),

  settings: () => req<Record<string, string>>("/settings"),
  putSettings: (body: Record<string, string>) => req<Record<string, string>>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  runner: () => req<{ enabled: boolean; busy_devices: string[]; current: Record<string, number> }>("/runner"),
};

export interface DayStart { device_id: string; run_date: string; start: string }
export const scheduleApi = {
  get: (start: string, end: string) => req<DayStart[]>(`/schedule?start=${start}&end=${end}`),
  put: (device_id: string, run_date: string, start: string) => req<{ ok: true }>("/schedule", { method: "PUT", body: JSON.stringify({ device_id, run_date, start }) }),
};
/** 15-minute slots as HH:MM with 12-hour labels, e.g. ["07:00", "7:00 AM"] */
export const TIME_SLOTS: [string, string][] = Array.from({ length: 96 }, (_, i) => {
  const h = Math.floor(i / 4), m = (i % 4) * 15;
  const hh = String(h).padStart(2, "0"), mm = String(m).padStart(2, "0");
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return [`${hh}:${mm}`, `${h12}:${mm} ${h < 12 ? "AM" : "PM"}`];
});
export const fmtHHMM = (hhmm: string) => { const [h, m] = hhmm.split(":").map(Number); const h12 = h % 12 === 0 ? 12 : h % 12; return `${h12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };

export const thumbUrl = (m: Media | null | undefined) => (m ? `/api/media/${m.id}/thumb.jpg` : "");  // made on demand server-side
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
