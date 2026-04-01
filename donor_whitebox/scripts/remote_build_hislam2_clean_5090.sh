#!/usr/bin/env bash
set -euo pipefail

HI_ROOT="${HI_ROOT:-/root/gs_refs/HI-SLAM2.clean}"
PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda-12.9}"
CC_BIN="${CC_BIN:-/usr/bin/gcc-11}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++-11}"
SEQ_DIR="${SEQ_DIR:-/root/donor_whitebox/outputs/hislam2_room3x3_seq_dense_600f_12s}"
CONFIG="${CONFIG:-/root/donor_whitebox/configs/hislam2_owndata_dense.yaml}"
OUT_DIR="${OUT_DIR:-/root/donor_whitebox/outputs/hislam2_room3x3_clean_5090}"
LOG_DIR="${LOG_DIR:-/root/donor_whitebox/logs}"
BUFFER_SIZE="${BUFFER_SIZE:-1000}"

TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
VENV_LIB_DIR="$(cd "$(dirname "${PYTHON_BIN}")/.." && pwd)/lib"

export PATH="${CUDA_ROOT}/bin:${PATH}"
export CUDA_HOME="${CUDA_ROOT}"
export CUDACXX="${CUDA_ROOT}/bin/nvcc"
export CUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}"
export CUDAToolkit_ROOT="${CUDA_ROOT}"
export TORCH_CUDA_ARCH_LIST="12.0"
export CC="${CC_BIN}"
export CXX="${CXX_BIN}"
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${VENV_LIB_DIR}:${CUDA_ROOT}/lib64:${LD_LIBRARY_PATH:-}"
export TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1

mkdir -p "${LOG_DIR}" "${OUT_DIR}"
BUILD_LOG="${LOG_DIR}/hislam2_clean_5090_build_$(date +%Y%m%d_%H%M%S).log"
RUN_LOG="${LOG_DIR}/hislam2_clean_5090_run_$(date +%Y%m%d_%H%M%S).log"
echo "BUILD_LOG=${BUILD_LOG}"
echo "RUN_LOG=${RUN_LOG}"

cd "${HI_ROOT}"
rm -rf build
"${PYTHON_BIN}" setup.py install > "${BUILD_LOG}" 2>&1
"${PYTHON_BIN}" -m pip install --no-build-isolation thirdparty/simple-knn thirdparty/diff-gaussian-rasterization >> "${BUILD_LOG}" 2>&1

"${PYTHON_BIN}" - <<'PY'
import importlib
for mod in ("droid_backends", "lietorch", "simple_knn", "diff_gaussian_rasterization"):
    importlib.import_module(mod)
    print(f"{mod}: OK")
PY

exec "${PYTHON_BIN}" demo.py \
  --imagedir "${SEQ_DIR}/images" \
  --calib "${SEQ_DIR}/calib.txt" \
  --config "${CONFIG}" \
  --buffer "${BUFFER_SIZE}" \
  --output "${OUT_DIR}" > "${RUN_LOG}" 2>&1
