#!/bin/zsh
# Import the dashboard's saved files now (Export statuses + "Add external job"); see src/jobsearch/import_pending.py.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.import_pending "$@"
