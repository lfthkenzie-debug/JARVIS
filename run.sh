#!/bin/bash
# Launch JARVIS with any flags: ./run.sh --ui | --selftest | --oneshot "open safari"
# Talk with JARVIS OFF the browser (Auto-Instant hands-free voice): ./run.sh voice
# Supervised panel that survives a closed terminal and self-heals a crash: ./run.sh ui
cd "$(dirname "$0")"
[[ -f .venv/bin/activate ]] && source .venv/bin/activate
case "$1" in
  voice|v) shift; exec python -m jarvis --voice "$@" ;;
  ui)
    mkdir -p jarvis_data
    while true; do
      python -m jarvis --ui >> jarvis_data/ui.log 2>&1
      echo "[$(date '+%F %T')] UI exited ($?) — restarting in 3s" >> jarvis_data/ui.log
      sleep 3
    done ;;
esac
exec python -m jarvis "$@"
