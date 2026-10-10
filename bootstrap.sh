#!/bin/zsh
# One-line install:  curl -fsSL https://raw.githubusercontent.com/bradensherota/phone-farm/main/bootstrap.sh | zsh
# Needs Xcode (App Store, opened once) and Homebrew. Puts the app in ~/phone-farm and installs it.
set -euo pipefail
DEST="$HOME/phone-farm"
if ! xcode-select -p >/dev/null 2>&1 || [ ! -d /Applications/Xcode.app ]; then
  echo "Install Xcode from the App Store, open it once, then run this again."; exit 1
fi
command -v brew >/dev/null 2>&1 || { echo "Install Homebrew first: https://brew.sh — then run this again."; exit 1; }
command -v git >/dev/null 2>&1 || brew install git
if [ -d "$DEST/.git" ]; then
  echo "Updating the copy in $DEST…"; git -C "$DEST" fetch origin && git -C "$DEST" reset --hard origin/main
else
  echo "Downloading Phone Farm into $DEST…"; git clone https://github.com/bradensherota/phone-farm.git "$DEST"
fi
cd "$DEST" && exec ./install.sh </dev/tty
