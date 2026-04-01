#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from pathlib import Path

files = {
    Path("/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu"): [
        (
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.type(), "sampler_forward_kernel"',
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.scalar_type(), "sampler_forward_kernel"',
        ),
        (
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.type(), "sampler_backward_kernel"',
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.scalar_type(), "sampler_backward_kernel"',
        ),
    ],
    Path("/root/gs_refs/HI-SLAM2/thirdparty/lietorch/lietorch/extras/extras.cpp"): [
        (
            '#define CHECK_CUDA(x) TORCH_CHECK(x.type().is_cuda(), #x " must be a CUDA tensor")',
            '#define CHECK_CUDA(x) TORCH_CHECK(x.is_cuda(), #x " must be a CUDA tensor")',
        ),
    ],
}

for path, replacements in files.items():
    text = path.read_text()
    original = text
    for old, new in replacements:
        if old not in text:
            raise SystemExit(f"missing pattern in {path}: {old}")
        text = text.replace(old, new)
    if text != original:
        path.write_text(text)
        print(f"patched {path}")
PY

export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
export TORCH_CUDA_ARCH_LIST=12.0
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export CUDAHOSTCXX=/usr/bin/g++

cd /root/gs_refs/HI-SLAM2/thirdparty/lietorch
rm -rf build
/venv/hislam2/bin/python setup.py install
