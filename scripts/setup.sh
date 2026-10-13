#!/bin/zsh
# One-time setup: venv + dependencies + example inputs. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

PY="${PYTHON:-$(command -v python3.12 || true)}"
[[ -z "$PY" ]] && { echo "python3.12 not found (brew install python@3.12)"; exit 1; }

# Runtime data always lives in data.nosync/: iCloud never syncs a folder whose name ends in ".nosync" (a live SQLite DB
# must not be synced). Older installs had data/ (or a data -> data.nosync symlink): scripts/migrate_layout.py moves them.
if [[ -e data && ! -L data ]]; then
  echo "Found an old data/ folder: run  $HOME/.venvs/hirescope/bin/python scripts/migrate_layout.py  after setup"
fi
mkdir -p data.nosync inputs/resume inputs/companies inputs/linkedin inputs/statuses inputs/h1b

# The venv lives outside the repo (default ~/.venvs/hirescope): a venv inside an iCloud-synced folder gets its
# .pth files flagged hidden (Python 3.12 then skips them) and every file synced. Override with JOBSEARCH_VENV.
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
[[ -d "$VENV" ]] || { mkdir -p "$(dirname "$VENV")"; "$PY" -m venv "$VENV"; }
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt
"$VENV/bin/pip" install -q -e .        # `python -m jobsearch.run` works from anywhere with this venv

for f in preferences.yaml resume/master_resume.md companies/my_companies.csv; do
  [[ -f "inputs/$f" ]] || echo "TODO: create inputs/$f (start from inputs/${f%.*}.example.${f##*.})"
done
echo "Setup done. Next: source $VENV/bin/activate && python -m jobsearch.run --verbose   (or ./scripts/run.sh)"
