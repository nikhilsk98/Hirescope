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

[[ -d .venv ]] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# macOS may flag .pth files as hidden, and Python 3.12 skips hidden .pth files, so an
# editable install is unreliable. Put src/ on PYTHONPATH from the venv's activate script instead.
if ! grep -q "jobsearch PYTHONPATH" .venv/bin/activate; then
  printf '\n# jobsearch PYTHONPATH\nexport PYTHONPATH="%s/src${PYTHONPATH:+:$PYTHONPATH}"\n' "$ROOT" >> .venv/bin/activate
fi

for f in preferences.yaml master_resume.md; do
  [[ -f "inputs/$f" ]] || echo "TODO: create inputs/$f (start from inputs/${f%.*}.example.${f##*.})"
done
echo "Setup done. Next: source .venv/bin/activate && python -m jobsearch.run --verbose"
