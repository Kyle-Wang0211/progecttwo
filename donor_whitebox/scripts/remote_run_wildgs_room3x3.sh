#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/venv/wildgs128/bin/python}"
CFG_PATH="${CFG_PATH:-/root/donor_whitebox/configs/wildgs_room3x3_dense600.yaml}"
SCENE_NAME="${SCENE_NAME:-room3x3_dense600_whitebox}"
REPO_ROOT="${REPO_ROOT:-/root/gs_refs/WildGS-SLAM}"
cd "${REPO_ROOT}"
WILDGS_BUILD_DIR="$(find "${REPO_ROOT}/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
LIETORCH_BUILD_DIR="$(find "${REPO_ROOT}/thirdparty/lietorch/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
TORCH_LIB_DIR=$("$PYTHON_BIN" - <<'PY'
import torch, pathlib
print(pathlib.Path(torch.__file__).resolve().parent / 'lib')
PY
)
CUDA_LIB_DIRS=""
for cand in \
  /usr/local/cuda-12.9/targets/x86_64-linux/lib \
  /usr/local/cuda-12.9/lib64 \
  /usr/local/cuda/lib64
do
  if [ -d "$cand" ]; then
    CUDA_LIB_DIRS="${CUDA_LIB_DIRS:+${CUDA_LIB_DIRS}:}$cand"
  fi
done
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}${CUDA_LIB_DIRS:+:${CUDA_LIB_DIRS}}:${LD_LIBRARY_PATH:-}"
export PYTHONPATH=/root/wildgs_pydeps:${REPO_ROOT}${WILDGS_BUILD_DIR:+:$WILDGS_BUILD_DIR}${LIETORCH_BUILD_DIR:+:$LIETORCH_BUILD_DIR}:${REPO_ROOT}/thirdparty/lietorch:${REPO_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose:${REPO_ROOT}/thirdparty/simple-knn
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
PYTHON="$PYTHON_BIN"

mkdir -p /root/donor_whitebox/logs /root/donor_whitebox/pids
LOG=/root/donor_whitebox/logs/${SCENE_NAME}_$(date +%Y%m%d_%H%M%S).log
PIDFILE=/root/donor_whitebox/pids/${SCENE_NAME}.pid

"$PYTHON" - <<'PY'
import os
from src import config
from src.utils.mono_priors.metric_depth_estimators import get_metric_depth_estimator
cfg = config.load_config(os.environ['CFG_PATH'])
model = get_metric_depth_estimator(cfg)
print(type(model).__name__)
PY

nohup "$PYTHON" "${REPO_ROOT}/run.py" "$CFG_PATH" > "$LOG" 2>&1 &
echo $! | tee "$PIDFILE"
echo "$LOG"
