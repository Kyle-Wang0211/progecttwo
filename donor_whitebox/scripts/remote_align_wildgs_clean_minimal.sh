#!/usr/bin/env bash
set -euo pipefail

CLEAN_ROOT="${CLEAN_ROOT:-/root/gs_refs/WildGS-SLAM.clean}"
DIRTY_ROOT="${DIRTY_ROOT:-/root/gs_refs/WildGS-SLAM}"

git -C "${CLEAN_ROOT}" checkout -- \
  setup.py \
  src/lib/altcorr_kernel.cu \
  src/lib/correlation_kernels.cu \
  src/mapper.py \
  src/modules/droid_net/corr.py \
  src/motion_filter.py \
  src/slam.py \
  src/utils/Printer.py \
  src/utils/mono_priors/img_feature_extractors.py \
  src/utils/pose_utils.py \
  src/utils/slam_utils.py \
  thirdparty/diff-gaussian-rasterization-w-pose \
  thirdparty/gaussian_splatting/scene/gaussian_model.py \
  thirdparty/lietorch \
  thirdparty/simple-knn

cp "${DIRTY_ROOT}/setup.py" "${CLEAN_ROOT}/setup.py"
cp "${DIRTY_ROOT}/src/lib/altcorr_kernel.cu" "${CLEAN_ROOT}/src/lib/altcorr_kernel.cu"
cp "${DIRTY_ROOT}/src/lib/correlation_kernels.cu" "${CLEAN_ROOT}/src/lib/correlation_kernels.cu"
cp "${DIRTY_ROOT}/src/slam.py" "${CLEAN_ROOT}/src/slam.py"
cp "${DIRTY_ROOT}/src/utils/Printer.py" "${CLEAN_ROOT}/src/utils/Printer.py"
cp "${DIRTY_ROOT}/src/utils/mono_priors/img_feature_extractors.py" "${CLEAN_ROOT}/src/utils/mono_priors/img_feature_extractors.py"
cp "${DIRTY_ROOT}/src/utils/pose_utils.py" "${CLEAN_ROOT}/src/utils/pose_utils.py"
cp "${DIRTY_ROOT}/src/utils/slam_utils.py" "${CLEAN_ROOT}/src/utils/slam_utils.py"
rm -rf "${CLEAN_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose"
cp -R "${DIRTY_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose" "${CLEAN_ROOT}/thirdparty/diff-gaussian-rasterization-w-pose"
rm -rf "${CLEAN_ROOT}/thirdparty/lietorch"
cp -R "${DIRTY_ROOT}/thirdparty/lietorch" "${CLEAN_ROOT}/thirdparty/lietorch"
rm -rf "${CLEAN_ROOT}/thirdparty/simple-knn"
cp -R "${DIRTY_ROOT}/thirdparty/simple-knn" "${CLEAN_ROOT}/thirdparty/simple-knn"

# Keep algorithm behavior as close to official as possible for these files.
cp "${DIRTY_ROOT}/src/modules/droid_net/corr.py" "${CLEAN_ROOT}/src/modules/droid_net/corr.py"
cp "${DIRTY_ROOT}/thirdparty/gaussian_splatting/scene/gaussian_model.py" \
  "${CLEAN_ROOT}/thirdparty/gaussian_splatting/scene/gaussian_model.py"

git -C "${CLEAN_ROOT}" diff --name-only
