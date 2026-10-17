#!/bin/zsh
# One hirescope:// link from the HireScope Launcher app (argument 1). Validated and run by src/jobsearch/launcher.py.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.launcher "$1"
