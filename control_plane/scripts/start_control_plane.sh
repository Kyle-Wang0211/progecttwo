#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/root/control_plane}"
LOG_DIR="${2:-/root/control-plane-logs}"
PORT="${3:-8787}"

mkdir -p "$LOG_DIR"
cd "$ROOT_DIR"

set -a
. ./.env.runtime
set +a

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  ACTIVATE_CMD=". .venv/bin/activate"
else
  PYTHON_BIN="$(command -v python3)"
  ACTIVATE_CMD="true"
fi

pkill -f "uvicorn app.main:app" || true
sleep 1

nohup bash -lc "cd '$ROOT_DIR' && set -a && . ./.env.runtime && set +a && ${ACTIVATE_CMD} && exec '$PYTHON_BIN' -m uvicorn app.main:app --host 0.0.0.0 --port '$PORT'" \
  </dev/null >"$LOG_DIR/uvicorn.log" 2>&1 &

sleep 3

curl -sS -m 3 "http://127.0.0.1:${PORT}/health"
