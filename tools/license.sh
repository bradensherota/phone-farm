#!/bin/zsh
# Braden's admin for the license service. Needs ~/.phonefarm-admin-token (his Mac only).
#   tools/license.sh list
#   tools/license.sh on <who>        activate for good
#   tools/license.sh off <who>       pause until "on"
#   tools/license.sh limit <who> <n> phones allowed
#   tools/license.sh trial <who> <days>
#   tools/license.sh name <who> "<name>"
#   tools/license.sh delete <mac_id>
# <who> = a person's name, Mac name or id (prefix is enough).
set -euo pipefail
URL="https://phone-farm-license.bradensherota.workers.dev"
TOKEN="$(cat "$HOME/.phonefarm-admin-token" 2>/dev/null || true)"
[ -n "$TOKEN" ] || { echo "no admin token at ~/.phonefarm-admin-token" >&2; exit 1; }
api() { curl -s -H "Authorization: Bearer $TOKEN" -H "content-type: application/json" "$@"; }
set_() { api -X POST "$URL/v1/admin/set" -d "$1" | python3 -c '
import sys, json
r = json.load(sys.stdin)
if "error" in r:
    print("error:", r["error"]); sys.exit(1)
name = r.get("name") or "?"; mac = r.get("mac_name") or ""; msg = r.get("message") or ""
print(name + " · " + mac + " · " + r["state"] + " · " + str(r.get("max_phones")) + " phones" + (" · " + msg if msg else ""))'; }
case "${1:-list}" in
  list)
    api "$URL/v1/admin/list" | python3 -c '
import sys, json, time
d = json.load(sys.stdin); now = d["now"]
def ago(ms):
    if not ms: return "never"
    m = int((now - ms) / 60000)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago" if m < 2880 else f"{m // 1440} d ago"
if not d["macs"]: print("nobody has checked in yet")
for r in d["macs"]:
    state = r["state"] + (f" ({max(0, round((r['trial_until'] - now) / 86400000))} d left)" if r["state"] == "trial" else "")
    print(f"{(r.get('name') or '?'):16} {(r.get('mac_name') or ''):24} {state:18} phones {r.get('phones', 0)}/{r.get('max_phones')}   seen {ago(r.get('last_seen'))}   v{r.get('version', '')}   id {r['mac_id']}")' ;;
  on)     set_ "{\"who\":\"$2\",\"state\":\"active\"}" ;;
  off)    set_ "{\"who\":\"$2\",\"state\":\"off\"}" ;;
  limit)  set_ "{\"who\":\"$2\",\"max_phones\":$3}" ;;
  trial)  set_ "{\"who\":\"$2\",\"trial_days\":$3}" ;;
  name)   set_ "{\"who\":\"$2\",\"name\":\"$3\"}" ;;
  delete) api -X POST "$URL/v1/admin/delete" -d "{\"mac_id\":\"$2\"}"; echo ;;
  *) echo "usage: license.sh list | on <who> | off <who> | limit <who> <n> | trial <who> <days> | name <who> <name> | delete <mac_id>" >&2; exit 2 ;;
esac
