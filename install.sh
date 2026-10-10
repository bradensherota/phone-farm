#!/bin/zsh
# Phone Farm: one-time setup on a Mac. Run it from this folder:   ./install.sh
# Safe to run again after an update (it rebuilds the UI and restarts the server; your data stays).
set -euo pipefail
cd "$(dirname "$0")"; ROOT="$(pwd)"
echo "== Phone Farm setup in $ROOT"

# 1. Xcode (the runner on the phone is built and launched with it)
if ! xcode-select -p >/dev/null 2>&1 || [ ! -d /Applications/Xcode.app ]; then
  echo "Install Xcode from the App Store, open it once, then run this again."; exit 1
fi
sudo -n true 2>/dev/null || true
if ! xcrun xcodebuild -version >/dev/null 2>&1; then
  echo "Pointing the command line at Xcode (asks for your Mac password)…"
  sudo xcode-select -s /Applications/Xcode.app
fi

# 2. Tools: Homebrew, ffmpeg (thumbnails), node (UI build), uv (Python)
if ! command -v brew >/dev/null 2>&1; then echo "Install Homebrew first: https://brew.sh — then run this again."; exit 1; fi
for t in ffmpeg node; do command -v $t >/dev/null 2>&1 || brew install $t; done
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# 3. Server and UI
(cd backend && uv sync --extra ios)
if command -v npm >/dev/null 2>&1; then (cd frontend && npm install --silent && npm run build --silent); fi
[ -d frontend/dist ] || { echo "UI build missing (frontend/dist). Install node and run again."; exit 1; }

# 4. OCR helper for TikTok (Apple Vision)
mkdir -p "$HOME/PhoneFarm" data/logs
swiftc -O -o "$HOME/PhoneFarm/ocr" backend/tools/ocr.swift

# 5. Updating from a previous copy of the app in another folder? Bring its data along
#    (phones, accounts, plans, thumbnails), so an update never starts from scratch.
AGENTS="$HOME/Library/LaunchAgents"; mkdir -p "$AGENTS"
REAL_PHONES=0
if [ -f "$ROOT/data/farm.db" ]; then
  REAL_PHONES="$(sqlite3 "$ROOT/data/farm.db" "select count(*) from devices where kind!='sim'" 2>/dev/null || echo 0)"
fi
if [ -f "$AGENTS/com.phonefarm.server.plist" ] && { [ ! -f "$ROOT/data/farm.db" ] || [ "${REAL_PHONES:-0}" = "0" ]; }; then
  OLD_BACKEND="$(plutil -extract WorkingDirectory raw -o - "$AGENTS/com.phonefarm.server.plist" 2>/dev/null || true)"
  OLD_ROOT="$(dirname "$OLD_BACKEND" 2>/dev/null || true)"
  if [ -n "$OLD_ROOT" ] && [ "$OLD_ROOT" != "$ROOT" ] && [ -f "$OLD_ROOT/data/farm.db" ]; then
    echo "Moving your data from the previous copy ($OLD_ROOT/data) into this one…"
    launchctl bootout "gui/$(id -u)" "$AGENTS/com.phonefarm.server.plist" >/dev/null 2>&1 || true
    [ -d "$ROOT/data" ] && mv "$ROOT/data" "$ROOT/data.fresh-$(date +%H%M%S)"
    mv "$OLD_ROOT/data" "$ROOT/data"
    echo "Done. The old folder ($OLD_ROOT) can be deleted."
  fi
fi

# 6. Always-on server + keep the Mac awake (launchd, this user only)
TZ_NAME="$(readlink /etc/localtime | sed 's|.*/zoneinfo/||')"
for old in "$AGENTS"/*phonefarm*.plist(N); do
  launchctl bootout "gui/$(id -u)" "$old" >/dev/null 2>&1 || true
done
cat > "$AGENTS/com.phonefarm.server.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.phonefarm.server</string>
  <key>ProgramArguments</key><array><string>$ROOT/backend/.venv/bin/python</string><string>-u</string><string>serve.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT/backend</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>$HOME</string>
    <key>PATH</key><string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>TZ</key><string>$TZ_NAME</string>
  </dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$ROOT/data/logs/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/data/logs/server.log</string>
</dict></plist>
PLIST
cat > "$AGENTS/com.phonefarm.awake.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.phonefarm.awake</string>
  <key>ProgramArguments</key><array><string>/usr/bin/caffeinate</string><string>-dims</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
PLIST
launchctl bootstrap "gui/$(id -u)" "$AGENTS/com.phonefarm.server.plist"
launchctl bootstrap "gui/$(id -u)" "$AGENTS/com.phonefarm.awake.plist"
for i in {1..30}; do curl -s -m 2 -o /dev/null http://localhost:8765/api/settings && break; sleep 2; done

cat <<MSG

== Done. Phone Farm is running at  http://localhost:8765  (it starts with your Mac from now on)

Next:
 1. In Chrome open http://localhost:8765, then menu ⋮ → Cast, save and share → Install page as app.
    That gives you a Phone Farm app in the Dock.
 2. Xcode → Settings → Accounts: sign in with your Apple ID (a free one is fine).
 3. On each iPhone: Settings → Privacy & Security → Developer Mode on; Auto-Lock Never;
    plug it in over USB and tap Trust. Press "Find phones" in the app.
    The first connect builds the runner for that phone (2-5 min). If the phone then shows
    "Untrusted Developer": Settings → General → VPN & Device Management → trust it, press Connect.
 4. Open the phone's page: add your accounts (handle, platform, story link).
 5. In Photos on the phone make an album called "Farm Queue" and put the clips to post in it.
    Then plan the week in the planner and press Run day.
MSG
