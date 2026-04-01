#!/usr/bin/env bash
set -euo pipefail

CLEAN_ROOT="${CLEAN_ROOT:-/root/gs_refs/HI-SLAM2.clean}"
DIRTY_ROOT="${DIRTY_ROOT:-/root/gs_refs/HI-SLAM2}"

git -C "${CLEAN_ROOT}" checkout -- \
  demo.py \
  hislam2/hi2.py \
  hislam2/midas/base_model.py \
  hislam2/midas/omnidata.py \
  setup.py \
  thirdparty/diff-gaussian-rasterization/CMakeLists.txt \
  thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h \
  thirdparty/lietorch

cp "${DIRTY_ROOT}/demo.py" "${CLEAN_ROOT}/demo.py"
cp "${DIRTY_ROOT}/hislam2/hi2.py" "${CLEAN_ROOT}/hislam2/hi2.py"
cp "${DIRTY_ROOT}/hislam2/midas/base_model.py" "${CLEAN_ROOT}/hislam2/midas/base_model.py"
cp "${DIRTY_ROOT}/hislam2/midas/omnidata.py" "${CLEAN_ROOT}/hislam2/midas/omnidata.py"
cp "${DIRTY_ROOT}/setup.py" "${CLEAN_ROOT}/setup.py"
cp "${DIRTY_ROOT}/thirdparty/diff-gaussian-rasterization/CMakeLists.txt" \
  "${CLEAN_ROOT}/thirdparty/diff-gaussian-rasterization/CMakeLists.txt"
cp "${DIRTY_ROOT}/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h" \
  "${CLEAN_ROOT}/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h"
rm -rf "${CLEAN_ROOT}/thirdparty/lietorch"
cp -R "${DIRTY_ROOT}/thirdparty/lietorch" "${CLEAN_ROOT}/thirdparty/lietorch"

git -C "${CLEAN_ROOT}" diff --name-only
