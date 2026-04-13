#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/object_slam3r_surface_v1}"
SLAM3R_DIR="${ROOT_DIR}/third_party/SLAM3R"
PYTHON_BIN="${2:-${OBJECT_SLAM3R_SURFACE_SLAM3R_PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}}"
CUDA_HOME="${OBJECT_SLAM3R_SURFACE_CUDA_HOME:-${CUDA_HOME:-/usr/local/cuda}}"
TORCH_BUILD_SHIM_DIR="${ROOT_DIR}/local/torch_build_shim"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "slam3r_python_missing: ${PYTHON_BIN}" >&2
  exit 2
fi
if [[ ! -d "${SLAM3R_DIR}" ]]; then
  echo "slam3r_repo_missing: ${SLAM3R_DIR}" >&2
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

"${PYTHON_BIN}" - <<'PY' "${SLAM3R_DIR}"
from pathlib import Path
import sys

repo = Path(sys.argv[1])
kernel_file = repo / "slam3r" / "pos_embed" / "curope" / "kernels.cu"
text = kernel_file.read_text()
patched = text.replace("AT_DISPATCH_FLOATING_TYPES_AND_HALF(tokens.type(),", "AT_DISPATCH_FLOATING_TYPES_AND_HALF(tokens.scalar_type(),")
if patched != text:
    kernel_file.write_text(patched)
PY

pushd "${SLAM3R_DIR}/slam3r/pos_embed/curope" >/dev/null
"${PYTHON_BIN}" setup.py build_ext --inplace
popd >/dev/null

"${PYTHON_BIN}" - <<'PY' "${SLAM3R_DIR}"
import contextlib
import io
import pathlib
import sys

repo_dir = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo_dir))

capture = io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    from slam3r.pos_embed import RoPE2D

rope_impl = f"{RoPE2D.__module__}.{RoPE2D.__name__}"
print(rope_impl)
warning = capture.getvalue().strip()
if warning:
    print(warning)
if ".curope." not in rope_impl:
    raise SystemExit("cuda_rope2d_still_missing")
PY

echo "SLAM3R_RUNTIME_READY"
