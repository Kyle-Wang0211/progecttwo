#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"
REPO="${REPO:-/root/gs_refs/WildGS-SLAM.clean}"
CONFIG_PATH="${CONFIG_PATH:-/root/donor_whitebox/configs/wildgs_official_tum_fr3_office_clean.yaml}"
LOG_DIR="${LOG_DIR:-/root/donor_whitebox/logs}"

if [[ $# -ge 1 ]]; then
  CONFIG_PATH="$1"
fi

mkdir -p "$LOG_DIR" /root/donor_whitebox/pids

cd "$REPO"

LOG="$LOG_DIR/wildgs_official_tum_fr3_$(date +%Y%m%d_%H%M%S).log"
PIDFILE="/root/donor_whitebox/pids/wildgs_official_tum_fr3.pid"

export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

nohup "$PYTHON_BIN" run.py "$CONFIG_PATH" > "$LOG" 2>&1 &
echo $! | tee "$PIDFILE"
echo "$LOG"
