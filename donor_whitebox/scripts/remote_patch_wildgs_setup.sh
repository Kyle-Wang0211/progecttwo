#!/usr/bin/env bash
set -euo pipefail

export WILDGS_ROOT="${WILDGS_ROOT:-/root/gs_refs/WildGS-SLAM}"
export DONOR_ENV_PY="${DONOR_ENV_PY:-/venv/hislam2/bin/python}"
export DONOR_ENV_ROOT="${DONOR_ENV_ROOT:-/venv/hislam2}"

python3 - <<'PY'
from pathlib import Path
import os
import re

root = Path(os.environ["WILDGS_ROOT"])
env_root = Path(os.environ["DONOR_ENV_ROOT"])
cuda_runtime_include = Path("/usr/local/cuda/include")

def patch_file(path: Path, func):
    text = path.read_text()
    original = text
    text = func(text)
    if text != original:
        path.write_text(text)
        print(f"patched {path}")
    else:
        print(f"unchanged {path}")

def normalize_arch_flags(text: str, anchor: str) -> str:
    text = re.sub(
        r"\s*'-gencode=arch=compute_\d+,code=(?:sm|compute)_\d+',\n",
        "",
        text,
    )
    block = (
        "                    '-gencode=arch=compute_120,code=sm_120',\n"
        "                    '-gencode=arch=compute_120,code=compute_120',\n"
    )
    if anchor not in text:
        raise SystemExit(f"missing arch anchor: {anchor}")
    return text.replace(anchor, anchor + block, 1)

def normalize_arch_flags_all(text: str, anchor: str) -> str:
    updated = text
    while anchor in updated:
        next_text = normalize_arch_flags(updated, anchor)
        if next_text == updated:
            break
        updated = next_text
    return updated

def ensure_include_lines(text: str, anchor: str, includes: list[str]) -> str:
    if anchor not in text:
        raise SystemExit(f"missing include anchor: {anchor}")
    for include in includes:
        if include not in text:
            text = text.replace(anchor, anchor + include, 1)
    return text

def canonicalize_root_setup(text: str) -> str:
    pattern = re.compile(
        r"(CUDAExtension\('droid_backends',\n)(?:\s*include_dirs=.*\n)+"
    )
    replacement = (
        "CUDAExtension('droid_backends',\n"
        "            include_dirs=[osp.join(ROOT, 'thirdparty/lietorch/eigen'), '/usr/include/eigen3'],\n"
    )
    if not pattern.search(text):
        anchor = "        CUDAExtension('droid_backends',\n"
        if anchor not in text:
            raise SystemExit("missing root CUDAExtension anchor")
        text = text.replace(anchor, replacement, 1)
    else:
        text = pattern.sub(replacement, text, count=1)
    return text

patch_file(
    root / "setup.py",
    lambda text: normalize_arch_flags(
        canonicalize_root_setup(text),
        "                    '-gencode=arch=compute_86,code=sm_86',\n",
    ),
)

patch_file(
    root / "thirdparty/lietorch/setup.py",
    lambda text: normalize_arch_flags_all(
        ensure_include_lines(
            text,
            "                osp.join(ROOT, 'eigen')",
            [
                ",\n                '/usr/include/eigen3'",
                f",\n                '{cuda_runtime_include}'",
            ],
        ),
        "                    '-gencode=arch=compute_75,code=compute_75',\n",
    ),
)

def replace_or_fail(path: Path, replacements):
    text = path.read_text()
    original = text
    for old, new in replacements:
        if old not in text and new in text:
            continue
        if old not in text:
            raise SystemExit(f"missing pattern in {path}: {old}")
        text = text.replace(old, new)
    if text != original:
        path.write_text(text)
        print(f"patched {path}")
    else:
        print(f"unchanged {path}")

replace_or_fail(
    root / "thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu",
    [
        (
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.type(), "sampler_forward_kernel"',
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.scalar_type(), "sampler_forward_kernel"',
        ),
        (
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.type(), "sampler_backward_kernel"',
            'AT_DISPATCH_FLOATING_TYPES_AND_HALF(volume.scalar_type(), "sampler_backward_kernel"',
        ),
    ],
)

replace_or_fail(
    root / "thirdparty/lietorch/lietorch/extras/extras.cpp",
    [
        (
            '#define CHECK_CUDA(x) TORCH_CHECK(x.type().is_cuda(), #x " must be a CUDA tensor")',
            '#define CHECK_CUDA(x) TORCH_CHECK(x.is_cuda(), #x " must be a CUDA tensor")',
        ),
    ],
)

replace_or_fail(
    root / "thirdparty/lietorch/lietorch/include/dispatch.h",
    [
        (
            "    at::ScalarType _st = ::detail::scalar_type(the_type);                            \\\n",
            "    at::ScalarType _st = the_type;                            \\\n",
        ),
    ],
)

for relative_path in [
    "thirdparty/lietorch/lietorch/src/lietorch_cpu.cpp",
    "thirdparty/lietorch/lietorch/src/lietorch_gpu.cu",
]:
    path = root / relative_path
    text = path.read_text()
    original = text
    text = text.replace(".type()", ".scalar_type()")
    if text != original:
        path.write_text(text)
        print(f"patched {path}")
    else:
        print(f"unchanged {path}")

for relative_path in [
    "src/lib/correlation_kernels.cu",
    "src/lib/altcorr_kernel.cu",
]:
    path = root / relative_path
    text = path.read_text()
    original = text
    text = text.replace(".type()", ".scalar_type()")
    if text != original:
        path.write_text(text)
        print(f"patched {path}")
    else:
        print(f"unchanged {path}")
PY

export CUDA_HOME="$DONOR_ENV_ROOT"
export CUDA_HOME=/usr/local/cuda
export PATH="$CUDA_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$DONOR_ENV_ROOT/lib/python3.10/site-packages/torch/lib:$CUDA_HOME/lib64:${LD_LIBRARY_PATH:-}"
export TORCH_CUDA_ARCH_LIST=12.0
export CC=/usr/bin/gcc
export CXX=/usr/bin/g++
export CUDAHOSTCXX=/usr/bin/g++

cd "$WILDGS_ROOT/thirdparty/lietorch"
rm -rf build
"$DONOR_ENV_PY" setup.py install

cd "$WILDGS_ROOT"
rm -rf build
"$DONOR_ENV_PY" setup.py install

"$DONOR_ENV_PY" - <<'PY'
import importlib

mods = [
    "droid_backends",
    "lietorch",
]

for name in mods:
    module = importlib.import_module(name)
    print(f"import_ok {name} -> {module.__file__}")
PY
