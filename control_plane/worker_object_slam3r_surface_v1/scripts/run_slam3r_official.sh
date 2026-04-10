#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <repo_dir> <curated_dir> <output_dir>" >&2
  exit 64
fi

REPO_DIR="$1"
CURATED_DIR="$2"
OUTPUT_DIR="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "slam3r_repo_missing: ${REPO_DIR}" >&2
  exit 2
fi
if [[ ! -d "${CURATED_DIR}" ]]; then
  echo "slam3r_curated_dir_missing: ${CURATED_DIR}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"
TEST_NAME="$(basename "${OUTPUT_DIR}")"
SAVE_DIR="$(dirname "${OUTPUT_DIR}")"

cd "${REPO_DIR}"
exec "${PYTHON_BIN}" recon.py \
  --img_dir "${CURATED_DIR}" \
  --save_dir "${SAVE_DIR}" \
  --test_name "${TEST_NAME}" \
  --save_preds
