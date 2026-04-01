#!/usr/bin/env bash
set -euo pipefail

CLEAN_ROOT="${CLEAN_ROOT:-/root/gs_refs/Photo-SLAM.clean}"
DIRTY_ROOT="${DIRTY_ROOT:-/root/gs_refs/Photo-SLAM}"

git -C "${CLEAN_ROOT}" checkout -- \
  CMakeLists.txt \
  cuda_rasterizer/rasterizer_impl.h \
  examples/euroc_stereo.cpp \
  examples/realsense_rgbd.cpp \
  examples/replica_mono.cpp \
  examples/replica_rgbd.cpp \
  examples/tum_mono.cpp \
  examples/tum_rgbd.cpp \
  src/gaussian_model.cpp \
  third_party/simple-knn/simple_knn.cu

cp "${DIRTY_ROOT}/CMakeLists.txt" "${CLEAN_ROOT}/CMakeLists.txt"
cp "${DIRTY_ROOT}/cuda_rasterizer/rasterizer_impl.h" "${CLEAN_ROOT}/cuda_rasterizer/rasterizer_impl.h"
cp "${DIRTY_ROOT}/examples/euroc_stereo.cpp" "${CLEAN_ROOT}/examples/euroc_stereo.cpp"
cp "${DIRTY_ROOT}/examples/realsense_rgbd.cpp" "${CLEAN_ROOT}/examples/realsense_rgbd.cpp"
cp "${DIRTY_ROOT}/examples/replica_mono.cpp" "${CLEAN_ROOT}/examples/replica_mono.cpp"
cp "${DIRTY_ROOT}/examples/replica_rgbd.cpp" "${CLEAN_ROOT}/examples/replica_rgbd.cpp"
cp "${DIRTY_ROOT}/examples/tum_mono.cpp" "${CLEAN_ROOT}/examples/tum_mono.cpp"
cp "${DIRTY_ROOT}/examples/tum_rgbd.cpp" "${CLEAN_ROOT}/examples/tum_rgbd.cpp"
cp "${DIRTY_ROOT}/src/gaussian_model.cpp" "${CLEAN_ROOT}/src/gaussian_model.cpp"
cp "${DIRTY_ROOT}/third_party/simple-knn/simple_knn.cu" "${CLEAN_ROOT}/third_party/simple-knn/simple_knn.cu"

git -C "${CLEAN_ROOT}" diff --name-only
