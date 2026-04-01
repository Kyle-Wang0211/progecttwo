#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/venv/gs128271310/bin/python}"
CONFIG_PATH="${CONFIG_PATH:-/root/donor_whitebox/configs/monogs_room3x3_tumlike_official_sp.yaml}"

cd /root/gs_refs/MonoGS
mkdir -p /root/donor_whitebox/logs /root/donor_whitebox/pids
LOG="/root/donor_whitebox/logs/monogs_room3x3_official_sp_$(date +%Y%m%d_%H%M%S).log"
PIDFILE="/root/donor_whitebox/pids/monogs_room3x3_official_sp.pid"

# Previous MonoGS crashes can leak orphan CUDA worker processes with PPID 1.
mapfile -t ORPHAN_PIDS < <(ps -axo pid=,ppid=,cmd= | awk '$2 == 1 && $0 ~ /\/venv\/gs128271310\/bin\/python -c from multiprocessing\.spawn import spawn_main/ {print $1}')
if ((${#ORPHAN_PIDS[@]})); then
  kill "${ORPHAN_PIDS[@]}" 2>/dev/null || true
  sleep 2
fi

export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

"$PYTHON_BIN" - <<'PY'
import cv2
import open3d
import torch
import wandb
import simple_knn
import diff_gaussian_rasterization
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
print("cv2", cv2.__version__)
print("open3d", open3d.__version__)
print("wandb", wandb.__version__)
PY

nohup "$PYTHON_BIN" /root/gs_refs/MonoGS/slam.py --config "$CONFIG_PATH" > "$LOG" 2>&1 &
echo $! | tee "$PIDFILE"
echo "$LOG"
