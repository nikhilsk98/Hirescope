#!/bin/zsh
# Add one job by URL (see src/jobsearch/add_url.py). Extra args are passed through.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m jobsearch.add_url "$@"
