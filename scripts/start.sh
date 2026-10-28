#!/usr/bin/env bash
# Start the platform (macOS / Linux).   ./scripts/start.sh [--no-chain] [--port 8000]
# Runs: local Hardhat chain (auto-deploys the audit contract) + API serving the built UI on ONE port.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
CHAIN=1; PORT=8000
while [ $# -gt 0 ]; do case "$1" in --no-chain) CHAIN=0;; --port) PORT="$2"; shift;; esac; shift; done
PY="$ROOT/backend/.venv/bin/python"; [ -x "$PY" ] || { echo "Run ./scripts/setup.sh first"; exit 1; }
[ -f frontend/dist/index.html ] || ( cd frontend && npm run build >/dev/null )
mkdir -p data/logs; PIDS=()
cleanup() { echo; echo "Stopping..."; for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done; }
trap cleanup EXIT INT TERM
if [ "$CHAIN" = 1 ]; then
  echo "Starting local blockchain (Hardhat)..."
  ( cd blockchain && exec npx hardhat node ) > data/logs/hardhat.log 2>&1 & PIDS+=($!)
  for i in $(seq 1 60); do curl -s -o /dev/null -X POST -H 'content-type: application/json' --data '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}' http://127.0.0.1:8545 && break; sleep 1; done
  # (re)deploy when the address is unset or the fresh local chain has no contract there
  ADDR="$("$PY" scripts/envset.py get CONTRACT_ADDRESS)"
  HAS=$(curl -s -X POST -H 'content-type: application/json' --data "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getCode\",\"params\":[\"${ADDR:-0x0000000000000000000000000000000000000000}\",\"latest\"],\"id\":1}" http://127.0.0.1:8545 | grep -o '"result":"0x[0-9a-f]*"' | head -1)
  if [ -z "$ADDR" ] || [ "$HAS" = '"result":"0x"' ] || [ -z "$HAS" ]; then
    NEW="$(cd backend && "$PY" -m app.cli deploy-contract | sed -n 's/^CONTRACT_ADDRESS=//p')"
    [ -n "$NEW" ] && "$PY" scripts/envset.py set CONTRACT_ADDRESS "$NEW" && echo "Audit contract deployed at $NEW"
  fi
else
  export BLOCKCHAIN_ENABLED=false     # this run only; .env is untouched
fi
echo "Starting the platform on http://localhost:$PORT  (Ctrl+C to stop)"
cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" 2>&1 | tee ../data/logs/api.log
