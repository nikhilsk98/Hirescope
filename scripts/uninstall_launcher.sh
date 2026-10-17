#!/bin/zsh
# Remove the hirescope:// launcher app and the background resume worker (your resumes and data are untouched).
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.ops.launcher_install uninstall "$@"
