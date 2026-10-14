#!/bin/zsh
# Add a job found outside HireScope from a JD file / pasted text (see src/jobsearch/add_job.py). Args pass through.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.add_job "$@"
