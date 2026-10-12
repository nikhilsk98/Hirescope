#!/bin/zsh
# One-time setup: venv + dependencies + example inputs. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

PY="${PYTHON:-$(command -v python3.12 || true)}"
[[ -z "$PY" ]] && { echo "python3.12 not found (brew install python@3.12)"; exit 1; }

# If the repo lives in an iCloud Drive-synced folder (e.g. Desktop & Documents sync), keep runtime data out
# of iCloud: iCloud never syncs a folder whose name ends in ".nosync". A live SQLite DB must not be synced.
in_icloud() { local d="$PWD"; while [[ "$d" != "/" ]]; do xattr -p com.apple.file-provider-domain-id "$d" 2>/dev/null \
  | grep -q CloudDocs && return 0; d="$(dirname "$d")"; done; return 1; }
if in_icloud && [[ ! -L data ]]; then
  echo "Repo is in an iCloud-synced folder: storing runtime data in data.nosync/ (not synced)"
  if [[ -d data ]]; then mv data data.nosync; else mkdir -p data.nosync; fi
  ln -s data.nosync data
fi

# The venv lives outside the repo (default ~/.venvs/hirescope): a venv inside an iCloud-synced folder gets its
# .pth files flagged hidden (Python 3.12 then skips them) and every file synced. Override with JOBSEARCH_VENV.
VENV="${JOBSEARCH_VENV:-$HOME/.venvs/hirescope}"
[[ -d "$VENV" ]] || { mkdir -p "$(dirname "$VENV")"; "$PY" -m venv "$VENV"; }
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt
"$VENV/bin/pip" install -q -e .        # `python -m jobsearch.run` works from anywhere with this venv

for f in preferences.yaml master_resume.md; do
  [[ -f "inputs/$f" ]] || echo "TODO: create inputs/$f (start from inputs/${f%.*}.example.${f##*.})"
done
echo "Setup done. Next: source $VENV/bin/activate && python -m jobsearch.run --verbose   (or ./scripts/run.sh)"
