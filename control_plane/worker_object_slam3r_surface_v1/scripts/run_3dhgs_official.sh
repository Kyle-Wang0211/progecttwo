#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <repo_dir> <scene_dir> <output_dir>" >&2
  exit 64
fi

REPO_DIR="$1"
SCENE_DIR="$2"
OUTPUT_DIR="$3"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}"

if [[ ! -d "${REPO_DIR}" ]]; then
  echo "3dhgs_repo_missing: ${REPO_DIR}" >&2
  exit 2
fi
if [[ ! -d "${SCENE_DIR}" ]]; then
  echo "3dhgs_native_contract_missing: expected official SfM scene dir at ${SCENE_DIR}" >&2
  exit 2
fi

mkdir -p "${OUTPUT_DIR}"

cd "${REPO_DIR}"
exec "${PYTHON_BIN}" train.py \
  -s "${SCENE_DIR}" \
  -m "${OUTPUT_DIR}"
