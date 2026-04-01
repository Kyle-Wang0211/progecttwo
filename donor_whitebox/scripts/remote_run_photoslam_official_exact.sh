#!/usr/bin/env bash
set -euo pipefail

PHOTO_SLAM_ROOT="${PHOTO_SLAM_ROOT:-/root/gs_refs/Photo-SLAM.clean}"
OPENCV_DIR="${OPENCV_DIR:-/opt/opencv-4.8.0-cuda118-cudnn8/lib/cmake/opencv4}"
TORCH_DIR="${TORCH_DIR:-/root/deps/libtorch/share/cmake/Torch}"
CMAKE_BIN="${CMAKE_BIN:-/root/deps/cmake-3.22.1-linux-x86_64/bin/cmake}"
JOBS="${JOBS:-8}"
CC_BIN="${CC_BIN:-/usr/bin/gcc-10}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++-10}"
CUDA_BIN="${CUDA_BIN:-/root/deps/cuda-11.8/bin}"
CUDA_ROOT="${CUDA_ROOT:-/root/deps/cuda-11.8}"
CUDA_LIB="${CUDA_LIB:-/root/deps/cuda-11.8-cudnn8/lib64}"
OUT_DIR="${OUT_DIR:-/root/donor_whitebox/outputs/photoslam_runs/room3x3_dense600_official}"
LOG_DIR="${LOG_DIR:-/root/donor_whitebox/logs}"
VOCAB="${VOCAB:-${PHOTO_SLAM_ROOT}/ORB-SLAM3/Vocabulary/ORBvoc.txt}"
ORB_CFG="${ORB_CFG:-/root/donor_whitebox/configs/photoslam_room3x3_orb_dense600.yaml}"
MAPPER_CFG="${MAPPER_CFG:-/root/donor_whitebox/configs/photoslam_room3x3_mapper.yaml}"
SEQ_DIR="${SEQ_DIR:-/root/donor_whitebox/outputs/photoslam_room3x3_seq_dense_600f}"
TOP_BUILD_DIR="${TOP_BUILD_DIR:-${PHOTO_SLAM_ROOT}/build_official_exact}"

mkdir -p "${LOG_DIR}" "${OUT_DIR}"

BUILD_LOG="${LOG_DIR}/photoslam_official_exact_build_$(date +%Y%m%d_%H%M%S).log"
RUN_LOG="${LOG_DIR}/photoslam_official_room3x3_dense600_$(date +%Y%m%d_%H%M%S).log"

echo "BUILD_LOG=${BUILD_LOG}"
echo "RUN_LOG=${RUN_LOG}"

while [[ ! -f "${OPENCV_DIR}/OpenCVConfig.cmake" ]]; do
  sleep 20
done

export PATH="$(dirname "${CMAKE_BIN}"):${CUDA_BIN}:${PATH}"
export CC="${CC_BIN}"
export CXX="${CXX_BIN}"
export CUDAHOSTCXX="${CXX_BIN}"
export CUDA_HOME="${CUDA_ROOT}"
export CUDACXX="${CUDA_BIN}/nvcc"
export CUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}"
export CUDA_BIN_PATH="${CUDA_ROOT}"
export CUDAToolkit_ROOT="${CUDA_ROOT}"
export CMAKE_CUDA_COMPILER="${CUDA_BIN}/nvcc"
export OpenCV_DIR="${OPENCV_DIR}"
export Torch_DIR="${TORCH_DIR}"
export CMAKE_PREFIX_PATH="${TORCH_DIR%/share/cmake/Torch}:${OpenCV_DIR%/lib/cmake/opencv4}:${CMAKE_PREFIX_PATH:-}"
export LD_LIBRARY_PATH="${CUDA_LIB}:${TORCH_DIR%/share/cmake/Torch}/lib:${OpenCV_DIR%/lib/cmake/opencv4}/../../:${LD_LIBRARY_PATH:-}"

{
  echo "BUILD_LOG=${BUILD_LOG}"
  echo "RUN_LOG=${RUN_LOG}"
  echo "PHOTO_SLAM_ROOT=${PHOTO_SLAM_ROOT}"
  echo "OpenCV_DIR=${OpenCV_DIR}"
  echo "Torch_DIR=${Torch_DIR}"

  cd "${PHOTO_SLAM_ROOT}/ORB-SLAM3/Thirdparty/DBoW2"
  mkdir -p build
  cd build
  "${CMAKE_BIN}" .. -DCMAKE_BUILD_TYPE=Release \
    -DOpenCV_DIR="${OpenCV_DIR}" \
    -DCUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}" \
    -DCUDA_BIN_PATH="${CUDA_ROOT}" \
    -DCUDAToolkit_ROOT="${CUDA_ROOT}" \
    -DCMAKE_CUDA_COMPILER="${CUDA_BIN}/nvcc"
  make -j"${JOBS}"

  cd ../../g2o
  mkdir -p build
  cd build
  "${CMAKE_BIN}" .. -DCMAKE_BUILD_TYPE=Release
  make -j"${JOBS}"

  cd ../../Sophus
  mkdir -p build
  cd build
  "${CMAKE_BIN}" .. -DCMAKE_BUILD_TYPE=Release
  make -j"${JOBS}"

  cd ../../../Vocabulary
  [[ -f ORBvoc.txt ]] || tar -xf ORBvoc.txt.tar.gz

  cd ..
  mkdir -p build
  cd build
  "${CMAKE_BIN}" .. -DCMAKE_BUILD_TYPE=Release \
    -DOpenCV_DIR="${OpenCV_DIR}" \
    -DCUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}" \
    -DCUDA_BIN_PATH="${CUDA_ROOT}" \
    -DCUDAToolkit_ROOT="${CUDA_ROOT}" \
    -DCMAKE_CUDA_COMPILER="${CUDA_BIN}/nvcc"
  make -j"${JOBS}"

  cd ../..
  mkdir -p "${TOP_BUILD_DIR}"
  cd "${TOP_BUILD_DIR}"
  "${CMAKE_BIN}" "${PHOTO_SLAM_ROOT}" \
    -DTorch_DIR="${Torch_DIR}" \
    -DOpenCV_DIR="${OpenCV_DIR}" \
    -DCUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}" \
    -DCUDA_BIN_PATH="${CUDA_ROOT}" \
    -DCUDAToolkit_ROOT="${CUDA_ROOT}" \
    -DCMAKE_CUDA_COMPILER="${CUDA_BIN}/nvcc"
  make -j"${JOBS}"
} > "${BUILD_LOG}" 2>&1

cd "${PHOTO_SLAM_ROOT}"
./bin/replica_mono \
  "${VOCAB}" \
  "${ORB_CFG}" \
  "${MAPPER_CFG}" \
  "${SEQ_DIR}" \
  "${OUT_DIR}" \
  no_viewer > "${RUN_LOG}" 2>&1

echo "BUILD_LOG=${BUILD_LOG}"
echo "RUN_LOG=${RUN_LOG}"
