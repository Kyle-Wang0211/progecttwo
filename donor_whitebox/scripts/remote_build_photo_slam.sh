#!/usr/bin/env bash
set -euo pipefail

PHOTO_SLAM_ROOT="${PHOTO_SLAM_ROOT:-/root/gs_refs/Photo-SLAM}"
PHOTO_SLAM_ENV="${PHOTO_SLAM_ENV:-/venv/photo_slam}"
JOBS="${JOBS:-8}"
CC_BIN="${CC_BIN:-/usr/bin/gcc-11}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++-11}"

if [[ ! -x "${CC_BIN}" ]]; then
  CC_BIN="$(command -v gcc)"
fi
if [[ ! -x "${CXX_BIN}" ]]; then
  CXX_BIN="$(command -v g++)"
fi

source "${PHOTO_SLAM_ENV}/bin/activate"

if [[ -f "${PHOTO_SLAM_ROOT}/build_env.sh" ]]; then
  source "${PHOTO_SLAM_ROOT}/build_env.sh"
else
  export OpenCV_DIR="${OpenCV_DIR:-/opt/opencv-4.10.0-cuda/lib/cmake/opencv4}"
  export CMAKE_PREFIX_PATH="/opt/opencv-4.10.0-cuda:${CMAKE_PREFIX_PATH:-}"
  export LD_LIBRARY_PATH="/opt/opencv-4.10.0-cuda/lib:${LD_LIBRARY_PATH:-}"
  export PKG_CONFIG_PATH="/opt/opencv-4.10.0-cuda/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
fi

export CC="${CC_BIN}"
export CXX="${CXX_BIN}"
export CUDAHOSTCXX="${CXX_BIN}"

TORCH_DIR="$(python - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "share/cmake/Torch")
PY
)"

cd "${PHOTO_SLAM_ROOT}"

python - <<'PY'
import importlib
for mod in ("torch", "yaml", "cv2"):
    importlib.import_module(mod)
    print(f"{mod}: OK")
PY

python - <<'PY'
from pathlib import Path
path = Path("CMakeLists.txt")
text = path.read_text()
old = 'set_target_properties(cuda_rasterizer PROPERTIES CUDA_ARCHITECTURES "75;86")'
new = 'set_target_properties(cuda_rasterizer PROPERTIES CUDA_ARCHITECTURES "120")'
if old in text:
    path.write_text(text.replace(old, new))
    print("patched CUDA_ARCHITECTURES -> 120")
else:
    print("CUDA_ARCHITECTURES already patched or managed elsewhere")
PY

cd "${PHOTO_SLAM_ROOT}/ORB-SLAM3/Thirdparty/DBoW2"
rm -rf build && mkdir build && cd build
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}" -DOpenCV_DIR="${OpenCV_DIR}"
ninja -j "${JOBS}"

cd ../../g2o
rm -rf build && mkdir build && cd build
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}"
ninja -j "${JOBS}"

cd ../../Sophus
rm -rf build && mkdir build && cd build
cmake .. -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}"
ninja -j "${JOBS}"

cd ../../../Vocabulary
tar -xf ORBvoc.txt.tar.gz

cd ..
rm -rf build && mkdir build && cd build
cmake .. -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="${CC}" \
  -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_CUDA_HOST_COMPILER="${CUDAHOSTCXX}" \
  -DOpenCV_DIR="${OpenCV_DIR}"
ninja -j "${JOBS}"

cd ../..
rm -rf build && mkdir build && cd build
cmake .. -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_C_COMPILER="${CC}" \
  -DCMAKE_CXX_COMPILER="${CXX}" \
  -DCMAKE_CUDA_HOST_COMPILER="${CUDAHOSTCXX}" \
  -DTorch_DIR="${TORCH_DIR}" \
  -DOpenCV_DIR="${OpenCV_DIR}" \
  -DCMAKE_CUDA_ARCHITECTURES=120
ninja -j "${JOBS}"

echo "Photo-SLAM build complete"
