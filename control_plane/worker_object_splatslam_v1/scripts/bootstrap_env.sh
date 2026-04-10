#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/object_splatslam_v1}"
VENV_DIR="${ROOT_DIR}/venv"
THIRD_PARTY_DIR="${ROOT_DIR}/third_party"
SPLATSLAM_DIR="${THIRD_PARTY_DIR}/Splat-SLAM"
SPLATSLAM_REF="${SPLATSLAM_REF:-main}"
CUDA_TOOLKIT_PKG="${OBJECT_SPLATSLAM_CUDA_TOOLKIT_PKG:-cuda-toolkit-12-8}"
CUDA_HOME_DIR="${OBJECT_SPLATSLAM_CUDA_HOME:-/usr/local/cuda-12.8}"
TORCH_INDEX_URL="${OBJECT_SPLATSLAM_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"
INSTALL_OPTIONAL_MESH_EXPORT="${OBJECT_SPLATSLAM_INSTALL_OPTIONAL_MESH_EXPORT:-0}"
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
  ffmpeg python3 python3-venv python3-pip \
  libglib2.0-0 libgl1

apt-get install -y --no-install-recommends "${CUDA_TOOLKIT_PKG}"

if [[ "${INSTALL_OPTIONAL_MESH_EXPORT}" == "1" ]]; then
  apt-get install -y --no-install-recommends \
    colmap \
    libeigen3-dev libboost-program-options-dev libboost-system-dev \
    libboost-filesystem-dev libboost-iostreams-dev libboost-serialization-dev \
    libceres-dev libgoogle-glog-dev libgflags-dev \
    libnanoflann-dev libjxl-dev libtinyxml2-dev \
    libopencv-dev libglew-dev libcgal-dev libgmp-dev libmpfr-dev \
    libatlas-base-dev libsuitesparse-dev qtbase5-dev libqt5opengl5-dev
fi

mkdir -p "${THIRD_PARTY_DIR}"

if [[ ! -d "${SPLATSLAM_DIR}" ]]; then
  git clone --recursive https://github.com/google-research/Splat-SLAM.git "${SPLATSLAM_DIR}"
else
  git -C "${SPLATSLAM_DIR}" fetch --all --tags --force
fi
git -C "${SPLATSLAM_DIR}" checkout -f "${SPLATSLAM_REF}"
git -C "${SPLATSLAM_DIR}" submodule update --init --recursive

python3 - "${SPLATSLAM_DIR}/thirdparty/diff-gaussian-rasterization-w-pose/cuda_rasterizer/auxiliary.h" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = "if (p_view.z <= 0.2f)"
new = "if (p_view.z <= 0.001f)"
if old in text and new not in text:
    path.write_text(text.replace(old, new), encoding="utf-8")
PY

python3 - "${SPLATSLAM_DIR}/thirdparty/diff-gaussian-rasterization-w-pose/cuda_rasterizer/rasterizer_impl.h" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
include = "#include <cstdint>\n"
needle = "#include <iostream>\n"
if include not in text and needle in text:
    path.write_text(text.replace(needle, needle + include, 1), encoding="utf-8")
PY

python3 - \
  "${SPLATSLAM_DIR}/thirdparty/lietorch/lietorch/src/lietorch_cpu.cpp" \
  "${SPLATSLAM_DIR}/thirdparty/lietorch/lietorch/src/lietorch_gpu.cu" \
  "${SPLATSLAM_DIR}/thirdparty/lietorch/lietorch/extras/extras.cpp" \
  "${SPLATSLAM_DIR}/thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu" \
  "${SPLATSLAM_DIR}/thirdparty/glorie_slam/lib/correlation_kernels.cu" \
  "${SPLATSLAM_DIR}/thirdparty/glorie_slam/lib/altcorr_kernel.cu" \
  "${SPLATSLAM_DIR}/src/utils/datasets.py" \
  "${SPLATSLAM_DIR}/thirdparty/lietorch/examples/core/data_readers/rgbd_utils.py" \
  "${SPLATSLAM_DIR}/src/mono_estimators.py" \
  "${SPLATSLAM_DIR}/thirdparty/mono_priors/omnidata/modules/midas/base_model.py" <<'PY'
from pathlib import Path
import sys

for raw in sys.argv[1:]:
    path = Path(raw)
    text = path.read_text(encoding="utf-8")
    updated = text.replace(".type().is_cuda()", ".is_cuda()")
    updated = updated.replace(".type()", ".scalar_type()")
    updated = updated.replace("np.unicode_", "np.str_")
    updated = updated.replace("torch.load(pretrained_path)", "torch.load(pretrained_path, weights_only=False)")
    updated = updated.replace(
        "torch.load(path, map_location=torch.device('cpu'))",
        "torch.load(path, map_location=torch.device('cpu'), weights_only=False)",
    )
    if updated != text:
        path.write_text(updated, encoding="utf-8")
PY

python3 - "${SPLATSLAM_DIR}/thirdparty/simple-knn/simple_knn.cu" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
include = "#include <cfloat>\n"
needle = "#include <vector>\n"
if include not in text and needle in text:
    path.write_text(text.replace(needle, needle + include, 1), encoding="utf-8")
PY

python3 - "${SPLATSLAM_DIR}/thirdparty/simple-knn/simple_knn/__init__.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
if not path.exists():
    path.write_text("from ._C import *  # noqa: F401,F403\\n", encoding="utf-8")
PY

python3 - "${SPLATSLAM_DIR}/torch_scatter/__init__.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(
    """import torch


def _expand_index(index: torch.Tensor, src: torch.Tensor, dim: int) -> torch.Tensor:
    while index.dim() < src.dim():
        index = index.unsqueeze(-1)
    return index.expand_as(src)


def scatter_sum(src: torch.Tensor, index: torch.Tensor, dim: int = -1, dim_size: int | None = None) -> torch.Tensor:
    if dim < 0:
        dim += src.dim()
    if dim_size is None:
        dim_size = int(index.max().item()) + 1 if index.numel() > 0 else 0
    out_shape = list(src.shape)
    out_shape[dim] = dim_size
    out = torch.zeros(out_shape, dtype=src.dtype, device=src.device)
    out.scatter_add_(dim, _expand_index(index, src, dim), src)
    return out


def scatter_mean(src: torch.Tensor, index: torch.Tensor, dim: int = -1, dim_size: int | None = None) -> torch.Tensor:
    summed = scatter_sum(src, index, dim=dim, dim_size=dim_size)
    counts = scatter_sum(torch.ones_like(src, dtype=summed.dtype), index, dim=dim, dim_size=summed.shape[dim])
    return summed / counts.clamp_min(1)
""",
    encoding="utf-8",
)
PY

python3 - "${SPLATSLAM_DIR}/setup.py" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
old = """                    '-gencode=arch=compute_86,code=sm_86',
                ]
            }),
"""
new = """                    '-gencode=arch=compute_86,code=sm_86',
                    '-gencode=arch=compute_120,code=compute_120',
                    '-gencode=arch=compute_120,code=sm_120',
                ]
            }),
"""
if old in text and new not in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
export CUDA_HOME="${CUDA_HOME_DIR}"
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"
python -m pip install --upgrade pip setuptools wheel
python -m pip install --upgrade --force-reinstall torch torchvision torchaudio --index-url "${TORCH_INDEX_URL}"
python -m pip install --no-build-isolation -e "${SPLATSLAM_DIR}/thirdparty/lietorch"
python -m pip install --no-build-isolation -e "${SPLATSLAM_DIR}/thirdparty/diff-gaussian-rasterization-w-pose"
python -m pip install --no-build-isolation -e "${SPLATSLAM_DIR}/thirdparty/simple-knn"
python -m pip install --no-build-isolation -e "${SPLATSLAM_DIR}/thirdparty/evaluate_3d_reconstruction_lib"
python -m pip install --no-build-isolation -e "${SPLATSLAM_DIR}"
python -m pip install -r "${SPLATSLAM_DIR}/requirements.txt"
python -m pip install pytorch-lightning==1.9 --no-deps gdown
python -m pip install -r "$(dirname "$0")/../requirements.txt"

if [[ -x "${SPLATSLAM_DIR}/scripts/download_pretrained_model.sh" ]]; then
  mkdir -p "${SPLATSLAM_DIR}/pretrained"
  (
    cd "${SPLATSLAM_DIR}" && \
    MODEL_DIR="${SPLATSLAM_DIR}/pretrained" bash "${SPLATSLAM_DIR}/scripts/download_pretrained_model.sh" true
  )
fi

if [[ ! -d "${SAM2_DIR}" ]]; then
  git clone https://github.com/facebookresearch/sam2.git "${SAM2_DIR}"
else
  git -C "${SAM2_DIR}" pull --ff-only
fi
python -m pip install -e "${SAM2_DIR}"
if [[ -x "${SAM2_DIR}/checkpoints/download_ckpts.sh" ]]; then
  (cd "${SAM2_DIR}" && bash checkpoints/download_ckpts.sh)
fi

if [[ "${INSTALL_OPTIONAL_MESH_EXPORT}" == "1" ]]; then
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
fi

cat <<EOF
bootstrap complete
ROOT_DIR=${ROOT_DIR}
VENV_DIR=${VENV_DIR}
SPLATSLAM_DIR=${SPLATSLAM_DIR}
CUDA_HOME=${CUDA_HOME}
SAM2_DIR=${SAM2_DIR}
OPENMVS_BIN_DIR=${OPENMVS_DIR}/install/bin
EOF
