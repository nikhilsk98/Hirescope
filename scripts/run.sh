#!/bin/zsh
# Run the pipeline once. All arguments are passed through to `python -m jobsearch.run`.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"   # kept outside the (iCloud-synced) repo
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.run "$@"
