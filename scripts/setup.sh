#!/usr/bin/env bash
# One-time setup (macOS / Linux).   ./scripts/setup.sh [--demo] [--ai]
#   --demo  also create sample accounts (DEVELOPMENT ONLY); one-time passwords go to data/demo-accounts.txt
#   --ai    also install the optional neural-embedding + Whisper packages (large downloads)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
DEMO=0; AI=0; for a in "$@"; do [ "$a" = "--demo" ] && DEMO=1; [ "$a" = "--ai" ] && AI=1; done
command -v python3 >/dev/null || { echo "Python 3.10+ is required"; exit 1; }
command -v node >/dev/null || { echo "Node.js 20+ is required"; exit 1; }
echo "== Backend"; [ -d backend/.venv ] || python3 -m venv backend/.venv
PY="$ROOT/backend/.venv/bin/python"
"$PY" -m pip install -q --upgrade pip && "$PY" -m pip install -q -r backend/requirements-dev.txt
[ "$AI" = 1 ] && "$PY" -m pip install -q -r backend/requirements-optional.txt
"$PY" scripts/envset.py init
( cd backend && "$PY" -m app.cli init-db )
echo "== Front-end"; ( cd frontend && npm install --no-audit --no-fund --loglevel=error && npm run build >/dev/null && echo "built frontend/dist" )
echo "== Blockchain tooling"; ( cd blockchain && npm install --no-audit --no-fund --loglevel=error )
if [ "$DEMO" = 1 ]; then
  ( cd backend && "$PY" -m app.cli demo-data ) | tee data/demo-accounts.txt
  chmod 600 data/demo-accounts.txt 2>/dev/null || true
  echo "Sample accounts saved to data/demo-accounts.txt (git-ignored)."
else
  echo; echo "Create your administrator:  cd backend && .venv/bin/python -m app.cli create-admin --email you@example.edu --name \"Your Name\""
fi
echo; echo "Setup complete. Start everything with:  ./scripts/start.sh"
