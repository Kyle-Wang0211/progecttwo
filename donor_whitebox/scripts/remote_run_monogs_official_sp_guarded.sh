#!/usr/bin/env bash
set -euo pipefail

RMSE_STOP_THRESHOLD_M="${RMSE_STOP_THRESHOLD_M:-0.03}"

RUN_OUTPUT="$(CONFIG_PATH="${CONFIG_PATH:-}" PYTHON_BIN="${PYTHON_BIN:-}" bash /root/donor_whitebox/scripts/remote_run_monogs_official_sp.sh)"
echo "$RUN_OUTPUT"

PID="$(printf '%s\n' "$RUN_OUTPUT" | tail -n 2 | head -n 1)"
LOG="$(printf '%s\n' "$RUN_OUTPUT" | tail -n 1)"
GUARD_LOG="/root/donor_whitebox/logs/monogs_rmse_guard_$(date +%Y%m%d_%H%M%S).log"

nohup bash -lc '
set -euo pipefail
threshold="$1"
pid="$2"
log="$3"
last_line=0

while kill -0 "$pid" 2>/dev/null; do
  if [[ -f "$log" ]]; then
    line_count=$(wc -l < "$log")
    if (( line_count > last_line )); then
      while IFS= read -r line; do
        if [[ "$line" =~ Eval:\ RMSE\ ATE\ \[m\]\ ([0-9eE+.-]+) ]]; then
          rmse="${BASH_REMATCH[1]}"
          if awk -v a="$rmse" -v b="$threshold" '"'"'BEGIN{exit !(a>b)}'"'"'; then
            echo "RMSE_GUARD_STOP pid=$pid rmse=$rmse threshold=$threshold"
            kill "$pid" 2>/dev/null || true
            exit 0
          fi
        fi
      done < <(sed -n "$((last_line + 1)),${line_count}p" "$log")
      last_line=$line_count
    fi
  fi
  sleep 5
done
echo "RMSE_GUARD_EXIT pid=$pid"
' _ "$RMSE_STOP_THRESHOLD_M" "$PID" "$LOG" > "$GUARD_LOG" 2>&1 &

echo "GUARD_PID=$!"
echo "GUARD_LOG=$GUARD_LOG"
