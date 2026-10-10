#!/bin/zsh
# Double-click me: runs the Phone Farm install/update in a Terminal window.
cd "$(dirname "$0")" || exit 1
./install.sh
echo
read -k1 "?All done. Press any key to close this window. "
