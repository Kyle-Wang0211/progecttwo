#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:?usage: remote_watch_photoslam_run.sh <out_dir> <run_log> <pid> <watch_log>}"
RUN_LOG="${2:?usage: remote_watch_photoslam_run.sh <out_dir> <run_log> <pid> <watch_log>}"
RUN_PID="${3:?usage: remote_watch_photoslam_run.sh <out_dir> <run_log> <pid> <watch_log>}"
WATCH_LOG="${4:?usage: remote_watch_photoslam_run.sh <out_dir> <run_log> <pid> <watch_log>}"

{
  while true; do
    SHUTDOWN_DIR="$(find "${OUT_DIR}" -maxdepth 1 -type d -name '*_shutdown' | sort | tail -n 1 || true)"
    if [[ -n "${SHUTDOWN_DIR}" ]]; then
      FINAL_PLY="$(find "${SHUTDOWN_DIR}" -type f -name 'point_cloud.ply' | sort | tail -n 1 || true)"
      echo "STATUS:shutdown"
      echo "SHUTDOWN:${SHUTDOWN_DIR}"
      echo "PLY:${FINAL_PLY}"
      if [[ -n "${FINAL_PLY}" && -f "${FINAL_PLY}" ]]; then
        awk '$1=="element" && $2=="vertex" { print "VERTICES:" $3; exit }' "${FINAL_PLY}"
      fi
      for metric in psnr.txt dssim.txt render_time.txt; do
        if [[ -f "${SHUTDOWN_DIR}/${metric}" ]]; then
          echo "==${metric}=="
          tail -n 10 "${SHUTDOWN_DIR}/${metric}"
        fi
      done
      exit 0
    fi

    if ! ps -p "${RUN_PID}" >/dev/null 2>&1; then
      echo "STATUS:process_exited_without_shutdown"
      LAST_ITER="$(grep -o 'Training iteration [0-9]*/100000000' "${RUN_LOG}" | tail -n 1 || true)"
      echo "LAST_ITER:${LAST_ITER}"
      tail -n 40 "${RUN_LOG}" || true
      exit 0
    fi

    sleep 30
  done
} > "${WATCH_LOG}" 2>&1
