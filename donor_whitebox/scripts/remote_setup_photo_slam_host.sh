#!/usr/bin/env bash
set -euo pipefail

PHOTO_SLAM_ROOT="${PHOTO_SLAM_ROOT:-/root/gs_refs/Photo-SLAM}"
OPENCV_VERSION="${OPENCV_VERSION:-4.10.0}"
OPENCV_PREFIX="${OPENCV_PREFIX:-/opt/opencv-${OPENCV_VERSION}-cuda}"
BUILD_ROOT="${BUILD_ROOT:-/root/build/opencv-${OPENCV_VERSION}-cuda}"
JOBS="${JOBS:-$(nproc)}"
CC_BIN="${CC_BIN:-/usr/bin/gcc-11}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++-11}"
PYTHON_ENV="${PYTHON_ENV:-/venv/photo_slam}"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential ninja-build pkg-config git curl unzip tar \
  gcc-11 g++-11 \
  libgtk-3-dev libavcodec-dev libavformat-dev libswscale-dev \
  libjpeg-dev libpng-dev libtiff-dev libopenexr-dev libwebp-dev \
  libtbb-dev libeigen3-dev libglew-dev libglfw3-dev libglm-dev \
  libjsoncpp-dev libboost-serialization-dev libssl-dev

if [ -d "${PYTHON_ENV}" ]; then
  source "${PYTHON_ENV}/bin/activate"
  python -m pip install --upgrade pip setuptools wheel
  python -m pip install pyyaml opencv-python-headless
fi

mkdir -p /root/third_party/opencv-src /root/build
cd /root/third_party/opencv-src

if [ ! -d "opencv-${OPENCV_VERSION}" ]; then
  git clone --depth 1 --branch "${OPENCV_VERSION}" https://github.com/opencv/opencv.git "opencv-${OPENCV_VERSION}"
fi
if [ ! -d "opencv_contrib-${OPENCV_VERSION}" ]; then
  git clone --depth 1 --branch "${OPENCV_VERSION}" https://github.com/opencv/opencv_contrib.git "opencv_contrib-${OPENCV_VERSION}"
fi

# OpenCV's CUDA detector is stale for Blackwell-class arches. On 5090 hosts it
# can still trip the deprecated-CC guard because the regex treats "1.0" as a
# wildcard and misreads "120" as "1.0". Disable only that legacy guard in the
# actual helper that raises the error and keep the rest of the CUDA detection
# path intact.
python - <<PY
from pathlib import Path
import re

path = Path("/root/third_party/opencv-src/opencv-${OPENCV_VERSION}/cmake/OpenCVDetectCUDAUtils.cmake")
text = path.read_text()
patched = re.sub(
    r'  # Check if user specified 1\\.0/2\\.1 compute capability: we don\\'t support it\\n'
    r'  macro\\(ocv_wipeout_deprecated_cc target_cc\\)\\n'
    r'    if\\(" \\$\\{CUDA_ARCH_BIN\\} \\$\\{CUDA_ARCH_PTX\\}" MATCHES " \\$\\{target_cc\\}"\\)\\n'
    r'      message\\(SEND_ERROR "CUDA: \\$\\{target_cc\\} compute capability is not supported - exclude it from ARCH/PTX list and re-run CMake"\\)\\n'
    r'    endif\\(\\)\\n'
    r'  endmacro\\(\\)\\n'
    r'  ocv_wipeout_deprecated_cc\\("1\\\\\\.0"\\)\\n'
    r'  ocv_wipeout_deprecated_cc\\("2\\\\\\.1"\\)\\n',
    '  # Deprecated CC guard disabled for explicit Blackwell builds (CUDA_ARCH_BIN=120).\\n',
    text,
    count=1,
)
if patched != text:
    path.write_text(patched)
    print(f"patched {path} to disable deprecated CC guard for Blackwell")
else:
    print(f"{path} already patched")
PY

rm -rf "${BUILD_ROOT}"
mkdir -p "${BUILD_ROOT}"
cd "${BUILD_ROOT}"

cmake -G Ninja \
  -D CMAKE_BUILD_TYPE=Release \
  -D CMAKE_C_COMPILER="${CC_BIN}" \
  -D CMAKE_CXX_COMPILER="${CXX_BIN}" \
  -D CUDA_HOST_COMPILER="${CXX_BIN}" \
  -D ENABLE_CUDA_FIRST_CLASS_LANGUAGE=OFF \
  -D CMAKE_INSTALL_PREFIX="${OPENCV_PREFIX}" \
  -D OPENCV_EXTRA_MODULES_PATH="/root/third_party/opencv-src/opencv_contrib-${OPENCV_VERSION}/modules" \
  -D WITH_CUDA=ON \
  -D CUDA_ARCH_BIN=120 \
  -D CUDA_FAST_MATH=ON \
  -D WITH_CUBLAS=ON \
  -D OPENCV_DNN_CUDA=OFF \
  -D WITH_CUDNN=OFF \
  -D BUILD_LIST=core,imgproc,imgcodecs,videoio,video,highgui,calib3d,features2d,flann,cudev,cudaarithm,cudafilters,cudaimgproc,cudawarping,cudastereo \
  -D BUILD_opencv_python3=OFF \
  -D BUILD_opencv_python2=OFF \
  -D BUILD_TESTS=OFF \
  -D BUILD_PERF_TESTS=OFF \
  -D BUILD_EXAMPLES=OFF \
  -D BUILD_JAVA=OFF \
  -D BUILD_DOCS=OFF \
  -D BUILD_opencv_world=OFF \
  -D WITH_QT=OFF \
  -D WITH_OPENGL=ON \
  -D WITH_GSTREAMER=OFF \
  -D WITH_IPP=OFF \
  -D ENABLE_FAST_MATH=ON \
  "/root/third_party/opencv-src/opencv-${OPENCV_VERSION}"

ninja -j "${JOBS}"
ninja install
ldconfig

cat > /etc/profile.d/photo_slam_opencv.sh <<EOF
export CC=${CC_BIN}
export CXX=${CXX_BIN}
export CUDAHOSTCXX=${CXX_BIN}
export OpenCV_DIR=${OPENCV_PREFIX}/lib/cmake/opencv4
export CMAKE_PREFIX_PATH=${OPENCV_PREFIX}:\${CMAKE_PREFIX_PATH:-}
export LD_LIBRARY_PATH=${OPENCV_PREFIX}/lib:\${LD_LIBRARY_PATH:-}
export PKG_CONFIG_PATH=${OPENCV_PREFIX}/lib/pkgconfig:\${PKG_CONFIG_PATH:-}
EOF

cat > /root/gs_refs/Photo-SLAM/build_env.sh <<EOF
export CC=${CC_BIN}
export CXX=${CXX_BIN}
export CUDAHOSTCXX=${CXX_BIN}
export OpenCV_DIR=${OPENCV_PREFIX}/lib/cmake/opencv4
export CMAKE_PREFIX_PATH=${OPENCV_PREFIX}:\${CMAKE_PREFIX_PATH:-}
export LD_LIBRARY_PATH=${OPENCV_PREFIX}/lib:\${LD_LIBRARY_PATH:-}
export PKG_CONFIG_PATH=${OPENCV_PREFIX}/lib/pkgconfig:\${PKG_CONFIG_PATH:-}
EOF

echo "OpenCV CUDA install complete:"
echo "  prefix=${OPENCV_PREFIX}"
echo "  cmake_dir=${OPENCV_PREFIX}/lib/cmake/opencv4"
