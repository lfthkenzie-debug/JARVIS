#!/bin/bash
# Launch JARVIS with any flags: ./run.sh --text | --selftest | --oneshot "open safari"
cd "$(dirname "$0")"
[[ -f .venv/bin/activate ]] && source .venv/bin/activate
exec python -m jarvis "$@"
