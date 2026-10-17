#!/bin/zsh
# Terminal fallback for the Resume Builder tab: import one saved external job and queue its resume.
#   ./scripts/external_resume.sh "hirescope-external-job-Acme-20261017T120000.json"   (in ~/Downloads or inputs/external_jobs/)
# If the file isn't found, the newest pending external job is used (and the output says so). See src/jobsearch/external_resume.py.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python" -m jobsearch.external_resume "$@"
