#!/bin/zsh
# Run the pipeline once. All arguments are passed through to `python -m jobsearch.run`.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec .venv/bin/python -m jobsearch.run "$@"
