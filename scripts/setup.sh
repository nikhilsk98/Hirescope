#!/bin/zsh
# One-time setup: venv + dependencies + example inputs. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"

PY="${PYTHON:-$(command -v python3.12 || true)}"
[[ -z "$PY" ]] && { echo "python3.12 not found (brew install python@3.12)"; exit 1; }

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
