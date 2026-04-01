#!/usr/bin/env bash
set -euo pipefail

ROOT="/root/donor_whitebox"
REPO="${HI_SLAM2_REPO:-/root/gs_refs/HI-SLAM2}"
PYTHON_BIN="${HI_SLAM2_PYTHON:-/venv/hislam2/bin/python}"
SEQ_DIR="${HI_SLAM2_SEQ_DIR:-${ROOT}/outputs/hislam2_room3x3_seq_dense_600f_12s}"
CALIB_PATH="${HI_SLAM2_CALIB:-${SEQ_DIR}/calib.txt}"
CONFIG_PATH="${HI_SLAM2_CONFIG:-${REPO}/config/owndata_config.yaml}"
OUT_DIR="${HI_SLAM2_OUT_DIR:-${ROOT}/outputs/hislam2_room3x3_official_runtime_20260315_r1}"
BUFFER_SIZE="${HI_SLAM2_BUFFER:-1000}"
TSDF_VOXEL_SIZE="${HI_SLAM2_TSDF_VOXEL_SIZE:-0.01}"
TSDF_WEIGHT="${HI_SLAM2_TSDF_WEIGHT:-2}"
START_INDEX="${HI_SLAM2_START:-0}"
MAX_LENGTH="${HI_SLAM2_LENGTH:-100000}"
SKIP_TSDF="${HI_SLAM2_SKIP_TSDF:-1}"
DEMO_TIMEOUT="${HI_SLAM2_DEMO_TIMEOUT:-0}"
UNDISTORT_MODE="${HI_SLAM2_UNDISTORT:-auto}"
POSITION_LR_MAX_STEPS_OVERRIDE="${HI_SLAM2_POSITION_LR_MAX_STEPS:-}"
USE_DYNAMIC_POSITION_LR_STEPS="${HI_SLAM2_DYNAMIC_POSITION_LR_STEPS:-0}"
PATCH_TORCH_LOADS_SCRIPT="${PATCH_TORCH_LOADS_SCRIPT:-/root/donor_whitebox/scripts/remote_patch_hislam2_torch_loads.py}"

cleanup_stale_hislam2() {
  python3 - <<'PY'
import os
import signal
import subprocess
import time

targets = [
    "/venv/hislam2/bin/python demo.py --imagedir",
    "/venv/hislam2/bin/python -c from multiprocessing.spawn import spawn_main",
]

me = os.getpid()
try:
    lines = subprocess.check_output(["ps", "-axo", "pid=,cmd="], text=True).splitlines()
except Exception:
    lines = []

for line in lines:
    line = line.strip()
    if not line:
      continue
    pid_s, cmd = line.split(None, 1)
    pid = int(pid_s)
    if pid == me:
        continue
    if any(t in cmd for t in targets):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
time.sleep(3)
for line in lines:
    line = line.strip()
    if not line:
      continue
    pid_s, cmd = line.split(None, 1)
    pid = int(pid_s)
    if pid == me:
        continue
    if any(t in cmd for t in targets):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
PY
  sleep 2
}

compute_dynamic_position_lr_steps() {
  python3 - "${SEQ_DIR}" "${START_INDEX}" "${MAX_LENGTH}" <<'PY'
import json
import math
import sys
from pathlib import Path

seq_dir = Path(sys.argv[1])
start_index = max(0, int(sys.argv[2]))
max_length = int(sys.argv[3])
images_dir = seq_dir / "images"
summary_path = seq_dir / "sequence_summary.json"
live_sfm_path = seq_dir / "LIVE_SFM_READY.json"

exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
frames = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in exts) if images_dir.is_dir() else []
available = max(0, len(frames) - start_index)
raw_effective = available if max_length <= 0 else min(available, max_length)

selected_frames = 0
registered_images = 0
extracted_images = raw_effective
basis = "raw_extracted_images"

def as_int(value) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0

if summary_path.is_file():
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    selected_frames = max(
        selected_frames,
        as_int(payload.get("selected_frames")),
    )
    extracted_images = max(
        extracted_images,
        as_int(payload.get("extracted_image_count")) or as_int(payload.get("frames")),
    )
    colmap_calib = payload.get("colmap_calib")
    if isinstance(colmap_calib, dict):
        selected_frames = max(selected_frames, as_int(colmap_calib.get("selected_frames")))

if live_sfm_path.is_file():
    try:
        payload = json.loads(live_sfm_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    registered_images = max(registered_images, as_int(payload.get("registered_images")))
    selected_frames = max(selected_frames, as_int(payload.get("selected_frames")))

if registered_images > 0:
    effective = registered_images
    basis = "registered_images"
elif selected_frames > 0:
    effective = selected_frames
    basis = "selected_frames"
elif extracted_images > 0:
    # Fallback only when preprocess metadata is missing. We intentionally
    # discount raw extracted video frames because phone videos are often 30-60fps,
    # and budgeting directly from that raw count overstates the true training need.
    effective = max(1, int(math.ceil(extracted_images * 0.25)))
    basis = "discounted_extracted_images"
else:
    effective = 0
    basis = "fallback_default"

if effective <= 0:
    steps = 12000
else:
    # Product-side runtime budget keyed to effective training evidence rather than
    # raw extracted frame count. This does not guarantee higher quality by itself;
    # it only avoids under/over-budgeting obvious cases.
    raw = 6000 + effective * 120
    steps = int(math.ceil(raw / 1000.0) * 1000)
    steps = max(8000, steps)

print(
    f"{steps}\t{basis}\t{effective}\t{selected_frames}\t{registered_images}\t{extracted_images}"
)
PY
}

read_config_position_lr_max_steps() {
  "${PYTHON_BIN}" - "${CONFIG_PATH}" <<'PY'
import sys
from pathlib import Path
import yaml

config_path = Path(sys.argv[1])
default_steps = 26000
if not config_path.is_file():
    print(default_steps)
    raise SystemExit(0)

with config_path.open("r", encoding="utf-8") as f:
    cfg = yaml.full_load(f) or {}

opt = cfg.get("opt_params") or {}
steps = opt.get("position_lr_max_steps", default_steps)
try:
    steps = int(steps)
except Exception:
    steps = default_steps
print(max(1, steps))
PY
}

prepare_runtime_config() {
  local source_config="$1"
  local runtime_config="$2"
  local step_budget="$3"
  "${PYTHON_BIN}" - "${source_config}" "${runtime_config}" "${step_budget}" <<'PY'
import sys
import yaml
from pathlib import Path

source = Path(sys.argv[1])
dest = Path(sys.argv[2])
step_budget = int(sys.argv[3])

with source.open("r", encoding="utf-8") as f:
    cfg = yaml.full_load(f)

cfg.setdefault("opt_params", {})
cfg["opt_params"]["position_lr_max_steps"] = step_budget

with dest.open("w", encoding="utf-8") as f:
    yaml.safe_dump(cfg, f, sort_keys=False)
PY
}

python_module_origin() {
  local module_name="$1"
  "${PYTHON_BIN}" - "${module_name}" <<'PY'
import importlib.util
import sys

name = sys.argv[1]
spec = importlib.util.find_spec(name)
if spec is None or not spec.origin:
    raise SystemExit(1)
print(spec.origin)
PY
}

dir_has_native_module() {
  local dir="$1"
  local module_name="$2"
  [[ -n "${dir}" && -d "${dir}" ]] || return 1
  compgen -G "${dir}/${module_name}"'*.so' >/dev/null
}

append_pythonpath_candidate() {
  local dir="$1"
  local module_name="$2"
  local label="$3"

  [[ -n "${dir}" ]] || return 0
  if dir_has_native_module "${dir}" "${module_name}"; then
    if [[ -n "${EXTRA_PYTHONPATH}" ]]; then
      EXTRA_PYTHONPATH="${EXTRA_PYTHONPATH}:${dir}"
    else
      EXTRA_PYTHONPATH="${dir}"
    fi
  else
    echo "[hislam2-realvideo-runtime] skipping stale ${label}: ${dir} (missing ${module_name}*.so)" >&2
  fi
}

ensure_runtime_native_module() {
  local module_name="$1"
  local label="$2"
  local origin=""

  if origin="$(python_module_origin "${module_name}" 2>/dev/null)"; then
    echo "[hislam2-realvideo-runtime] native import ok ${label} -> ${origin}" >&2
    return 0
  fi

  echo "[hislam2-realvideo-runtime] rebuilding missing native module: ${label}" >&2
  (
    cd "${REPO}"
    rm -rf build *.egg-info thirdparty/lietorch/build
    "${PYTHON_BIN}" setup.py install
  ) >&2

  if origin="$(python_module_origin "${module_name}" 2>/dev/null)"; then
    echo "[hislam2-realvideo-runtime] native import repaired ${label} -> ${origin}" >&2
    return 0
  fi

  echo "[hislam2-realvideo-runtime] missing native module after rebuild: ${label}" >&2
  return 1
}

if [[ ! -d "${REPO}" ]]; then
  echo "[hislam2-realvideo-runtime] missing repo: ${REPO}" >&2
  exit 2
fi

if [[ ! -d "${SEQ_DIR}/images" || ! -f "${CALIB_PATH}" || ! -f "${SEQ_DIR}/.complete" ]]; then
  missing_parts=()
  if [[ ! -d "${SEQ_DIR}/images" ]]; then
    missing_parts+=("images")
  fi
  if [[ ! -f "${CALIB_PATH}" ]]; then
    missing_parts+=("calib.txt")
  fi
  if [[ ! -f "${SEQ_DIR}/.complete" ]]; then
    missing_parts+=(".complete")
  fi
  echo "[hislam2-realvideo-runtime] missing sequence: ${SEQ_DIR} (missing: ${missing_parts[*]})" >&2
  exit 3
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "[hislam2-realvideo-runtime] missing config: ${CONFIG_PATH}" >&2
  exit 4
fi

TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib
import torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
VENV_LIB_DIR="$(cd "$(dirname "${PYTHON_BIN}")/.." && pwd)/lib"
PY_SITE_DIR="$("${PYTHON_BIN}" - <<'PY'
import sysconfig
print(sysconfig.get_paths()["purelib"])
PY
)"
CUDA_RUNTIME_DIR="${PY_SITE_DIR}/nvidia/cuda_runtime/lib"
SYSTEM_CUDA_LIB_DIR="${HI_SLAM2_SYSTEM_CUDA_LIB_DIR:-/usr/local/cuda/lib64}"
SYSTEM_CUDART_SO="${HI_SLAM2_SYSTEM_CUDART_SO:-${SYSTEM_CUDA_LIB_DIR}/libcudart.so.13.1.80}"
REPO_BUILD_LIB=""
if [[ -d "${REPO}/build" ]]; then
  REPO_BUILD_LIB="$(find "${REPO}/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
fi
LIETORCH_BUILD_LIB=""
if [[ -d "${REPO}/thirdparty/lietorch/build" ]]; then
  LIETORCH_BUILD_LIB="$(find "${REPO}/thirdparty/lietorch/build" -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
fi

export LD_LIBRARY_PATH="${SYSTEM_CUDA_LIB_DIR}:${CUDA_RUNTIME_DIR}:${TORCH_LIB_DIR}:${VENV_LIB_DIR}:${LD_LIBRARY_PATH:-}"
if [[ -f "${SYSTEM_CUDART_SO}" ]]; then
  export LD_PRELOAD="${SYSTEM_CUDART_SO}:${LD_PRELOAD:-}"
fi
EXTRA_PYTHONPATH=""
append_pythonpath_candidate "${REPO_BUILD_LIB}" "droid_backends" "repo build dir"
append_pythonpath_candidate "${LIETORCH_BUILD_LIB}" "lietorch_backends" "lietorch build dir"
if [[ -n "${EXTRA_PYTHONPATH}" ]]; then
  export PYTHONPATH="${EXTRA_PYTHONPATH}:${PYTHONPATH:-}"
fi
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-1}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-1}"
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-max_split_size_mb:128}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

ensure_runtime_dependency() {
  local module_name="$1"
  local package_name="${2:-$1}"
  if ! "${PYTHON_BIN}" - <<PY >/dev/null 2>&1
import importlib.util
import sys
raise SystemExit(0 if importlib.util.find_spec("${module_name}") else 1)
PY
  then
    echo "[hislam2-realvideo-runtime] installing missing python package: ${package_name}"
    "${PYTHON_BIN}" -m pip install "${package_name}"
  fi
}

ensure_runtime_dependency munch munch
ensure_runtime_dependency plyfile plyfile
ensure_runtime_dependency open3d open3d
ensure_runtime_dependency imgviz imgviz
ensure_runtime_dependency glfw glfw
ensure_runtime_dependency glm glm
ensure_runtime_dependency pyrender pyrender
ensure_runtime_native_module droid_backends droid_backends
ensure_runtime_native_module lietorch_backends lietorch_backends

mkdir -p "${OUT_DIR}"
cd "${REPO}"

if [[ -f "${PATCH_TORCH_LOADS_SCRIPT}" ]]; then
  python3 "${PATCH_TORCH_LOADS_SCRIPT}"
fi

if [[ -n "${POSITION_LR_MAX_STEPS_OVERRIDE}" ]]; then
  POSITION_LR_MAX_STEPS="${POSITION_LR_MAX_STEPS_OVERRIDE}"
  POSITION_LR_STEP_BASIS="override"
  POSITION_LR_EFFECTIVE_FRAMES="0"
  POSITION_LR_SELECTED_FRAMES="0"
  POSITION_LR_REGISTERED_IMAGES="0"
  POSITION_LR_EXTRACTED_IMAGES="0"
elif [[ "${USE_DYNAMIC_POSITION_LR_STEPS,,}" =~ ^(1|true|yes|on)$ ]]; then
  DYNAMIC_STEP_INFO="$(compute_dynamic_position_lr_steps)"
  IFS=$'\t' read -r \
    POSITION_LR_MAX_STEPS \
    POSITION_LR_STEP_BASIS \
    POSITION_LR_EFFECTIVE_FRAMES \
    POSITION_LR_SELECTED_FRAMES \
    POSITION_LR_REGISTERED_IMAGES \
    POSITION_LR_EXTRACTED_IMAGES <<< "${DYNAMIC_STEP_INFO}"
else
  POSITION_LR_MAX_STEPS="$(read_config_position_lr_max_steps)"
  POSITION_LR_STEP_BASIS="config_default"
  POSITION_LR_EFFECTIVE_FRAMES="0"
  POSITION_LR_SELECTED_FRAMES="0"
  POSITION_LR_REGISTERED_IMAGES="0"
  POSITION_LR_EXTRACTED_IMAGES="0"
fi
RUNTIME_CONFIG_PATH="${CONFIG_PATH}"
if [[ "${POSITION_LR_STEP_BASIS}" != "config_default" ]]; then
  RUNTIME_CONFIG_PATH="${OUT_DIR}/runtime_config.generated.yaml"
  prepare_runtime_config "${CONFIG_PATH}" "${RUNTIME_CONFIG_PATH}" "${POSITION_LR_MAX_STEPS}"
fi

cleanup_stale_hislam2

USE_UNDISTORT=0
case "${UNDISTORT_MODE}" in
  1|true|TRUE|yes|YES)
    USE_UNDISTORT=1
    ;;
  auto|AUTO)
    if python3 - "${CALIB_PATH}" <<'PY'
import sys
from pathlib import Path

parts = Path(sys.argv[1]).read_text(encoding="utf-8").strip().split()
raise SystemExit(0 if len(parts) > 4 else 1)
PY
    then
      USE_UNDISTORT=1
    fi
    ;;
esac

echo "[hislam2-realvideo-runtime] repo=${REPO}"
if [[ -d "${REPO}/.git" ]]; then
  repo_head="$(git -C "${REPO}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  repo_dirty_count="$(git -C "${REPO}" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  echo "[hislam2-realvideo-runtime] repo_head=${repo_head} repo_dirty=${repo_dirty_count}"
fi
echo "[hislam2-realvideo-runtime] seq=${SEQ_DIR}"
echo "[hislam2-realvideo-runtime] calib=${CALIB_PATH}"
echo "[hislam2-realvideo-runtime] config=${CONFIG_PATH}"
echo "[hislam2-realvideo-runtime] runtime_config=${RUNTIME_CONFIG_PATH}"
echo "[hislam2-realvideo-runtime] position_lr_max_steps=${POSITION_LR_MAX_STEPS}"
echo "[hislam2-realvideo-runtime] step_basis=${POSITION_LR_STEP_BASIS} effective_frames=${POSITION_LR_EFFECTIVE_FRAMES} selected_frames=${POSITION_LR_SELECTED_FRAMES} registered_images=${POSITION_LR_REGISTERED_IMAGES} extracted_images=${POSITION_LR_EXTRACTED_IMAGES}"
echo "[hislam2-realvideo-runtime] out=${OUT_DIR}"
echo "[hislam2-realvideo-runtime] undistort=${USE_UNDISTORT}"

echo "[hislam2-realvideo-runtime] running demo"
if [[ "${DEMO_TIMEOUT}" != "0" ]] && command -v timeout >/dev/null 2>&1; then
  timeout --foreground --kill-after=15 "${DEMO_TIMEOUT}" \
    "${PYTHON_BIN}" -u demo.py \
      --imagedir "${SEQ_DIR}/images" \
      --calib "${CALIB_PATH}" \
      --config "${RUNTIME_CONFIG_PATH}" \
      --buffer "${BUFFER_SIZE}" \
      --output "${OUT_DIR}" \
      --start "${START_INDEX}" \
      --length "${MAX_LENGTH}" \
      $( [[ "${USE_UNDISTORT}" == "1" ]] && printf '%s' "--undistort" )
else
  "${PYTHON_BIN}" -u demo.py \
    --imagedir "${SEQ_DIR}/images" \
    --calib "${CALIB_PATH}" \
    --config "${RUNTIME_CONFIG_PATH}" \
    --buffer "${BUFFER_SIZE}" \
    --output "${OUT_DIR}" \
    --start "${START_INDEX}" \
    --length "${MAX_LENGTH}" \
    $( [[ "${USE_UNDISTORT}" == "1" ]] && printf '%s' "--undistort" )
fi

if [[ "${SKIP_TSDF}" == "1" ]]; then
  echo "[hislam2-realvideo-runtime] skipping tsdf integration"
else
  echo "[hislam2-realvideo-runtime] running tsdf integration"
  "${PYTHON_BIN}" -u tsdf_integrate.py --result "${OUT_DIR}" --voxel_size "${TSDF_VOXEL_SIZE}" --weight "${TSDF_WEIGHT}"
fi

echo "[hislam2-realvideo-runtime] done"
