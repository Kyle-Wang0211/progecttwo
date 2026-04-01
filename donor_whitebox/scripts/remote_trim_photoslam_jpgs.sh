#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:?usage: remote_trim_photoslam_jpgs.sh <out_dir> <pid> <trim_log>}"
RUN_PID="${2:?usage: remote_trim_photoslam_jpgs.sh <out_dir> <pid> <trim_log>}"
TRIM_LOG="${3:?usage: remote_trim_photoslam_jpgs.sh <out_dir> <pid> <trim_log>}"

{
  while ps -p "${RUN_PID}" >/dev/null 2>&1; do
    COUNT_BEFORE="$(find "${OUT_DIR}" -maxdepth 1 -type f -name '*.jpg' | wc -l || true)"
    if [[ "${COUNT_BEFORE}" != "0" ]]; then
      find "${OUT_DIR}" -maxdepth 1 -type f -name '*.jpg' -delete
      COUNT_AFTER="$(find "${OUT_DIR}" -maxdepth 1 -type f -name '*.jpg' | wc -l || true)"
      echo "trimmed_jpgs before=${COUNT_BEFORE} after=${COUNT_AFTER} ts=$(date +%Y-%m-%dT%H:%M:%S)"
    fi
    sleep 30
  done
  echo "run_pid_exited ts=$(date +%Y-%m-%dT%H:%M:%S)"
} > "${TRIM_LOG}" 2>&1
