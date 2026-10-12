#!/bin/zsh
# Add one job by URL (see src/jobsearch/add_url.py). Extra args are passed through.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.add_url "$@"
