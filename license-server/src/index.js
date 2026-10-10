// Phone Farm license service (Cloudflare Worker + KV).
// Every copy of the app checks in with its Mac's hardware id. A new Mac gets a 7-day trial at
// once; after that Braden activates it (for good), or switches it off and on at will.
// Admin routes need the ADMIN_TOKEN secret, which lives only on Braden's Mac.

const TRIAL_DAYS = 7;
const DEFAULT_PHONES = 3;
const DAY = 86400000;

const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });

function effective(r, now) {
  let state = r.state;
  if (state === "trial" && now > r.trial_until) state = "expired";
  const active = state === "trial" || state === "active";
  const daysLeft = Math.max(0, Math.ceil((r.trial_until - now) / DAY));
  const message = state === "trial" ? `Trial: ${daysLeft} day${daysLeft === 1 ? "" : "s"} left` :
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
  async fetch(req, env) {
    const url = new URL(req.url);
    const now = Date.now();

    if (req.method === "POST" && url.pathname === "/v1/checkin") {
      let b;
      try { b = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const id = String(b.mac_id || "").slice(0, 64);
      if (!id) return json({ error: "mac_id required" }, 400);
      const key = "mac:" + id;
      let r = await env.LICENSES.get(key, "json");
      if (!r) r = { mac_id: id, first_seen: now, state: "trial", trial_until: now + TRIAL_DAYS * DAY, max_phones: DEFAULT_PHONES, name: "", mac_name: "" };
      r.last_seen = now;
      if (b.user_name) r.name = String(b.user_name).slice(0, 60);
      if (b.mac_name) r.mac_name = String(b.mac_name).slice(0, 60);
      r.phones = Number(b.phones) || 0;
      r.version = String(b.version || "").slice(0, 40);
      await env.LICENSES.put(key, JSON.stringify(r));
      const e = effective(r, now);
      return json({ state: e.state, active: e.active, max_phones: r.max_phones, trial_until: r.trial_until, message: e.message, now });
    }

    if (!url.pathname.startsWith("/v1/admin/")) return json({ error: "not found" }, 404);
    if ((req.headers.get("authorization") || "") !== "Bearer " + env.ADMIN_TOKEN) return json({ error: "unauthorized" }, 401);

    if (req.method === "GET" && url.pathname === "/v1/admin/list") return json({ now, macs: await allMacs(env, now) });

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
      if (b.state && ["active", "off", "trial"].includes(b.state)) r.state = b.state;
      if (b.max_phones != null) r.max_phones = Math.max(0, Number(b.max_phones) || 0);
      if (b.trial_days != null) { r.state = "trial"; r.trial_until = now + Number(b.trial_days) * DAY; }
      if (b.name) r.name = String(b.name).slice(0, 60);
      await env.LICENSES.put("mac:" + r.mac_id, JSON.stringify(r));
      return json(effective(r, now));
    }
    if (req.method === "POST" && url.pathname === "/v1/admin/delete") {
      let b;
      try { b = await req.json(); } catch { return json({ error: "bad json" }, 400); }
      const id = String(b.mac_id || "");
      if (!id) return json({ error: "mac_id required" }, 400);
      await env.LICENSES.delete("mac:" + id);
      return json({ deleted: id });
    }
    return json({ error: "not found" }, 404);
  },
};
