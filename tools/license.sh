#!/bin/zsh
# Braden's admin for the license service. Needs ~/.phonefarm-admin-token (his Mac only).
#   tools/license.sh list
#   tools/license.sh on <who>        activate for good
#   tools/license.sh off <who>       pause until "on"
#   tools/license.sh limit <who> <n> phones allowed
#   tools/license.sh trial <who> <days>
#   tools/license.sh plan <who> <days>   paid plan: ends <days> from now (or from its current end if renewed early)
#   tools/license.sh name <who> "<name>"
#   tools/license.sh delete <mac_id>
#   tools/license.sh history [who] [n]   what changed and when (newest last; n = how many, default 100)
# <who> = a person's name, Mac name or id (prefix is enough).
set -euo pipefail
URL="https://phone-farm-license.bradensherota.workers.dev"
TOKEN="$(cat "$HOME/.phonefarm-admin-token" 2>/dev/null || true)"
[ -n "$TOKEN" ] || { echo "no admin token at ~/.phonefarm-admin-token" >&2; exit 1; }
api() { curl -s -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" "$@"; }
LIST_PY=$(cat <<'PY'
import sys, json
d = json.load(sys.stdin); now = d["now"]
def ago(ms):
    if not ms: return "never"
    m = int((now - ms) / 60000)
    return f"{m} min ago" if m < 120 else (f"{m // 60} h ago" if m < 2880 else f"{m // 1440} d ago")
if not d["macs"]: print("nobody has checked in yet")
for r in d["macs"]:
    state = r["state"]
    if state == "trial": state += " (%d d left)" % max(0, round((r["trial_until"] - now) / 86400000))
    if state == "plan": state += " (%d d left)" % max(0, round(((r.get("plan_until") or 0) - now) / 86400000))
    name = r.get("name") or "?"; mac = r.get("mac_name") or ""
    print(f"{name:16} {mac:24} {state:18} phones {r.get('phones', 0)}/{r.get('max_phones')}   seen {ago(r.get('last_seen'))}   v{r.get('version', '')}   id {r['mac_id']}")
PY
)
HIST_PY=$(cat <<'PY'
import sys, json, datetime
d = json.load(sys.stdin)
if "error" in d: print("error:", d["error"]); sys.exit(1)
when = lambda ms: datetime.datetime.fromtimestamp(ms / 1000).strftime("%b %d %Y %-I:%M %p") if ms else "-"
day = lambda ms: datetime.datetime.fromtimestamp(ms / 1000).strftime("%b %-d") if ms else "-"
def tell(e):
    ev = e["event"]
    if ev == "registered": return f"registered ({e.get('state')}, trial ends {day(e.get('trial_until'))}, {e.get('phones', 0)} phones, v{e.get('version', '')})"
    if ev == "version": return f"updated the app: v{e.get('from') or '?'} -> v{e.get('to')}"
    if ev == "phones": return f"phones: {e.get('from')} -> {e.get('to')}"
    if ev == "name": return f"name: {e.get('from') or '?'} -> {e.get('to')}"
    if ev == "mac_name": return f"Mac renamed: {e.get('from') or '?'} -> {e.get('to')}"
    if ev == "state": return f"status: {e.get('from')} -> {e.get('to')} (ran out)"
    if ev == "deleted": return f"removed from the list (was {e.get('state')})"
    if ev == "admin":
        bits = []
        if "plan_days" in e: bits.append(f"plan +{e['plan_days']} days, ends {day(e.get('plan_until'))}")
        if "trial_days" in e: bits.append(f"trial set to {e['trial_days']} days, ends {day(e.get('trial_until'))}")
        if "state" in e: bits.append("switched " + {"active": "on", "off": "off"}.get(e["state"], e["state"]))
        if "max_phones" in e: bits.append(f"phone limit {e['max_phones']}")
        if "name" in e: bits.append(f"named {e['name']}")
        st = f" [{e.get('state_from')} -> {e.get('state_to')}]" if e.get("state_from") != e.get("state_to") else ""
        return "you: " + ", ".join(bits or ["change"]) + st
    return ev
if not d["events"]: print("no history yet (it starts from the day logging was switched on)")
for e in d["events"]:
    print(f"{when(e['t']):22} {(e.get('who') or e['mac_id'][:8]):22} {tell(e)}")
if d.get("total", 0) > len(d["events"]): print(f"... showing the newest {len(d['events'])} of {d['total']}")
PY
)
set_() { api -X POST "$URL/v1/admin/set" -d "$1" | python3 -c '
import sys, json
r = json.load(sys.stdin)
if "error" in r:
    print("error:", r["error"]); sys.exit(1)
name = r.get("name") or "?"; mac = r.get("mac_name") or ""; msg = r.get("message") or ""
if r["state"] == "plan" and r.get("plan_until"):
    import datetime
    msg = "plan ends " + datetime.datetime.fromtimestamp(r["plan_until"] / 1000).strftime("%a %b %-d, %-I:%M %p")
print(name + " · " + mac + " · " + r["state"] + " · " + str(r.get("max_phones")) + " phones" + (" · " + msg if msg else ""))'; }
case "${1:-list}" in
  list)
    api "$URL/v1/admin/list" | python3 -c "$LIST_PY" ;;
  on)     set_ "{\"who\":\"$2\",\"state\":\"active\"}" ;;
  off)    set_ "{\"who\":\"$2\",\"state\":\"off\"}" ;;
  limit)  set_ "{\"who\":\"$2\",\"max_phones\":$3}" ;;
  trial)  set_ "{\"who\":\"$2\",\"trial_days\":$3}" ;;
  plan)   set_ "{\"who\":\"$2\",\"plan_days\":${3:-30}}" ;;
  name)   set_ "{\"who\":\"$2\",\"name\":\"$3\"}" ;;
  delete) api -X POST "$URL/v1/admin/delete" -d "{\"mac_id\":\"$2\"}"; echo ;;
  history) api -G "$URL/v1/admin/history" --data-urlencode "who=${2:-}" --data-urlencode "limit=${3:-100}" | python3 -c "$HIST_PY" ;;
  *) echo "usage: license.sh list | on <who> | off <who> | limit <who> <n> | trial <who> <days> | plan <who> [days] | name <who> <name> | delete <mac_id> | history [who] [n]" >&2; exit 2 ;;
esac
