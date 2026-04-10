#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/object_slam3r_surface_v1}"
MATCHA_DIR="${ROOT_DIR}/third_party/MAtCha"
PYTHON_BIN="${2:-${OBJECT_SLAM3R_SURFACE_MATCHA_PYTHON_BIN:-${ROOT_DIR}/envs/sugar-adapted/bin/python}}"
PIP_BIN="$(cd "$(dirname "${PYTHON_BIN}")" && pwd)/pip"
CUDA_HOME="${OBJECT_SLAM3R_SURFACE_CUDA_HOME:-/usr/local/cuda-12.8}"

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
  open3d==0.18.0 \
  fvcore \
  iopath

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
export Torch_DIR="${CONDA_PREFIX}/lib/python3.10/site-packages/torch/share/cmake/Torch"
cmake -DCMAKE_CXX_FLAGS="-D_GLIBCXX_USE_CXX11_ABI=1" -DCMAKE_CUDA_FLAGS="-D_GLIBCXX_USE_CXX11_ABI=1" .
make -j2
"${PIP_BIN}" install -e . --no-build-isolation
popd >/dev/null

echo "MATCHA_RUNTIME_READY"
