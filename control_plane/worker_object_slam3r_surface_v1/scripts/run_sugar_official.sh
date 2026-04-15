#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "usage: $0 <repo_dir> <scene_dir> <gs_output_dir> <output_dir>" >&2
  exit 64
fi

REPO_DIR="$1"
SCENE_DIR="$2"
GS_OUTPUT_DIR="$3"
OUTPUT_DIR="$4"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${OBJECT_SLAM3R_SURFACE_SUGAR_PYTHON_BIN:-${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}}"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "sugar_repo_missing: ${REPO_DIR}" >&2
  exit 2
fi
if [[ ! -d "${SCENE_DIR}" || ! -d "${GS_OUTPUT_DIR}" ]]; then
  echo "sugar_native_contract_missing: expected official scene dir ${SCENE_DIR} and vanilla 3DGS checkpoint dir ${GS_OUTPUT_DIR}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"
export PYTHONPATH="${REPO_DIR}:${PYTHONPATH:-}"

TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib
import torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
export CUDA_HOME="${OBJECT_SLAM3R_SURFACE_CUDA_HOME:-${CUDA_HOME:-/usr/local/cuda}}"
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"
export PATH="${CUDA_HOME}/bin:${PATH}"
export PATH="$(dirname "${PYTHON_BIN}"):${PATH}"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-12.0}"

cd "${REPO_DIR}"
exec "${PYTHON_BIN}" train_full_pipeline.py \
  -s "${SCENE_DIR}" \
  -r dn_consistency \
  --high_poly True \
  --export_obj True \
  --gs_output_dir "${GS_OUTPUT_DIR}"
