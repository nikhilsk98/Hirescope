#!/bin/zsh
# launchd schedule: install | uninstall | status | change-time HH:MM,HH:MM   (see src/jobsearch/ops/schedule.py)
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.ops.schedule "$@"
