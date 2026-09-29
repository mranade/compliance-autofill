#!/usr/bin/env bash
# One-time setup for macOS/Linux. Run from VS Code (Terminal > Run Task >
# "Set up project") or from the project folder:  ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || { echo "Python 3.10+ not found."; exit 1; }
echo "==> Using $PY ($("$PY" --version))"

[ -x .venv/bin/python ] || { echo "==> Creating .venv"; "$PY" -m venv .venv; }
echo "==> Installing dependencies"
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt

[ -f .env ] || { cp .env.example .env; echo "    Created .env"; }

echo ""
echo "Setup complete."
grep -Eq '^\s*ANTHROPIC_API_KEY\s*=\s*\S+' .env ||
  echo "    ! Next: open .env and paste your key after ANTHROPIC_API_KEY="
echo "Then press F5 (Run pipeline) in VS Code, or run: .venv/bin/python pipeline.py"
