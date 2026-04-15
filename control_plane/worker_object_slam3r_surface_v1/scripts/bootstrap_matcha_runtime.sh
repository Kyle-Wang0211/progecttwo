#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
ROOT_DIR="${1:-${DEFAULT_ROOT_DIR}}"
MATCHA_DIR="${ROOT_DIR}/third_party/MAtCha"
PYTHON_BIN="${2:-${OBJECT_SLAM3R_SURFACE_MATCHA_PYTHON_BIN:-${ROOT_DIR}/envs/sugar-adapted/bin/python}}"
PIP_BIN="$(cd "$(dirname "${PYTHON_BIN}")" && pwd)/pip"
CUDA_HOME="${OBJECT_SLAM3R_SURFACE_CUDA_HOME:-${CUDA_HOME:-/usr/local/cuda}}"
TORCH_BUILD_SHIM_DIR="${ROOT_DIR}/local/torch_build_shim"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "matcha_python_missing: ${PYTHON_BIN}" >&2
  exit 2
fi
if [[ ! -d "${MATCHA_DIR}" ]]; then
  echo "matcha_repo_missing: ${MATCHA_DIR}" >&2
  exit 2
fi

export PATH="$(cd "$(dirname "${PYTHON_BIN}")" && pwd):${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"
export CPATH="${CUDA_HOME}/targets/x86_64-linux/include:${CPATH:-}"
export CUDA_HOME
export CUDA_PATH="${CUDA_HOME}"
export CUDAToolkit_ROOT="${CUDA_HOME}"
export CUDACXX="${CUDA_HOME}/bin/nvcc"
export TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-12.0}"

mkdir -p "${TORCH_BUILD_SHIM_DIR}"
cat > "${TORCH_BUILD_SHIM_DIR}/sitecustomize.py" <<'PY'
try:
    import torch.utils.cpp_extension as _ce
except Exception:
    _ce = None

if _ce is not None:
    def _ignore_cuda_version(*args, **kwargs):
        return None

    _ce._check_cuda_version = _ignore_cuda_version
PY
export PYTHONPATH="${TORCH_BUILD_SHIM_DIR}:${PYTHONPATH:-}"

OPEN3D_VERSION="$("${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info >= (3, 12):
    print("0.19.0")
else:
    print("0.18.0")
PY
)"

patch_if_missing() {
  local needle="$1"
  local file="$2"
  local header="$3"
  if ! grep -q "${needle}" "${file}"; then
    python3 - <<'PY' "${file}" "${header}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
header = sys.argv[2]
text = path.read_text()
path.write_text(header + "\n" + text)
PY
  fi
}

patch_if_missing "cstdint" \
  "${MATCHA_DIR}/2d-gaussian-splatting/submodules/diff-surfel-rasterization/cuda_rasterizer/rasterizer_impl.h" \
  "#include <cstdint>\n#include <cstddef>"

patch_if_missing "cfloat" \
  "${MATCHA_DIR}/2d-gaussian-splatting/submodules/simple-knn/simple_knn.cu" \
  "#include <cfloat>"

if [[ ! -f "${MATCHA_DIR}/2d-gaussian-splatting/submodules/simple-knn/simple_knn/__init__.py" ]]; then
  cat > "${MATCHA_DIR}/2d-gaussian-splatting/submodules/simple-knn/simple_knn/__init__.py" <<'PY'
"""Python package shim for the compiled simple_knn extension."""
PY
fi

python3 - <<'PY' "${MATCHA_DIR}/2d-gaussian-splatting/submodules/tetra-triangulation/CMakeLists.txt"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
marker = "find_package(Torch REQUIRED)\n"
patch = (
    "find_package(Torch REQUIRED)\n"
    "string(REPLACE \"-D_GLIBCXX_USE_CXX11_ABI=0\" \"-D_GLIBCXX_USE_CXX11_ABI=1\" TORCH_CXX_FLAGS \"${TORCH_CXX_FLAGS}\")\n"
)
if patch not in text and marker in text:
    text = text.replace(marker, patch, 1)
    path.write_text(text)
PY

python3 - <<'PY' "${MATCHA_DIR}/2d-gaussian-splatting/submodules/tetra-triangulation/cmake/FindTorch.cmake"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
needle = '-D_GLIBCXX_USE_CXX11_ABI=0'
if needle in text:
    text = text.replace(needle, '-D_GLIBCXX_USE_CXX11_ABI=1')
    path.write_text(text)
PY

"${PIP_BIN}" install --upgrade pip setuptools wheel
"${PIP_BIN}" install \
  importlib_metadata \
  rich==13.9.4 \
  pyyaml==6.0.2 \
  trimesh==4.6.4 \
  scipy==1.13.1 \
  einops==0.8.1 \
  opencv-python==4.11.0.86 \
  plyfile==0.8.1 \
  scikit-learn==1.6.1 \
  cython==3.0.12 \
  tqdm==4.67.1 \
  matplotlib==3.9.4 \
  roma==1.5.0 \
  scikit-image \
  "open3d==${OPEN3D_VERSION}" \
  fvcore \
  iopath

if ! "${PYTHON_BIN}" - <<'PY' >/dev/null 2>&1
import pytorch3d  # noqa: F401
PY
then
  "${PIP_BIN}" install ninja
  export CUB_HOME="${CUB_HOME:-${CUDA_HOME}/include}"
  PYTORCH3D_BUILD_DIR="$(mktemp -d)"
  cleanup_pytorch3d_build_dir() {
    rm -rf "${PYTORCH3D_BUILD_DIR}"
  }
  trap cleanup_pytorch3d_build_dir EXIT
  git clone --depth 1 https://github.com/facebookresearch/pytorch3d.git "${PYTORCH3D_BUILD_DIR}/pytorch3d"
  rm -rf \
    "${PYTORCH3D_BUILD_DIR}/pytorch3d/pytorch3d/csrc/pulsar" \
    "${PYTORCH3D_BUILD_DIR}/pytorch3d/pytorch3d/renderer/points/pulsar"
  python3 - <<'PY' "${PYTORCH3D_BUILD_DIR}/pytorch3d"
from pathlib import Path
import sys

root = Path(sys.argv[1])

ext_cpp = root / "pytorch3d" / "csrc" / "ext.cpp"
text = ext_cpp.read_text()
text = text.replace('#include "./pulsar/global.h" // Include before <torch/extension.h>.\n', "")
text = text.replace('#include "./pulsar/pytorch/renderer.h"\n', "")
text = text.replace('#include "./pulsar/pytorch/tensor_util.h"\n', "")
pulsar_marker = "  // Pulsar.\n"
if pulsar_marker in text:
    text = text.split(pulsar_marker, 1)[0].rstrip() + "\n}\n"
ext_cpp.write_text(text)

points_init = root / "pytorch3d" / "renderer" / "points" / "__init__.py"
points_text = points_init.read_text()
points_text = points_text.replace(
    "from .pulsar.unified import PulsarPointsRenderer\n",
    "try:\n"
    "    from .pulsar.unified import PulsarPointsRenderer\n"
    "except Exception:\n"
    "    PulsarPointsRenderer = None\n",
)
points_init.write_text(points_text)
PY
  export MAX_JOBS="${OBJECT_SLAM3R_SURFACE_PYTORCH3D_MAX_JOBS:-1}"
  "${PIP_BIN}" install "${PYTORCH3D_BUILD_DIR}/pytorch3d" --no-build-isolation
  trap - EXIT
  cleanup_pytorch3d_build_dir
fi

pushd "${MATCHA_DIR}/2d-gaussian-splatting/submodules/diff-surfel-rasterization" >/dev/null
"${PIP_BIN}" install -e . --no-build-isolation
popd >/dev/null

pushd "${MATCHA_DIR}/2d-gaussian-splatting/submodules/simple-knn" >/dev/null
"${PIP_BIN}" install -e . --no-build-isolation
popd >/dev/null

pushd "${MATCHA_DIR}/2d-gaussian-splatting/submodules/tetra-triangulation" >/dev/null
export TMPDIR="${ROOT_DIR}/tmp_build"
mkdir -p "${TMPDIR}"
rm -rf CMakeCache.txt CMakeFiles build bin lib tetranerf/utils/extension/tetranerf_cpp_extension*.so
export CONDA_PREFIX="$(cd "$(dirname "${PYTHON_BIN}")/.." && pwd)"
export CMAKE_PREFIX_PATH="$("${PYTHON_BIN}" - <<'PY'
import torch

print(torch.utils.cmake_prefix_path)
PY
)"
export Torch_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib
import torch

print(pathlib.Path(torch.__file__).resolve().parent / "share" / "cmake" / "Torch")
PY
)"
cmake -DCMAKE_CXX_FLAGS="-D_GLIBCXX_USE_CXX11_ABI=1" -DCMAKE_CUDA_FLAGS="-D_GLIBCXX_USE_CXX11_ABI=1" .
make -j2
"${PIP_BIN}" install -e . --no-build-isolation
popd >/dev/null

echo "MATCHA_RUNTIME_READY"
