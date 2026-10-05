#!/bin/zsh
# Stops the always-on server and keep-awake. Files and data are left in place.
for p in "$HOME/Library/LaunchAgents"/*phonefarm*.plist(N); do
  launchctl bootout "gui/$(id -u)" "$p" >/dev/null 2>&1 || true
  rm -f "$p"
done
echo "Phone Farm server stopped and removed from login. Delete this folder to remove it fully."
