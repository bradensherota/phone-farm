#!/bin/zsh
# Build (or rebuild) the WebDriverAgent runner for a plugged-in, unlocked iPhone, signed with
# the Apple ID that Xcode is signed into (Xcode → Settings → Accounts). A free Apple ID works:
# its signature lasts 7 days, and the app reruns this by itself when it has expired.
#
#   tools/wda-build.sh <UDID> [TEAM_ID]
#
# Team ID: taken from the Apple Development certificate on this Mac, else from Xcode's accounts,
# else pass it (developer.apple.com → Membership, or Xcode → Settings → Accounts → team).
set -euo pipefail
UDID="${1:?usage: tools/wda-build.sh <UDID> [TEAM_ID]}"
PF="$HOME/PhoneFarm"; SRC="$PF/WebDriverAgent"; BUILD="$PF/wda-build"
mkdir -p "$PF"
if [ ! -d "$SRC/WebDriverAgent.xcodeproj" ]; then
  echo "Fetching WebDriverAgent…"
  git clone --depth 1 https://github.com/appium/WebDriverAgent.git "$SRC"
fi
TEAM="${2:-${PHONEFARM_TEAM_ID:-}}"
if [ -z "$TEAM" ]; then
  TEAM=$(security find-identity -v -p codesigning 2>/dev/null | grep -oE 'Apple Development: [^(]*\([A-Z0-9]{10}\)' | head -1 | grep -oE '[A-Z0-9]{10}\)$' | tr -d ')' || true)
fi
if [ -z "$TEAM" ]; then
  TEAM=$(defaults read com.apple.dt.Xcode IDEProvisioningTeams 2>/dev/null | grep -oE 'teamID = "?[A-Z0-9]{10}' | head -1 | grep -oE '[A-Z0-9]{10}$' || true)
fi
if [ -z "$TEAM" ]; then
  echo "No Apple team found. Open Xcode → Settings → Accounts, sign in with any Apple ID (a free one is fine), then run again." >&2
  exit 2
fi
SLUG=$(id -un | tr -cd '[:alnum:]' | tr '[:upper:]' '[:lower:]')
BUNDLE="com.${SLUG}.WebDriverAgentRunner"
echo "Building the runner for $UDID (team $TEAM, $BUNDLE)… 2-5 minutes"
cd "$SRC"
xcodebuild -project WebDriverAgent.xcodeproj -scheme WebDriverAgentRunner \
  -destination "id=$UDID" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
  -derivedDataPath "$BUILD" \
  DEVELOPMENT_TEAM="$TEAM" PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE" CODE_SIGN_STYLE=Automatic \
  build-for-testing -quiet
APP="$BUILD/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app"
if [ -d "$APP" ]; then
  xcrun devicectl device install app --device "$UDID" "$APP" >/dev/null 2>&1 || true
fi
echo "Runner built and installed for $UDID."
echo "If the phone shows 'Untrusted Developer' when it starts: Settings → General → VPN & Device Management → trust it."
