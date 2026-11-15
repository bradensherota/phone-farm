// Phone Farm license service (Cloudflare Worker + KV).
// Every copy of the app checks in with its Mac's hardware id. A new Mac gets a short trial at
// once; after that Braden puts it on a paid plan with an end date (renewed month by month),
// activates it for good, or switches it off and on at will.
// Admin routes need the ADMIN_TOKEN secret, which lives only on Braden's Mac.
// Every check-in answer is signed (Ed25519, SIGNING_KEY secret): the app trusts only the signed
// `payload`, so an edited saved answer or a stand-in service is refused. The app holds the
// public half; the private half lives here and in ~/.phonefarm-signing-key on Braden's Mac.

const TRIAL_DAYS = 3;
const DEFAULT_PHONES = 3;
const DAY = 86400000;

const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });

let signingKey;
async function signed(env, lic) {
  // `payload` is the exact text that was signed; `sig` is the signature over its bytes
  if (!env.SIGNING_KEY) return {};
  try {
    if (!signingKey) {
      const der = Uint8Array.from(atob(env.SIGNING_KEY), (c) => c.charCodeAt(0));
      signingKey = await crypto.subtle.importKey("pkcs8", der, { name: "Ed25519" }, false, ["sign"]);
    }
    const payload = JSON.stringify(lic);
    const sig = await crypto.subtle.sign("Ed25519", signingKey, new TextEncoder().encode(payload));
    return { payload, sig: btoa(String.fromCharCode(...new Uint8Array(sig))) };
  } catch (e) {
    return { sign_error: String(e) };
  }
}

// History: one small KV entry per change (a Mac registering, a new name / version / phone count,
// a trial or plan running out, every admin command). The entry lives in the key's metadata, so
// reading the history is a listing with no further reads. Never allowed to break a check-in.
async function logEvent(env, now, r, event, detail = {}) {
  try {
    const key = `log:${String(now).padStart(13, "0")}:${r.mac_id}:${Math.random().toString(36).slice(2, 6)}`;
    const who = String(r.name || r.mac_name || "").slice(0, 60);
    await env.LICENSES.put(key, "", { metadata: { t: now, mac_id: r.mac_id, who, event, ...detail } });
  } catch {}
}

async function history(env) {
  const out = [];
  let cursor;
  do {
    const l = await env.LICENSES.list({ prefix: "log:", cursor });
    for (const k of l.keys) if (k.metadata) out.push(k.metadata);
    cursor = l.list_complete ? undefined : l.cursor;
  } while (cursor);
  return out.sort((a, b) => a.t - b.t);
}

function effective(r, now) {
  let state = r.state;
  if (state === "trial" && now > r.trial_until) state = "expired";
  if (state === "plan" && now > (r.plan_until || 0)) state = "plan_ended";
  const active = state === "trial" || state === "active" || state === "plan";
  const daysLeft = Math.max(0, Math.ceil((r.trial_until - now) / DAY));
  const planLeft = Math.max(0, Math.ceil(((r.plan_until || 0) - now) / DAY));
  // a paid plan is quiet until its last days
  const message = state === "trial" ? `Trial: ${daysLeft} day${daysLeft === 1 ? "" : "s"} left` :
    state === "plan" ? (planLeft <= 5 ? `Plan: ${planLeft} day${planLeft === 1 ? "" : "s"} left` : "") :
    state === "plan_ended" ? "Your plan has ended. Ask Braden to renew Phone Farm on this Mac." :
    state === "expired" ? "Your trial is over. Ask Braden to activate Phone Farm on this Mac." :
    state === "off" ? "Phone Farm is paused on this Mac by Braden." : "";
  return { ...r, state, active, message };
}

async function allMacs(env, now) {
  const out = [];
  let cursor;
  do {
    const l = await env.LICENSES.list({ prefix: "mac:", cursor });
    for (const k of l.keys) {
      const r = await env.LICENSES.get(k.name, "json");
      if (r) out.push(effective(r, now));
    }
    cursor = l.list_complete ? undefined : l.cursor;
  } while (cursor);
  return out.sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));
}

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const now = Date.now();

    if (req.method === "POST" && url.pathname === "/v1/checkin") {
      let b;
      try { b = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const id = String(b.mac_id || "").slice(0, 64);
      if (!id) return json({ error: "mac_id required" }, 400);
      const key = "mac:" + id;
      let r = await env.LICENSES.get(key, "json");
      const isNew = !r;
      if (!r) r = { mac_id: id, first_seen: now, state: "trial", trial_until: now + TRIAL_DAYS * DAY, max_phones: DEFAULT_PHONES, name: "", mac_name: "" };
      const was = { name: r.name || "", mac_name: r.mac_name || "", phones: r.phones, version: r.version, eff: r.eff };
      r.last_seen = now;
      if (b.user_name) r.name = String(b.user_name).slice(0, 60);
      if (b.mac_name) r.mac_name = String(b.mac_name).slice(0, 60);
      r.phones = Number(b.phones) || 0;
      r.version = String(b.version || "").slice(0, 40);
      const e = effective(r, now);
      r.eff = e.state; // remembered, so the moment a trial or plan runs out is logged once
      await env.LICENSES.put(key, JSON.stringify(r));
      const logs = [];
      if (isNew) logs.push(logEvent(env, now, r, "registered", { name: r.name, mac_name: r.mac_name, version: r.version, phones: r.phones, state: e.state, trial_until: r.trial_until }));
      else {
        if (was.name !== r.name) logs.push(logEvent(env, now, r, "name", { from: was.name, to: r.name }));
        if (was.mac_name !== r.mac_name) logs.push(logEvent(env, now, r, "mac_name", { from: was.mac_name, to: r.mac_name }));
        if (was.version !== r.version) logs.push(logEvent(env, now, r, "version", { from: was.version || "", to: r.version }));
        if (was.phones !== r.phones) logs.push(logEvent(env, now, r, "phones", { from: was.phones ?? null, to: r.phones }));
        if (was.eff && was.eff !== e.state) logs.push(logEvent(env, now, r, "state", { from: was.eff, to: e.state }));
      }
      if (logs.length) ctx.waitUntil(Promise.all(logs));
      const lic = { state: e.state, active: e.active, max_phones: r.max_phones, trial_until: r.trial_until, plan_until: r.plan_until || null, message: e.message, now };
      // the plain fields stay for older copies of the app; newer ones read only the signed payload
      return json({ ...lic, ...(await signed(env, { v: 1, mac_id: id, ...lic })) });
    }

    if (!url.pathname.startsWith("/v1/admin/")) return json({ error: "not found" }, 404);
    if ((req.headers.get("authorization") || "") !== "Bearer " + env.ADMIN_TOKEN) return json({ error: "unauthorized" }, 401);

    if (req.method === "GET" && url.pathname === "/v1/admin/list") return json({ now, macs: await allMacs(env, now) });

    if (req.method === "GET" && url.pathname === "/v1/admin/history") {
      // ?who= a name, Mac name or id (prefix); ?limit= the newest N (default 100)
      const q = (url.searchParams.get("who") || "").toLowerCase().trim();
      let events = await history(env);
      if (q) {
        const macs = await allMacs(env, now);
        const ids = new Set(macs.filter((r) => [r.name, r.mac_name, r.mac_id].some((v) => (v || "").toLowerCase().startsWith(q))).map((r) => r.mac_id));
        events = events.filter((ev) => ids.has(ev.mac_id) || [ev.who, ev.mac_id].some((v) => (v || "").toLowerCase().startsWith(q)));
      }
      const limit = Math.max(1, Math.min(2000, Number(url.searchParams.get("limit")) || 100));
      return json({ now, total: events.length, events: events.slice(-limit) });
    }

    if (req.method === "POST" && url.pathname === "/v1/admin/set") {
      let b;
      try { b = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const q = String(b.who || "").toLowerCase().trim();
      if (!q) return json({ error: "who is required (a name, Mac name or id)" }, 400);
      const macs = await allMacs(env, now);
      const hits = macs.filter((r) => [r.name, r.mac_name, r.mac_id].some((v) => (v || "").toLowerCase().startsWith(q)));
      const ids = new Set(hits.map((h) => h.mac_id));
      if (ids.size === 0) return json({ error: `nobody matches "${b.who}"` }, 404);
      if (ids.size > 1) return json({ error: "ambiguous: " + hits.map((h) => `${h.name || "?"} (${h.mac_name || h.mac_id})`).join(", ") }, 409);
      const r = await env.LICENSES.get("mac:" + hits[0].mac_id, "json");
      const before = effective(r, now);
      const did = {};
      for (const k of ["state", "max_phones", "trial_days", "plan_days", "name"]) if (b[k] != null && b[k] !== "") did[k] = b[k];
      if (b.state && ["active", "off", "trial"].includes(b.state)) r.state = b.state;
      if (b.max_phones != null) r.max_phones = Math.max(0, Number(b.max_phones) || 0);
      if (b.trial_days != null) { r.state = "trial"; r.trial_until = now + Number(b.trial_days) * DAY; }
      if (b.plan_days != null) {
        // a paid plan: runs `plan_days` from now, or from its current end when renewed early
        const base = r.state === "plan" && (r.plan_until || 0) > now ? r.plan_until : now;
        r.state = "plan";
        r.plan_until = base + Number(b.plan_days) * DAY;
      }
      if (b.name) r.name = String(b.name).slice(0, 60);
      const after = effective(r, now);
      r.eff = after.state;
      await env.LICENSES.put("mac:" + r.mac_id, JSON.stringify(r));
      await logEvent(env, now, r, "admin", { ...did, state_from: before.state, state_to: after.state, trial_until: r.trial_until, plan_until: r.plan_until || null, max_phones_now: r.max_phones });
      return json(effective(r, now));
    }
    if (req.method === "POST" && url.pathname === "/v1/admin/delete") {
      let b;
      try { b = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const id = String(b.mac_id || "");
      if (!id) return json({ error: "mac_id required" }, 400);
      const gone = await env.LICENSES.get("mac:" + id, "json");
      await env.LICENSES.delete("mac:" + id);
      if (gone) await logEvent(env, now, gone, "deleted", { name: gone.name || "", mac_name: gone.mac_name || "", state: effective(gone, now).state });
      return json({ deleted: id });
    }
    return json({ error: "not found" }, 404);
  },
};
