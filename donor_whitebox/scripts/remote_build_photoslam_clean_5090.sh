#!/usr/bin/env bash
set -euo pipefail

PHOTO_SLAM_ROOT="${PHOTO_SLAM_ROOT:-/root/gs_refs/Photo-SLAM.clean}"
PHOTO_SLAM_ENV="${PHOTO_SLAM_ENV:-/venv/photo_slam}"
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda-12.9}"
OPENCV_PREFIX="${OPENCV_PREFIX:-/opt/opencv-4.10.0-cuda}"
BUILD_DIR_NAME="${BUILD_DIR_NAME:-build_5090}"
JOBS="${JOBS:-8}"
CC_BIN="${CC_BIN:-/usr/bin/gcc-11}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++-11}"
APP_TARGET="${APP_TARGET:-replica_mono}"
APP_BIN="${APP_BIN:-./bin/${APP_TARGET}}"
OUT_DIR="${OUT_DIR:-/root/donor_whitebox/outputs/photoslam_runs/room3x3_dense600_clean_5090}"
SEQ_DIR="${SEQ_DIR:-/root/donor_whitebox/outputs/photoslam_room3x3_seq_dense_600f}"
ORB_CFG="${ORB_CFG:-/root/donor_whitebox/configs/photoslam_room3x3_orb_dense600.yaml}"
MAPPER_CFG="${MAPPER_CFG:-/root/donor_whitebox/configs/photoslam_room3x3_mapper.yaml}"
LOG_DIR="${LOG_DIR:-/root/donor_whitebox/logs}"

source "${PHOTO_SLAM_ENV}/bin/activate"

TORCH_DIR="$(python - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "share/cmake/Torch")
PY
)"
TORCH_LIB_DIR="$(python - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"

export PATH="${CUDA_ROOT}/bin:${PATH}"
export CC="${CC_BIN}"
export CXX="${CXX_BIN}"
export CUDAHOSTCXX="${CXX_BIN}"
export CUDA_HOME="${CUDA_ROOT}"
export CUDACXX="${CUDA_ROOT}/bin/nvcc"
export CUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}"
export CUDAToolkit_ROOT="${CUDA_ROOT}"
export CMAKE_CUDA_COMPILER="${CUDA_ROOT}/bin/nvcc"
export OpenCV_DIR="${OPENCV_PREFIX}/lib/cmake/opencv4"
export CMAKE_PREFIX_PATH="${OPENCV_PREFIX}:${CMAKE_PREFIX_PATH:-}"
export LD_LIBRARY_PATH="${CUDA_ROOT}/lib64:${OPENCV_PREFIX}/lib:${TORCH_LIB_DIR}:${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="${OPENCV_PREFIX}/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

mkdir -p "${LOG_DIR}" "${OUT_DIR}"
BUILD_LOG="${LOG_DIR}/photoslam_clean_5090_build_$(date +%Y%m%d_%H%M%S).log"
RUN_LOG="${LOG_DIR}/photoslam_clean_5090_run_$(date +%Y%m%d_%H%M%S).log"
echo "BUILD_LOG=${BUILD_LOG}"
echo "RUN_LOG=${RUN_LOG}"

cd "${PHOTO_SLAM_ROOT}"

python - <<'PY'
import importlib
for mod in ("torch", "yaml", "cv2"):
    importlib.import_module(mod)
    print(f"{mod}: OK")
PY

cd "${PHOTO_SLAM_ROOT}/ORB-SLAM3/Thirdparty/DBoW2"
rm -rf build_5090 && mkdir build_5090 && cd build_5090
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}" -DOpenCV_DIR="${OpenCV_DIR}" > "${BUILD_LOG}" 2>&1
ninja -j "${JOBS}" >> "${BUILD_LOG}" 2>&1

cd ../../g2o
rm -rf build_5090 && mkdir build_5090 && cd build_5090
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}" >> "${BUILD_LOG}" 2>&1
ninja -j "${JOBS}" >> "${BUILD_LOG}" 2>&1

cd ../../Sophus
rm -rf build_5090 && mkdir build_5090 && cd build_5090
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}" >> "${BUILD_LOG}" 2>&1
ninja -j "${JOBS}" >> "${BUILD_LOG}" 2>&1

cd ../../../Vocabulary
[[ -f ORBvoc.txt ]] || tar -xf ORBvoc.txt.tar.gz

cd ..
rm -rf build_5090 && mkdir build_5090 && cd build_5090
cmake .. -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="${CC}" \
  -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_CUDA_HOST_COMPILER="${CUDAHOSTCXX}" \
  -DOpenCV_DIR="${OpenCV_DIR}" \
  -DCMAKE_CUDA_COMPILER="${CMAKE_CUDA_COMPILER}" \
  -DCMAKE_CUDA_ARCHITECTURES=120 >> "${BUILD_LOG}" 2>&1
ninja -j "${JOBS}" >> "${BUILD_LOG}" 2>&1

cd ../..
rm -rf "${BUILD_DIR_NAME}" && mkdir "${BUILD_DIR_NAME}" && cd "${BUILD_DIR_NAME}"
cmake .. -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="${CC}" \
  -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_CUDA_HOST_COMPILER="${CUDAHOSTCXX}" \
  -DTorch_DIR="${TORCH_DIR}" \
  -DOpenCV_DIR="${OpenCV_DIR}" \
  -DCMAKE_CUDA_COMPILER="${CMAKE_CUDA_COMPILER}" \
  -DCMAKE_CUDA_ARCHITECTURES=120 >> "${BUILD_LOG}" 2>&1
ninja -j "${JOBS}" "${APP_TARGET}" >> "${BUILD_LOG}" 2>&1

cd "${PHOTO_SLAM_ROOT}"
"${APP_BIN}" \
  ./ORB-SLAM3/Vocabulary/ORBvoc.txt \
  "${ORB_CFG}" \
  "${MAPPER_CFG}" \
  "${SEQ_DIR}" \
  "${OUT_DIR}" \
  no_viewer > "${RUN_LOG}" 2>&1
