#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/donor_whitebox"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
PYTHON_BIN="${HI_SLAM2_PYTHON:-/venv/hislam2/bin/python}"
OUT_DIR="${HI_SLAM2_OUT_DIR:-${REPO}/outputs/room0}"
LOG_PATH="${HI_SLAM2_LOG_PATH:-${ROOT}/logs/hislam2_official_room0_r1.log}"

if [[ ! -d "${REPO}" ]]; then
  echo "[hislam2-room0] missing repo: ${REPO}" >&2
  exit 2
fi

mkdir -p "${ROOT}/logs"

TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib
import torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
VENV_LIB_DIR="$(cd "$(dirname "${PYTHON_BIN}")/.." && pwd)/lib"
CUDA_RUNTIME_DIR="$(cd "$(dirname "${PYTHON_BIN}")/.." && pwd)/lib/python3.10/site-packages/nvidia/cuda_runtime/lib"
CUDA12_LIB_DIR="/usr/local/cuda-12.9/targets/x86_64-linux/lib"
REPO_BUILD_LIB="${REPO}/build/lib.linux-x86_64-cpython-310"
LIETORCH_BUILD_LIB="${REPO}/thirdparty/lietorch/build/lib.linux-x86_64-cpython-310"

export LD_LIBRARY_PATH="${CUDA_RUNTIME_DIR}:${TORCH_LIB_DIR}:${VENV_LIB_DIR}:${CUDA12_LIB_DIR}:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${REPO_BUILD_LIB}:${LIETORCH_BUILD_LIB}:${PYTHONPATH:-}"

cd "${REPO}"

if [[ ! -d "data/Replica" ]]; then
  echo "[hislam2-room0] downloading Replica"
  bash scripts/download_replica.sh
fi

if [[ ! -d "data/Replica/room0/colors" ]]; then
  echo "[hislam2-room0] preprocessing Replica"
  "${PYTHON_BIN}" scripts/preprocess_replica.py
fi

mkdir -p "${OUT_DIR}"

echo "[hislam2-room0] running demo"
"${PYTHON_BIN}" demo.py \
  --imagedir data/Replica/room0/colors \
  --calib calib/replica.txt \
  --config config/replica_config.yaml \
  --output "${OUT_DIR}"

echo "[hislam2-room0] running tsdf integration"
"${PYTHON_BIN}" tsdf_integrate.py --result "${OUT_DIR}" --voxel_size 0.01 --weight 2

echo "[hislam2-room0] done"
