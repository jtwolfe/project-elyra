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

echo ""
echo "OK. Activate and start:"
echo "  source .venv/bin/activate"
echo "  # Full dogfood extras (isolation + search + browser) — see README § Install:"
echo "  #   pip install -e '.[dev,sandbox,search,browser]'"
echo "  #   playwright install chromium"
echo "  #   ./scripts/setup-microsandbox.sh --doctor-only"
echo "  # Grok (product default): grok login  # or XAI_API_KEY / glass Status"
echo "  elyra start"
echo "  # Hermetic UI / no remote calls:"
echo "  elyra start --stub-llm"
echo "  # UI: http://127.0.0.1:8787/"
