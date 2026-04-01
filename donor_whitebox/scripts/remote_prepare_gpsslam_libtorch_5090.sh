#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/GPS-SLAM.clean}"
TORCH_ROOT="${TORCH_ROOT:-/venv/hislam2/lib/python3.10/site-packages/torch}"
BUILD_DIR="${BUILD_DIR:-${REPO}/build}"
LIBTORCH_DIR="${REPO}/ThirdLibs/libtorch"
LIBTORCH_BACKUP="${REPO}/ThirdLibs/libtorch.cu118.bak"
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda-12.9}"

if [[ ! -d "${TORCH_ROOT}" ]]; then
  echo "[gpsslam-5090] missing torch root ${TORCH_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${CUDA_ROOT}/bin/nvcc" ]]; then
  echo "[gpsslam-5090] missing cuda toolkit ${CUDA_ROOT}" >&2
  exit 1
fi

if [[ -d "${LIBTORCH_DIR}" && ! -L "${LIBTORCH_DIR}" && ! -e "${LIBTORCH_BACKUP}" ]]; then
  mv "${LIBTORCH_DIR}" "${LIBTORCH_BACKUP}"
fi

if [[ -L "${LIBTORCH_DIR}" ]]; then
  unlink "${LIBTORCH_DIR}"
fi

ln -s "${TORCH_ROOT}" "${LIBTORCH_DIR}"

if [[ -x /root/donor_whitebox/scripts/remote_patch_gpsslam_cuda_arch_list.sh ]]; then
  /root/donor_whitebox/scripts/remote_patch_gpsslam_cuda_arch_list.sh
fi

if [[ -x /root/donor_whitebox/scripts/remote_patch_gpsslam_tensorboard_stub.sh ]]; then
  /root/donor_whitebox/scripts/remote_patch_gpsslam_tensorboard_stub.sh
fi

cd "${BUILD_DIR}"
rm -f CMakeCache.txt
find . -maxdepth 1 -type f \( -name '*.a' -o -name '*.so' -o -name 'slam_trainer' -o -name 'remote_viewer' \) -delete
find . -type f \( -name '*.o' -o -name '*.obj' -o -name '*.cmake' \) -delete
export CUDACXX="${CUDA_ROOT}/bin/nvcc"
export CUDA_HOME="${CUDA_ROOT}"
export CUDA_PATH="${CUDA_ROOT}"
export PATH="${CUDA_ROOT}/bin:${PATH}"
CC=/usr/bin/gcc-11 CXX=/usr/bin/g++-11 cmake .. \
  -DCMAKE_CUDA_COMPILER="${CUDA_ROOT}/bin/nvcc" \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-11 \
  -DCUDAToolkit_ROOT="${CUDA_ROOT}" \
  -DCMAKE_CUDA_ARCHITECTURES=120 \
  -DProtobuf_INCLUDE_DIR=/usr/include \
  -DProtobuf_LIBRARY=/usr/lib/x86_64-linux-gnu/libprotobuf.so \
  -DProtobuf_PROTOC_EXECUTABLE=/usr/bin/protoc \
  -DNVML_LIBRARY=/usr/lib/x86_64-linux-gnu/libnvidia-ml.so.1
make -j16
