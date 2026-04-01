#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/donor_whitebox"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
PYTHON_BIN="${HI_SLAM2_PYTHON:-/venv/hislam2/bin/python}"
SEQ_DIR="${HI_SLAM2_SEQ_DIR:?set HI_SLAM2_SEQ_DIR}"
CALIB_PATH="${HI_SLAM2_CALIB:-${SEQ_DIR}/calib.txt}"
CONFIG_PATH="${HI_SLAM2_CONFIG:-${ROOT}/configs/hislam2_owndata_replica_full_official_r1.yaml}"
OUT_DIR="${HI_SLAM2_OUT_DIR:?set HI_SLAM2_OUT_DIR}"
BUFFER_SIZE="${HI_SLAM2_BUFFER:-1000}"
TSDF_VOXEL_SIZE="${HI_SLAM2_TSDF_VOXEL_SIZE:-0.01}"
TSDF_WEIGHT="${HI_SLAM2_TSDF_WEIGHT:-2}"
MIN_START_FRAMES="${HI_SLAM2_MIN_START_FRAMES:-24}"

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
EXTRA_PYTHONPATH=""
if [[ -d "${REPO_BUILD_LIB}" ]] && compgen -G "${REPO_BUILD_LIB}/droid_backends"*.so >/dev/null; then
  EXTRA_PYTHONPATH="${REPO_BUILD_LIB}"
fi
if [[ -d "${LIETORCH_BUILD_LIB}" ]] && compgen -G "${LIETORCH_BUILD_LIB}/lietorch_backends"*.so >/dev/null; then
  if [[ -n "${EXTRA_PYTHONPATH}" ]]; then
    EXTRA_PYTHONPATH="${EXTRA_PYTHONPATH}:${LIETORCH_BUILD_LIB}"
  else
    EXTRA_PYTHONPATH="${LIETORCH_BUILD_LIB}"
  fi
fi
if [[ -n "${EXTRA_PYTHONPATH}" ]]; then
  export PYTHONPATH="${EXTRA_PYTHONPATH}:${REPO}:${ROOT}/scripts:${PYTHONPATH:-}"
else
  export PYTHONPATH="${REPO}:${ROOT}/scripts:${PYTHONPATH:-}"
fi
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
export HI_SLAM2_REPO="${REPO}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

mkdir -p "${OUT_DIR}"
cd "${REPO}"

for _ in $(seq 1 600); do
  if [[ -d "${SEQ_DIR}/images" && -f "${CALIB_PATH}" ]]; then
    jpg_count="$(find "${SEQ_DIR}/images" -maxdepth 1 -type f -name '*.jpg' | wc -l | tr -d ' ')"
    if [[ "${jpg_count}" -ge "${MIN_START_FRAMES}" ]]; then
      break
    fi
  fi
  sleep 2
done

echo "[hislam2-live] repo=${REPO}"
echo "[hislam2-live] seq=${SEQ_DIR}"
echo "[hislam2-live] calib=${CALIB_PATH}"
echo "[hislam2-live] config=${CONFIG_PATH}"
echo "[hislam2-live] out=${OUT_DIR}"

"${PYTHON_BIN}" "${ROOT}/scripts/hislam2_live_demo.py" \
  --imagedir "${SEQ_DIR}/images" \
  --calib "${CALIB_PATH}" \
  --config "${CONFIG_PATH}" \
  --buffer "${BUFFER_SIZE}" \
  --output "${OUT_DIR}" \
  --poll-interval 0.25 \
  --stable-seconds 1.0

"${PYTHON_BIN}" tsdf_integrate.py --result "${OUT_DIR}" --voxel_size "${TSDF_VOXEL_SIZE}" --weight "${TSDF_WEIGHT}"
