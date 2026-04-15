#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "usage: $0 <repo_dir> <scene_dir> <sparse2dgs_model_dir> <output_dir>" >&2
  exit 64
fi

REPO_DIR="$1"
SCENE_DIR="$2"
MODEL_DIR="$3"
OUTPUT_DIR="$4"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${OBJECT_SLAM3R_SURFACE_MATCHA_PYTHON_BIN:-${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}}"
PYTHON_DIR="$(cd "$(dirname "${PYTHON_BIN}")" && pwd)"
TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib
import torch

print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "matcha_repo_missing: ${REPO_DIR}" >&2
  exit 2
fi
if [[ ! -d "${SCENE_DIR}/images" || ! -d "${SCENE_DIR}/sparse" ]]; then
  echo "matcha_scene_contract_missing: expected posed COLMAP scene at ${SCENE_DIR}" >&2
  exit 2
fi
if [[ ! -d "${MODEL_DIR}/point_cloud" || ! -f "${MODEL_DIR}/cfg_args" ]]; then
  echo "matcha_model_contract_missing: expected Sparse2DGS checkpoint dir at ${MODEL_DIR}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"
export PYTHONPATH="${REPO_DIR}:${REPO_DIR}/2d-gaussian-splatting/submodules/simple-knn:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-12.0}"
export CUDA_HOME="${OBJECT_SLAM3R_SURFACE_CUDA_HOME:-${CUDA_HOME:-/usr/local/cuda}}"
export PATH="${PYTHON_DIR}:${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"

cd "${REPO_DIR}"
exec "${PYTHON_BIN}" scripts/extract_tetra_mesh.py \
  -s "${SCENE_DIR}" \
  -m "${MODEL_DIR}" \
  -o "${OUTPUT_DIR}"
