#!/bin/zsh
# Zip the project for someone else: code and built UI only, none of your data, phones or accounts.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="$HOME/Desktop/phone-farm-$(date +%Y-%m-%d).zip"
rm -f "$OUT"
# the zip always unpacks to a folder called "phone-farm", whatever the zip is named
STAGE="$(mktemp -d)"; mkdir -p "$STAGE/phone-farm"
rsync -a --exclude "data" --exclude "backend/.venv" --exclude "frontend/node_modules" --exclude ".git" --exclude "sim_media" \
      --exclude "__pycache__" --exclude "*.pyc" --exclude ".DS_Store" --exclude "tools/WebDriverAgent" ./ "$STAGE/phone-farm/"
echo "$(date +%Y-%m-%d) $(git rev-parse --short HEAD 2>/dev/null || echo zip)" > "$STAGE/phone-farm/VERSION"
(cd "$STAGE" && zip -qr "$OUT" phone-farm)
rm -rf "$STAGE"
echo "Ready: $OUT ($(du -h "$OUT" | cut -f1)). Send it; it unpacks to a 'phone-farm' folder; they double-click Install.command (or run ./install.sh)"
