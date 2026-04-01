#!/usr/bin/env bash
set -euo pipefail

DIRTY_ROOT="${DIRTY_ROOT:-/root/gs_refs/WildGS-SLAM}"
CLEAN_ROOT="${CLEAN_ROOT:-/root/gs_refs/WildGS-SLAM.clean}"

cp "${DIRTY_ROOT}/setup.py" "${CLEAN_ROOT}/setup.py"
cp "${DIRTY_ROOT}/src/lib/altcorr_kernel.cu" "${CLEAN_ROOT}/src/lib/altcorr_kernel.cu"
cp "${DIRTY_ROOT}/src/lib/correlation_kernels.cu" "${CLEAN_ROOT}/src/lib/correlation_kernels.cu"
cp "${DIRTY_ROOT}/src/motion_filter.py" "${CLEAN_ROOT}/src/motion_filter.py"
cp "${DIRTY_ROOT}/src/mapper.py" "${CLEAN_ROOT}/src/mapper.py"
cp "${DIRTY_ROOT}/src/utils/slam_utils.py" "${CLEAN_ROOT}/src/utils/slam_utils.py"

# Copy the lietorch compatibility source changes from the already-working dirty tree.
rm -rf "${CLEAN_ROOT}/thirdparty/lietorch"
cp -a "${DIRTY_ROOT}/thirdparty/lietorch" "${CLEAN_ROOT}/thirdparty/lietorch"
rm -rf "${CLEAN_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose"
cp -a "${DIRTY_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose" "${CLEAN_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose"
rm -rf "${CLEAN_ROOT}/thirdparty/simple-knn"
cp -a "${DIRTY_ROOT}/thirdparty/simple-knn" "${CLEAN_ROOT}/thirdparty/simple-knn"
rm -rf "${CLEAN_ROOT}/pretrained"
cp -a "${DIRTY_ROOT}/pretrained" "${CLEAN_ROOT}/pretrained"

echo "[remote_patch_wildgs_clean_from_dirty] patched ${CLEAN_ROOT} from ${DIRTY_ROOT}"
