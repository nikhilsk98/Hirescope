#!/bin/zsh
# The background resume queue worker (launchd: com.nikhil.hirescope.worker). Runs queued builds, exits when empty.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m resume_builder queue worker
