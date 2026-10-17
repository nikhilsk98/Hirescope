#!/bin/zsh
# One-time setup for zero-CLI resumes: the hirescope:// launcher app (~/Applications/HireScope Launcher.app) and the
# background resume worker (launchd: com.nikhil.hirescope.worker). Safe to run again (it replaces both).
# See src/jobsearch/ops/launcher_install.py. Remove with ./scripts/uninstall_launcher.sh
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.ops.launcher_install install "$@"
