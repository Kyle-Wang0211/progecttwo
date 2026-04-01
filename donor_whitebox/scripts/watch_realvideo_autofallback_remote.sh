#!/usr/bin/env bash
set -euo pipefail

HOST="${1:?host}"
PORT="${2:?port}"
RUN_NAME="${3:?run_name}"
RUNNER_PID="${4:-}"
INTERVAL_SEC="${INTERVAL_SEC:-90}"
LOG_PATH="${LOG_PATH:-/Users/kaidongwang/Documents/progecttwo/donor_whitebox/logs/${RUN_NAME}_watch.log}"

mkdir -p "$(dirname "${LOG_PATH}")"

remote() {
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -p "${PORT}" "root@${HOST}" "$@"
}

log() {
  printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "${LOG_PATH}"
}

log "watcher start host=${HOST} port=${PORT} run=${RUN_NAME} runner_pid=${RUNNER_PID:-unknown}"

while true; do
  if ! remote "echo ok" >/dev/null 2>&1; then
    log "remote_unreachable"
    sleep "${INTERVAL_SEC}"
    continue
  fi

  BASE_DIR="/root/donor_whitebox/outputs/hislam2_${RUN_NAME}"
  SUMMARY_DIR="${BASE_DIR}/summaries"
  MAIN_LOG="/root/donor_whitebox/logs/${RUN_NAME}.log"

  status_line="$(remote "python3 - <<'PY'
from pathlib import Path
base = Path('${BASE_DIR}')
summary = base / 'summaries'
success = (summary / 'SUCCESS.json').is_file()
failures = sorted(p.name for p in summary.glob('*_failure.json')) if summary.is_dir() else []
print(f'success={int(success)} failures={len(failures)} latest_failure={(failures[-1] if failures else \"none\")}')
PY" 2>/dev/null || true)"
  log "status ${status_line}"

  if [[ -n "${RUNNER_PID}" ]]; then
    runner_state="$(remote "ps -p ${RUNNER_PID} -o pid=,etime=,%cpu=,%mem=,cmd=" 2>/dev/null || true)"
    if [[ -n "${runner_state}" ]]; then
      log "runner ${runner_state}"
    else
      log "runner_missing"
    fi
  fi

  latest_log="$(remote "tail -n 20 '${MAIN_LOG}' 2>/dev/null" || true)"
  if [[ -n "${latest_log}" ]]; then
    while IFS= read -r line; do
      [[ -n "${line}" ]] && log "log ${line}"
    done <<< "${latest_log}"
  fi

  if remote "test -f '${SUMMARY_DIR}/SUCCESS.json'" >/dev/null 2>&1; then
    log "terminal_success"
    exit 0
  fi

  if ! remote "test -d '${BASE_DIR}'" >/dev/null 2>&1; then
    log "base_dir_missing"
    exit 1
  fi

  if [[ -n "${RUNNER_PID}" ]] && ! remote "ps -p ${RUNNER_PID} >/dev/null 2>&1" >/dev/null 2>&1; then
    if remote "find '${SUMMARY_DIR}' -maxdepth 1 -name '*_failure.json' | grep -q ." >/dev/null 2>&1; then
      if remote "grep -q '\\[autofallback\\] all tiers failed' '${MAIN_LOG}'" >/dev/null 2>&1; then
        log "terminal_all_tiers_failed"
        exit 2
      fi
    fi
    log "runner_stopped_without_terminal_marker"
    exit 3
  fi

  sleep "${INTERVAL_SEC}"
done
