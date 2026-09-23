#!/bin/zsh
# Launch Phone Farm. No args = native window. --server = API only (for `npm run dev`).
cd "$(dirname "$0")/backend" && exec .venv/bin/python -m farm.app "$@"
