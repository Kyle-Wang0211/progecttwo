#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/object_fast_publish_v1}"
VENV_DIR="${ROOT_DIR}/venv"
THIRD_PARTY_DIR="${ROOT_DIR}/third_party"
OPENMVS_DIR="${THIRD_PARTY_DIR}/openMVS"
OPENMVS_BUILD_DIR="${ROOT_DIR}/build/openMVS"
OPENMVS_REF="${OPENMVS_REF:-v2.2.0}"
SAM2_DIR="${THIRD_PARTY_DIR}/sam2"
TINYEXIF_DIR="${THIRD_PARTY_DIR}/TinyEXIF"
TINYNPY_DIR="${THIRD_PARTY_DIR}/TinyNPY_openmvs"
POSELIB_DIR="${THIRD_PARTY_DIR}/PoseLib"
VCGLIB_DIR="${THIRD_PARTY_DIR}/vcglib"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  git curl wget unzip ca-certificates pkg-config \
  build-essential cmake ninja-build \
  ffmpeg colmap \
  python3 python3-venv python3-pip \
  libeigen3-dev libboost-program-options-dev libboost-system-dev \
  libboost-filesystem-dev libboost-iostreams-dev libboost-serialization-dev \
  libceres-dev libgoogle-glog-dev libgflags-dev \
  libnanoflann-dev \
  libjxl-dev \
  libtinyxml2-dev \
  libopencv-dev libglew-dev libcgal-dev libgmp-dev libmpfr-dev \
  libatlas-base-dev libsuitesparse-dev qtbase5-dev libqt5opengl5-dev

mkdir -p "${THIRD_PARTY_DIR}"

if [[ ! -d "${OPENMVS_DIR}" ]]; then
  git clone --recursive https://github.com/cdcseacave/openMVS.git "${OPENMVS_DIR}"
else
  git -C "${OPENMVS_DIR}" submodule update --init --recursive
fi
git -C "${OPENMVS_DIR}" fetch --tags --force
git -C "${OPENMVS_DIR}" checkout -f "${OPENMVS_REF}"
git -C "${OPENMVS_DIR}" submodule update --init --recursive
git -C "${OPENMVS_DIR}" checkout -- build

if [[ ! -d "${TINYEXIF_DIR}" ]]; then
  git clone https://github.com/cdcseacave/TinyEXIF.git "${TINYEXIF_DIR}"
else
  git -C "${TINYEXIF_DIR}" pull --ff-only
fi

if [[ ! -d "${TINYNPY_DIR}" ]]; then
  git clone https://github.com/cdcseacave/TinyNPY.git "${TINYNPY_DIR}"
else
  git -C "${TINYNPY_DIR}" pull --ff-only
fi

if [[ ! -d "${POSELIB_DIR}" ]]; then
  git clone https://github.com/PoseLib/PoseLib.git "${POSELIB_DIR}"
else
  git -C "${POSELIB_DIR}" pull --ff-only
fi

if [[ ! -d "${VCGLIB_DIR}" ]]; then
  git clone https://github.com/cnr-isti-vclab/vcglib.git "${VCGLIB_DIR}"
else
  git -C "${VCGLIB_DIR}" pull --ff-only
fi

python3 - "${OPENMVS_DIR}/libs/Common/Types.inl" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = """\t} else\n\tif (ext == \".jxl\") {\n\t\tcompression_params.push_back(cv::IMWRITE_JPEGXL_QUALITY);\n\t\tcompression_params.push_back(95);\n\t} else\n"""
new = """\t} else\n\tif (ext == \".jxl\") {\n#if (CV_VERSION_MAJOR > 4) || (CV_VERSION_MAJOR == 4 && CV_VERSION_MINOR >= 7)\n\t\tcompression_params.push_back(cv::IMWRITE_JPEGXL_QUALITY);\n#else\n\t\tcompression_params.push_back(cv::IMWRITE_JPEG_QUALITY);\n#endif\n\t\tcompression_params.push_back(95);\n\t} else\n"""
if old in text and new not in text:
    path.write_text(text.replace(old, new), encoding="utf-8")
PY

cmake -S "${TINYEXIF_DIR}" -B "${TINYEXIF_DIR}/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${TINYEXIF_DIR}/install" \
  -DBUILD_DEMO=OFF
cmake --build "${TINYEXIF_DIR}/build" -j"$(nproc)"
cmake --install "${TINYEXIF_DIR}/build"

cmake -S "${TINYNPY_DIR}" -B "${TINYNPY_DIR}/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${TINYNPY_DIR}/install" \
  -DBUILD_DEMO=OFF
cmake --build "${TINYNPY_DIR}/build" -j"$(nproc)"
cmake --install "${TINYNPY_DIR}/build"

cmake -S "${POSELIB_DIR}" -B "${POSELIB_DIR}/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="${POSELIB_DIR}/install" \
  -DBUILD_PYTHON=OFF \
  -DBUILD_TESTS=OFF
cmake --build "${POSELIB_DIR}/build" -j"$(nproc)"
cmake --install "${POSELIB_DIR}/build"

rm -rf "${OPENMVS_BUILD_DIR}"
mkdir -p "${OPENMVS_BUILD_DIR}"
cmake -S "${OPENMVS_DIR}" -B "${OPENMVS_BUILD_DIR}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DOpenMVS_USE_CUDA=OFF \
  -DCMAKE_PREFIX_PATH="${TINYEXIF_DIR}/install;${TINYNPY_DIR}/install;${POSELIB_DIR}/install" \
  -DVCG_ROOT="${VCGLIB_DIR}" \
  -DCMAKE_INSTALL_PREFIX="${OPENMVS_DIR}/install"
cmake --build "${OPENMVS_BUILD_DIR}" -j"$(nproc)"
cmake --install "${OPENMVS_BUILD_DIR}"

python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$(dirname "$0")/../requirements.txt"

if [[ ! -d "${SAM2_DIR}" ]]; then
  git clone https://github.com/facebookresearch/sam2.git "${SAM2_DIR}"
else
  git -C "${SAM2_DIR}" pull --ff-only
fi

python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
python -m pip install -e "${SAM2_DIR}"

if [[ -x "${SAM2_DIR}/checkpoints/download_ckpts.sh" ]]; then
  (cd "${SAM2_DIR}" && bash checkpoints/download_ckpts.sh)
fi

cat <<EOF
bootstrap complete
ROOT_DIR=${ROOT_DIR}
VENV_DIR=${VENV_DIR}
OPENMVS_BIN_DIR=${OPENMVS_DIR}/install/bin
SAM2_DIR=${SAM2_DIR}
EOF
