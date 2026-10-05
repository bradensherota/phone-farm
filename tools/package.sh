#!/bin/zsh
# Zip the project for someone else: code and built UI only, none of your data, phones or accounts.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="$HOME/Desktop/phone-farm-$(date +%Y-%m-%d).zip"
rm -f "$OUT"
zip -qr "$OUT" . -x "data/*" "backend/.venv/*" "frontend/node_modules/*" ".git/*" "sim_media/*" "*/__pycache__/*" "*.pyc" "*.DS_Store" "tools/WebDriverAgent/*"
echo "Ready: $OUT ($(du -h "$OUT" | cut -f1)). Send it; they unzip it and run ./install.sh"
