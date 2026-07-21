#!/usr/bin/env bash
# Create .venv and install Elyra editable for development.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer a real 3.12 binary when present; else system python3.
if [[ -x /home/jim/.local/share/mise/installs/python/3.12.8/bin/python3 ]]; then
  PYTHON=/home/jim/.local/share/mise/installs/python/3.12.8/bin/python3
elif [[ -x /usr/bin/python3.12 ]]; then
  PYTHON=/usr/bin/python3.12
elif [[ -x /usr/bin/python3 ]]; then
  PYTHON=/usr/bin/python3
else
  PYTHON="${PYTHON:-python3}"
fi

echo "Using: $PYTHON ($($PYTHON --version 2>&1))"
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

if [[ ! -e model ]]; then
  CANDIDATE="$ROOT/../aurimago/project-elyra2/model"
  if [[ -d "$CANDIDATE" ]]; then
    ln -sfn "$CANDIDATE" model
    echo "Linked model/ -> $CANDIDATE"
  else
    echo "NOTE: model/ not found. Symlink project-elyra2/model when ready:"
    echo "  ln -sfn ../aurimago/project-elyra2/model model"
  fi
fi

echo ""
echo "OK. Activate and start:"
echo "  source .venv/bin/activate"
echo "  elyra start                 # llama + API + UI"
echo "  elyra start --no-llama      # stub LLM + UI only"
echo "  # UI: http://127.0.0.1:8787/"
