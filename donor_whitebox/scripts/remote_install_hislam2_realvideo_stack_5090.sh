#!/usr/bin/env bash
set -euo pipefail

MINIFORGE_ROOT="${MINIFORGE_ROOT:-/opt/miniforge3}"
CONDA_BIN="${CONDA_BIN:-${MINIFORGE_ROOT}/bin/conda}"
HI_REPO="${HI_REPO:-/root/gs_refs/HI-SLAM2}"
HI_ENV_PREFIX="${HI_ENV_PREFIX:-/venv/hislam2}"
UNI_SETUP_SCRIPT="${UNI_SETUP_SCRIPT:-/root/donor_whitebox/scripts/remote_setup_unik3d_runtime.sh}"
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda}"
CC_BIN="${CC_BIN:-/usr/bin/gcc}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++}"
MAX_JOBS="${MAX_JOBS:-12}"
PRETRAINED_DIR="${PRETRAINED_DIR:-${HI_REPO}/pretrained_models}"
TORCH_VER="${TORCH_VER:-2.10.0}"
TORCHVISION_VER="${TORCHVISION_VER:-0.25.0}"
TORCHAUDIO_VER="${TORCHAUDIO_VER:-2.10.0}"
NUMPY_VER="${NUMPY_VER:-1.26.4}"
SETUPTOOLS_VER="${SETUPTOOLS_VER:-69.5.1}"
PATCH_SCATTER_SCRIPT="${PATCH_SCATTER_SCRIPT:-/root/donor_whitebox/scripts/remote_patch_torch_scatter_cuda_guard.sh}"
PATCH_TORCH_LOADS_SCRIPT="${PATCH_TORCH_LOADS_SCRIPT:-/root/donor_whitebox/scripts/remote_patch_hislam2_torch_loads.py}"
SAM2_REPO="${SAM2_REPO:-/root/donor_whitebox/third_party/sam2}"
SAM2_COMMIT="${SAM2_COMMIT:-2b90b9f5ceec907a1c18123530e92e794ad901a4}"
SAM2_CHECKPOINT_DIR="${SAM2_CHECKPOINT_DIR:-${SAM2_REPO}/checkpoints}"
SAM2_CHECKPOINT="${SAM2_CHECKPOINT:-${SAM2_CHECKPOINT_DIR}/sam2.1_hiera_large.pt}"

export CUDA_HOME="${CUDA_ROOT}"
export CUDACXX="${CUDA_ROOT}/bin/nvcc"
export CUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}"
export CUDAToolkit_ROOT="${CUDA_ROOT}"
export PATH="${CUDA_ROOT}/bin:${PATH}"
export CC="${CC_BIN}"
export CXX="${CXX_BIN}"
export CUDAHOSTCXX="${CXX_BIN}"
export MAX_JOBS

if [[ ! -x "${CUDA_ROOT}/bin/nvcc" ]]; then
  if command -v nvcc >/dev/null 2>&1; then
    CUDA_ROOT="$(cd "$(dirname "$(command -v nvcc)")/.." && pwd)"
  elif [[ -x /usr/local/cuda-12.9/bin/nvcc ]]; then
    CUDA_ROOT="/usr/local/cuda-12.9"
  elif [[ -x /usr/local/cuda-12/bin/nvcc ]]; then
    CUDA_ROOT="/usr/local/cuda-12"
  fi
  export CUDA_HOME="${CUDA_ROOT}"
  export CUDACXX="${CUDA_ROOT}/bin/nvcc"
  export CUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}"
  export CUDAToolkit_ROOT="${CUDA_ROOT}"
  export PATH="${CUDA_ROOT}/bin:${PATH}"
fi

if [[ ! -x "${CONDA_BIN}" ]]; then
  echo "[install-hislam2-5090] missing conda at ${CONDA_BIN}" >&2
  exit 1
fi

mkdir -p "${PRETRAINED_DIR}"
if [[ ! -f "${PRETRAINED_DIR}/droid.pth" ]]; then
  echo "[install-hislam2-5090] missing required official weight ${PRETRAINED_DIR}/droid.pth" >&2
  exit 9
fi
if [[ ! -f "${PRETRAINED_DIR}/omnidata_dpt_depth_v2.ckpt" ]]; then
  curl -L "https://zenodo.org/records/10447888/files/omnidata_dpt_depth_v2.ckpt" -o "${PRETRAINED_DIR}/omnidata_dpt_depth_v2.ckpt"
fi
if [[ ! -f "${PRETRAINED_DIR}/omnidata_dpt_normal_v2.ckpt" ]]; then
  curl -L "https://zenodo.org/records/10447888/files/omnidata_dpt_normal_v2.ckpt" -o "${PRETRAINED_DIR}/omnidata_dpt_normal_v2.ckpt"
fi

if [[ ! -x "${HI_ENV_PREFIX}/bin/python" ]]; then
  TMP_ENV_YAML="$(mktemp /tmp/hislam2_env.XXXXXX.yml)"
  awk '
    /^  - pip:/ {skip=1; next}
    skip && /^    - / {next}
    {skip=0; print}
  ' "${HI_REPO}/environment.yaml" > "${TMP_ENV_YAML}"
  "${CONDA_BIN}" env create --environment-spec environment.yml -p "${HI_ENV_PREFIX}" -f "${TMP_ENV_YAML}"
  rm -f "${TMP_ENV_YAML}"
fi

TORCH_LIB_DIR="$("${HI_ENV_PREFIX}/bin/python" - <<'PY'
import pathlib, torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
VENV_LIB_DIR="$(cd "$(dirname "${HI_ENV_PREFIX}/bin/python")/.." && pwd)/lib"
CUDA_CUDART_SO="${CUDA_CUDART_SO:-${CUDA_ROOT}/lib64/libcudart.so.13.1.80}"
export LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${VENV_LIB_DIR}:${CUDA_ROOT}/lib64:${LD_LIBRARY_PATH:-}"
if [[ -f "${CUDA_CUDART_SO}" ]]; then
  export LD_PRELOAD="${CUDA_CUDART_SO}:${LD_PRELOAD:-}"
fi

"${CONDA_BIN}" remove -p "${HI_ENV_PREFIX}" -y pytorch torchvision torchaudio pytorch-cuda cudatoolkit >/dev/null 2>&1 || true
"${HI_ENV_PREFIX}/bin/pip" uninstall -y torch torchvision torchaudio torch-scatter diff-gaussian-rasterization simple-knn lietorch droid_backends >/dev/null 2>&1 || true

"${HI_ENV_PREFIX}/bin/pip" install --upgrade pip "setuptools<81" wheel
"${HI_ENV_PREFIX}/bin/pip" install \
  "torch==${TORCH_VER}" \
  "torchvision==${TORCHVISION_VER}" \
  "torchaudio==${TORCHAUDIO_VER}" \
  "numpy==${NUMPY_VER}" \
  "setuptools==${SETUPTOOLS_VER}" \
  "pyrender" \
  "imgviz" \
  "open3d" \
  "evo" \
  "munch" \
  "plyfile" \
  "rich" \
  "glfw" \
  "glm" \
  "timm" \
  "torchmetrics" \
  "future>=0.17.1" \
  "pyDeprecate==0.3.1" \
  "tensorboard>=2.2.0"
"${HI_ENV_PREFIX}/bin/pip" install --no-deps "pytorch-lightning==1.5.10.post0"
"${HI_ENV_PREFIX}/bin/pip" install "git+https://github.com/eriksandstroem/evaluate_3d_reconstruction_lib.git"

python3 - <<'PY'
from pathlib import Path
import re

setup_py = Path("/root/gs_refs/HI-SLAM2/setup.py")
text = setup_py.read_text()
original = text
dispatch_h = Path("/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/include/dispatch.h")
rasterizer_impl_h = Path("/root/gs_refs/HI-SLAM2/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h")

include_old = """include_dirs=[osp.join(ROOT, 'thirdparty/eigen')],"""
include_new = """include_dirs=[osp.join(ROOT, 'thirdparty/eigen'), '/usr/include/eigen3', '/usr/local/cuda/include'],"""
text = text.replace(include_old, include_new, 1)

lietorch_old = """include_dirs=[
                osp.join(ROOT, 'thirdparty/lietorch/lietorch/include'), 
                osp.join(ROOT, 'thirdparty/eigen')],"""
lietorch_new = """include_dirs=[
                osp.join(ROOT, 'thirdparty/lietorch/lietorch/include'),
                osp.join(ROOT, 'thirdparty/eigen'),
                '/usr/include/eigen3',
                '/usr/local/cuda/include'],"""
text = text.replace(lietorch_old, lietorch_new, 1)

droid_old_flags = """                'nvcc': ['-O3',
                    '-gencode=arch=compute_60,code=sm_60',
                    '-gencode=arch=compute_61,code=sm_61',
                    '-gencode=arch=compute_70,code=sm_70',
                    '-gencode=arch=compute_75,code=sm_75',
                    '-gencode=arch=compute_80,code=sm_80',
                    '-gencode=arch=compute_86,code=sm_86',
                ]"""
droid_new_flags = """                'nvcc': ['-O3',
                    '-gencode=arch=compute_120,code=sm_120',
                    '-gencode=arch=compute_120,code=compute_120',
                ]"""
text = text.replace(droid_old_flags, droid_new_flags, 1)

old_lietorch_flags = """                'nvcc': ['-O2',
                    '-gencode=arch=compute_60,code=sm_60', 
                    '-gencode=arch=compute_61,code=sm_61', 
                    '-gencode=arch=compute_70,code=sm_70', 
                    '-gencode=arch=compute_75,code=sm_75',
                    '-gencode=arch=compute_80,code=sm_80',
                    '-gencode=arch=compute_86,code=sm_86',                 
                ]"""
new_lietorch_flags = """                'nvcc': ['-O2',
                    '-gencode=arch=compute_120,code=sm_120',
                    '-gencode=arch=compute_120,code=compute_120',
                ]"""
text = text.replace(old_lietorch_flags, new_lietorch_flags, 1)
text = re.sub(
    r"(name='droid_backends'.*?'nvcc': \['-O3',\n)(.*?)(\n\s+\]\n\s+\}\),)",
    r"\1                    '-gencode=arch=compute_120,code=sm_120',\n"
    r"                    '-gencode=arch=compute_120,code=compute_120',\3",
    text,
    count=1,
    flags=re.S,
)
text = re.sub(
    r"(name='lietorch'.*?'nvcc': \['-O2',\n)(.*?)(\n\s+\]\n\s+\}\),)",
    r"\1                    '-gencode=arch=compute_120,code=sm_120',\n"
    r"                    '-gencode=arch=compute_120,code=compute_120',\3",
    text,
    count=1,
    flags=re.S,
)

if text != original:
    setup_py.write_text(text)
    print(f"[install-hislam2-5090] patched {setup_py}")
else:
    print(f"[install-hislam2-5090] setup.py already patched")

if rasterizer_impl_h.is_file():
    header_text = rasterizer_impl_h.read_text()
    header_original = header_text
    if "#include <cstddef>" not in header_text:
        header_text = header_text.replace("#pragma once\n\n", "#pragma once\n\n#include <cstddef>\n", 1)
    if "#include <cstdint>" not in header_text:
        header_text = header_text.replace("#include <cstddef>\n", "#include <cstddef>\n#include <cstdint>\n", 1)
    if header_text != header_original:
        rasterizer_impl_h.write_text(header_text)
        print(f"[install-hislam2-5090] patched {rasterizer_impl_h}")
    else:
        print(f"[install-hislam2-5090] rasterizer_impl.h already patched")

patched_dispatch = """#ifndef DISPATCH_H
#define DISPATCH_H

#include <torch/extension.h>

#include "so3.h"
#include "rxso3.h"
#include "se3.h"
#include "sim3.h"

inline at::ScalarType lietorch_scalar_type(at::ScalarType t) {
  return t;
}

inline at::ScalarType lietorch_scalar_type(const at::Tensor& t) {
  return t.scalar_type();
}

inline at::ScalarType lietorch_scalar_type(const at::DeprecatedTypeProperties& t) {
  return t.scalarType();
}

#define PRIVATE_CASE_TYPE(group_index, enum_type, type, ...)    \\
  case enum_type: {                                             \\
    using scalar_t = type;                                      \\
    switch (group_index) {                                      \\
      case 1: {                                                 \\
        using group_t = SO3<type>;                              \\
        return __VA_ARGS__();                                   \\
      }                                                         \\
      case 2: {                                                 \\
        using group_t = RxSO3<type>;                            \\
        return __VA_ARGS__();                                   \\
      }                                                         \\
      case 3: {                                                 \\
        using group_t = SE3<type>;                              \\
        return __VA_ARGS__();                                   \\
      }                                                         \\
      case 4: {                                                 \\
        using group_t = Sim3<type>;                             \\
        return __VA_ARGS__();                                   \\
      }                                                         \\
    }                                                           \\
  }                                                             \\

#define DISPATCH_GROUP_AND_FLOATING_TYPES(GROUP_INDEX, TYPE, NAME, ...)              \\
  [&] {                                                                              \\
    const auto& the_type = TYPE;                                                     \\
    /* don't use TYPE again in case it is an expensive or side-effect op */          \\
    at::ScalarType _st = lietorch_scalar_type(the_type);                             \\
    switch (_st) {                                                                   \\
      PRIVATE_CASE_TYPE(GROUP_INDEX, at::ScalarType::Double, double, __VA_ARGS__)    \\
      PRIVATE_CASE_TYPE(GROUP_INDEX, at::ScalarType::Float, float, __VA_ARGS__)      \\
      default: break;                                                                \\
    }                                                                                \\
  }()

#endif
"""
if dispatch_h.exists():
    dispatch_text = dispatch_h.read_text()
    if "inline at::ScalarType lietorch_scalar_type" not in dispatch_text:
        dispatch_h.write_text(patched_dispatch)
        print(f"[install-hislam2-5090] patched {dispatch_h}")
PY

if [[ -f "${PATCH_TORCH_LOADS_SCRIPT}" ]]; then
  python3 "${PATCH_TORCH_LOADS_SCRIPT}"
fi

PATCH_ROOT="$(mktemp -d)"
cat > "${PATCH_ROOT}/sitecustomize.py" <<'PY'
import os
import torch.utils.cpp_extension as ce

_orig_get_cuda_arch_flags = ce._get_cuda_arch_flags

def _patched_get_cuda_arch_flags(cflags=None):
    try:
        return _orig_get_cuda_arch_flags(cflags)
    except ValueError as exc:
        arch_env = os.environ.get("TORCH_CUDA_ARCH_LIST", "").replace(" ", ";")
        if "12.0" not in arch_env:
            raise
        flags = []
        for arch in [x for x in arch_env.split(";") if x]:
            if arch == "12.0":
                flags.append("-gencode=arch=compute_120,code=sm_120")
            elif arch == "12.0+PTX":
                flags.extend([
                    "-gencode=arch=compute_120,code=sm_120",
                    "-gencode=arch=compute_120,code=compute_120",
                ])
            else:
                raise exc
        return sorted(set(flags))

ce._get_cuda_arch_flags = _patched_get_cuda_arch_flags
ce._check_cuda_version = lambda *args, **kwargs: None
PY

export PYTHONPATH="${PATCH_ROOT}:${PYTHONPATH:-}"
export TORCH_CUDA_ARCH_LIST="12.0+PTX"

"${HI_ENV_PREFIX}/bin/pip" install --no-cache-dir --no-build-isolation --no-binary=torch-scatter torch-scatter==2.1.2
if [[ -x "${PATCH_SCATTER_SCRIPT}" ]]; then
  "${PATCH_SCATTER_SCRIPT}"
fi

for pkg_dir in \
  "${HI_REPO}/thirdparty/simple-knn" \
  "${HI_REPO}/thirdparty/diff-gaussian-rasterization"
do
  (
    cd "${pkg_dir}"
    rm -rf build *.egg-info
    "${HI_ENV_PREFIX}/bin/python" setup.py install
  )
done

(
  cd "${HI_REPO}"
  rm -rf build *.egg-info thirdparty/lietorch/build
  "${HI_ENV_PREFIX}/bin/python" setup.py install
)

"${HI_ENV_PREFIX}/bin/python" - <<'PY'
import importlib
mods = [
    "torch",
    "torch_scatter",
    "cv2",
    "droid_backends",
    "lietorch",
    "lietorch_backends",
    "simple_knn",
    "diff_gaussian_rasterization",
]
for name in mods:
    module = importlib.import_module(name)
    print(f"[install-hislam2-5090] import_ok {name} -> {getattr(module, '__file__', 'builtin')}")
PY

mkdir -p "$(dirname "${SAM2_REPO}")"
if [[ ! -d "${SAM2_REPO}/.git" ]]; then
  rm -rf "${SAM2_REPO}"
  git clone https://github.com/facebookresearch/sam2.git "${SAM2_REPO}"
fi
(
  cd "${SAM2_REPO}"
  git fetch --all --tags
  git checkout "${SAM2_COMMIT}"
)
"${HI_ENV_PREFIX}/bin/pip" install --upgrade "hydra-core>=1.3" "iopath>=0.1.10" "huggingface_hub>=0.26.0"
SAM2_BUILD_CUDA=0 "${HI_ENV_PREFIX}/bin/pip" install -e "${SAM2_REPO}"
mkdir -p "${SAM2_CHECKPOINT_DIR}"
if [[ ! -f "${SAM2_CHECKPOINT}" ]]; then
  curl -L "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt" -o "${SAM2_CHECKPOINT}"
fi
"${HI_ENV_PREFIX}/bin/python" - <<'PY'
from pathlib import Path

from sam2.sam2_image_predictor import SAM2ImagePredictor
from sam2.sam2_video_predictor import SAM2VideoPredictor

checkpoint = Path("/root/donor_whitebox/third_party/sam2/checkpoints/sam2.1_hiera_large.pt")
print("[install-hislam2-5090] sam2_checkpoint", checkpoint, checkpoint.exists())
print("[install-hislam2-5090] sam2_image_predictor", SAM2ImagePredictor)
print("[install-hislam2-5090] sam2_video_predictor", SAM2VideoPredictor)
PY

rm -rf "${PATCH_ROOT}"
unset PYTHONPATH
unset TORCH_CUDA_ARCH_LIST

if [[ -x "${UNI_SETUP_SCRIPT}" ]]; then
  bash "${UNI_SETUP_SCRIPT}"
fi

"${CONDA_BIN}" clean -a -y >/dev/null 2>&1 || true
echo "[install-hislam2-5090] complete"
