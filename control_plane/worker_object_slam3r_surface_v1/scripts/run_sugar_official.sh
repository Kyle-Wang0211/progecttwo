#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <repo_dir> <sparse2dgs_dir> <output_dir>" >&2
  exit 64
fi

REPO_DIR="$1"
SPARSE2DGS_DIR="$2"
OUTPUT_DIR="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}"
SCENE_DIR="${SUGAR_SCENE_DIR:-${SPARSE2DGS_DIR}/scene}"
GS_OUTPUT_DIR="${SUGAR_GS_OUTPUT_DIR:-${SPARSE2DGS_DIR}/gaussian_splatting_output}"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "sugar_repo_missing: ${REPO_DIR}" >&2
  exit 2
fi
if [[ ! -d "${SCENE_DIR}" || ! -d "${GS_OUTPUT_DIR}" ]]; then
  echo "sugar_native_contract_missing: expected official scene dir ${SCENE_DIR} and vanilla 3DGS checkpoint dir ${GS_OUTPUT_DIR}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"

cd "${REPO_DIR}"
exec "${PYTHON_BIN}" train_full_pipeline.py \
  -s "${SCENE_DIR}" \
  --gs_output_dir "${GS_OUTPUT_DIR}" \
  -r dn_consistency \
  --high_poly True \
  -t True \
  --export_ply True \
  --gpu 0
