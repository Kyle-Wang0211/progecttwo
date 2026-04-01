#!/usr/bin/env bash
set -euo pipefail

CLEAN_ROOT="${CLEAN_ROOT:-/root/gs_refs/WildGS-SLAM.clean}"
PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"
TARGET_CUDA_HOME="${TARGET_CUDA_HOME:-/usr/local/cuda-12.9}"
ORIG_CUDA_LINK="$(readlink -f /usr/local/cuda || true)"

restore_cuda_link() {
  if [ -n "${ORIG_CUDA_LINK}" ] && [ "${ORIG_CUDA_LINK}" != "${TARGET_CUDA_HOME}" ]; then
    ln -sfn "${ORIG_CUDA_LINK}" /usr/local/cuda
  fi
}

trap restore_cuda_link EXIT

CUDA_HOME="${TARGET_CUDA_HOME}"
export CUDA_HOME
export CUDACXX="${CUDA_HOME}/bin/nvcc"
export PATH="${CUDA_HOME}/bin:${PATH}"
ln -sfn "${CUDA_HOME}" /usr/local/cuda
echo "[remote_build_wildgs_clean_h2] using CUDA_HOME=${CUDA_HOME}"
echo "[remote_build_wildgs_clean_h2] nvcc=$(command -v nvcc)"
nvcc --version

run_setup() {
  local workdir="$1"
  (
    cd "$workdir"
    CUDA_HOME="$CUDA_HOME" TARGET_CUDA_HOME="$TARGET_CUDA_HOME" "$PYTHON_BIN" - <<'PY'
import os
import runpy
import sys
import torch.utils.cpp_extension as ce

os.environ["CUDA_HOME"] = os.environ.get("TARGET_CUDA_HOME", "/usr/local/cuda-12.9")
os.environ["CUDACXX"] = os.path.join(os.environ["CUDA_HOME"], "bin", "nvcc")
os.environ["PATH"] = os.path.join(os.environ["CUDA_HOME"], "bin") + os.pathsep + os.environ["PATH"]
ce.CUDA_HOME = os.environ["CUDA_HOME"]
ce._check_cuda_version = lambda *args, **kwargs: None
sys.argv = ["setup.py", "install"]
runpy.run_path("setup.py", run_name="__main__")
PY
  )
}

run_setup "${CLEAN_ROOT}/thirdparty/lietorch"
run_setup "${CLEAN_ROOT}"

echo "[remote_build_wildgs_clean_h2] build complete for ${CLEAN_ROOT}"
