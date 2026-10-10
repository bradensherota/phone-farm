#!/bin/zsh
# Update this copy from GitHub and reinstall (the Settings → Update button runs this).
set -euo pipefail
cd "$(dirname "$0")"
echo "== Phone Farm update $(date)"
git fetch origin
git reset --hard origin/main
./install.sh
