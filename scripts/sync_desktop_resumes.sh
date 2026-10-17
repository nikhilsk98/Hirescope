#!/bin/zsh
# Copy every READY resume PDF to the Desktop folder (config/resume_builder.yaml → desktop_copy); see
# src/resume_builder/desktop.py. --force copies even when desktop_copy.enabled is false.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m resume_builder sync-desktop "$@"
