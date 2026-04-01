#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/donor_whitebox"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
PYTHON_BIN="${HI_SLAM2_PYTHON:-/venv/hislam2/bin/python}"
OUT_DIR="${HI_SLAM2_REPLICA_OUT_DIR:-${REPO}/outputs/replica}"
LOG_PATH="${HI_SLAM2_LOG_PATH:-${ROOT}/logs/hislam2_official_replica_r1.log}"

if [[ ! -d "${REPO}" ]]; then
  echo "[hislam2-replica] missing repo: ${REPO}" >&2
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
EVAL_RECON_LIB="${HI_SLAM2_EVAL_RECON_LIB:-/root/gs_refs/evaluate_3d_reconstruction_lib}"
VENV_BIN_DIR="$(dirname "${PYTHON_BIN}")"

export LD_LIBRARY_PATH="${CUDA_RUNTIME_DIR}:${TORCH_LIB_DIR}:${VENV_LIB_DIR}:${CUDA12_LIB_DIR}:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="${EVAL_RECON_LIB}:${REPO_BUILD_LIB}:${LIETORCH_BUILD_LIB}:${PYTHONPATH:-}"
export PATH="${VENV_BIN_DIR}:${PATH}"

cd "${REPO}"

if [[ ! -d "data/Replica" ]]; then
  echo "[hislam2-replica] downloading Replica"
  bash scripts/download_replica.sh
fi

if [[ ! -d "data/Replica/room0/colors" ]]; then
  echo "[hislam2-replica] preprocessing Replica"
  "${PYTHON_BIN}" scripts/preprocess_replica.py
fi

mkdir -p "${OUT_DIR}"

echo "[hislam2-replica] running official scripts/run_replica.py"
"${PYTHON_BIN}" scripts/run_replica.py | tee "${LOG_PATH}"

echo "[hislam2-replica] done"
