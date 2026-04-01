#!/usr/bin/env bash
set -euo pipefail

HI_REPO="${HI_REPO:-/root/gs_refs/HI-SLAM2}"
HI_ENV_PREFIX="${HI_ENV_PREFIX:-/venv/hislam2}"
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda}"
CC_BIN="${CC_BIN:-/usr/bin/gcc}"
CXX_BIN="${CXX_BIN:-/usr/bin/g++}"
MAX_JOBS="${MAX_JOBS:-12}"
SKIP_DIFF="${SKIP_DIFF:-0}"
REBUILD_SCATTER="${REBUILD_SCATTER:-1}"
PATCH_SCATTER_GUARD="${PATCH_SCATTER_GUARD:-1}"
PATCH_SCATTER_SCRIPT="${PATCH_SCATTER_SCRIPT:-/root/donor_whitebox/scripts/remote_patch_torch_scatter_cuda_guard.sh}"
PATCH_TORCH_LOADS_SCRIPT="${PATCH_TORCH_LOADS_SCRIPT:-/root/donor_whitebox/scripts/remote_patch_hislam2_torch_loads.py}"

LOG="${LOG:-/root/donor_whitebox/logs/rebuild_hislam2_after_torch210_$(date +%Y%m%d_%H%M%S).log}"
PATCH_ROOT="$(mktemp -d)"
trap 'rm -rf "${PATCH_ROOT}"' EXIT

cat > "${PATCH_ROOT}/sitecustomize.py" <<'PY'
import torch.utils.cpp_extension as ce

_orig_get_cuda_arch_flags = ce._get_cuda_arch_flags

def _patched_get_cuda_arch_flags(cflags=None):
    try:
        return _orig_get_cuda_arch_flags(cflags)
    except ValueError as exc:
        import os
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
export CUDA_HOME="${CUDA_ROOT}"
export CUDACXX="${CUDA_ROOT}/bin/nvcc"
export CUDA_TOOLKIT_ROOT_DIR="${CUDA_ROOT}"
export CUDAToolkit_ROOT="${CUDA_ROOT}"
export PATH="${CUDA_ROOT}/bin:${PATH}"
export CC="${CC_BIN}"
export CXX="${CXX_BIN}"
export CUDAHOSTCXX="${CXX_BIN}"
export MAX_JOBS

python3 - "${HI_REPO}" <<'PY'
from pathlib import Path
import re
import sys

repo = Path(sys.argv[1])
setup_py = repo / "setup.py"
rasterizer_impl_h = repo / "thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h"
if setup_py.is_file():
    text = setup_py.read_text()
    original = text

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

    lietorch_old_flags = """                'nvcc': ['-O2',
                    '-gencode=arch=compute_60,code=sm_60', 
                    '-gencode=arch=compute_61,code=sm_61', 
                    '-gencode=arch=compute_70,code=sm_70', 
                    '-gencode=arch=compute_75,code=sm_75',
                    '-gencode=arch=compute_80,code=sm_80',
                    '-gencode=arch=compute_86,code=sm_86',                 
                ]"""
    lietorch_new_flags = """                'nvcc': ['-O2',
                    '-gencode=arch=compute_120,code=sm_120',
                    '-gencode=arch=compute_120,code=compute_120',
                ]"""
    text = text.replace(lietorch_old_flags, lietorch_new_flags, 1)

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
        print(f"[rebuild] patched {setup_py}")

if rasterizer_impl_h.is_file():
    text = rasterizer_impl_h.read_text()
    original = text
    if "#include <cstddef>" not in text:
        text = text.replace("#pragma once\n\n", "#pragma once\n\n#include <cstddef>\n", 1)
    if "#include <cstdint>" not in text:
        text = text.replace("#include <cstddef>\n", "#include <cstddef>\n#include <cstdint>\n", 1)
    if text != original:
        rasterizer_impl_h.write_text(text)
        print(f"[rebuild] patched {rasterizer_impl_h}")
PY

if [[ -f "${PATCH_TORCH_LOADS_SCRIPT}" ]]; then
  python3 "${PATCH_TORCH_LOADS_SCRIPT}"
fi

{
  echo "[rebuild] start $(date -Is)"

  if [[ "${REBUILD_SCATTER}" == "1" ]]; then
    "${HI_ENV_PREFIX}/bin/pip" uninstall -y torch-scatter >/dev/null 2>&1 || true
    "${HI_ENV_PREFIX}/bin/pip" install --no-cache-dir --no-build-isolation --no-binary=torch-scatter torch-scatter==2.1.2
    if [[ "${PATCH_SCATTER_GUARD}" == "1" && -x "${PATCH_SCATTER_SCRIPT}" ]]; then
      "${PATCH_SCATTER_SCRIPT}"
    fi
  else
    echo "[rebuild] skipping torch-scatter"
  fi

  if [[ "${SKIP_DIFF}" != "1" ]]; then
    cd "${HI_REPO}/thirdparty/diff-gaussian-rasterization"
    rm -rf build *.egg-info
    "${HI_ENV_PREFIX}/bin/python" setup.py install
  else
    echo "[rebuild] skipping diff-gaussian-rasterization"
  fi

  cd "${HI_REPO}"
  rm -rf build *.egg-info thirdparty/lietorch/build
  "${HI_ENV_PREFIX}/bin/python" setup.py install

  "${HI_ENV_PREFIX}/bin/python" - <<'PY'
import importlib
import torch

print("torch", torch.__version__, "cuda", torch.version.cuda)
print("is_available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0), "cap", torch.cuda.get_device_capability(0))
for name in ("torch_scatter", "droid_backends", "lietorch", "lietorch_backends", "simple_knn", "diff_gaussian_rasterization"):
    mod = importlib.import_module(name)
    print("import_ok", name, getattr(mod, "__file__", "builtin"))
PY

  echo "[rebuild] done $(date -Is)"
} | tee "${LOG}"
