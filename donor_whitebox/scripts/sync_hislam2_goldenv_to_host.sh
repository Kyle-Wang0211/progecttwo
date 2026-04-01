#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 HOST [PORT]" >&2
  exit 2
fi

HOST="$1"
PORT="${2:-22}"
REMOTE="root@${HOST}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCAL_HI_REPO="${WORK_ROOT}/third_party/HI-SLAM2"
LOCAL_DROID="${LOCAL_HI_REPO}/pretrained_models/droid.pth"
REMOTE_ROOT="/root"
REMOTE_GS_ROOT="${REMOTE_ROOT}/gs_refs"
REMOTE_HI_REPO="${REMOTE_GS_ROOT}/HI-SLAM2"
REMOTE_SCRIPTS_DIR="${REMOTE_ROOT}/donor_whitebox/scripts"
OFFICIAL_COMMIT="76c833c7d8ed474f0f3ba18056c1803e032a537f"
PATCH_HI_CORE="${PATCH_HI_CORE:-0}"

if [[ ! -f "${LOCAL_DROID}" ]]; then
  echo "[sync-goldenv] missing local droid weight: ${LOCAL_DROID}" >&2
  exit 3
fi

SSH=(ssh -o StrictHostKeyChecking=accept-new -p "${PORT}" "${REMOTE}")
SCP=(scp -P "${PORT}")

echo "[sync-goldenv] target=${REMOTE} port=${PORT}"

"${SSH[@]}" "bash -lc \"
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y git git-lfs curl ffmpeg colmap build-essential cmake ninja-build pkg-config libgl1 libglib2.0-0 libsm6 libxext6 libxrender1
  mkdir -p '${REMOTE_GS_ROOT}' '${REMOTE_SCRIPTS_DIR}' /venv
  if [[ ! -d '${REMOTE_HI_REPO}/.git' ]]; then
    rm -rf '${REMOTE_HI_REPO}'
    git clone --recursive https://github.com/Willyzw/HI-SLAM2 '${REMOTE_HI_REPO}'
  fi
  mkdir -p '${REMOTE_HI_REPO}/pretrained_models'
  cd '${REMOTE_HI_REPO}'
  git fetch --all --tags
  git checkout '${OFFICIAL_COMMIT}'
  git submodule update --init --recursive
\""

"${SCP[@]}" "${LOCAL_DROID}" "${REMOTE}:${REMOTE_HI_REPO}/pretrained_models/droid.pth"

for rel in \
  donor_whitebox/scripts/remote_install_hislam2_realvideo_stack_5090.sh \
  donor_whitebox/scripts/remote_setup_unik3d_runtime.sh \
  donor_whitebox/scripts/remote_patch_torch_scatter_cuda_guard.sh \
  donor_whitebox/scripts/prepare_real_video_owndata.py \
  donor_whitebox/scripts/audit_real_video_frames.py \
  donor_whitebox/scripts/frame_audit_core.py \
  donor_whitebox/scripts/judge_hislam2_probe.py \
  donor_whitebox/scripts/summarize_hislam2_result.py \
  donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh \
  donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh \
  donor_whitebox/scripts/preexport_surface_prune.py \
  donor_whitebox/scripts/postprocess_3dgs_ply.py \
  donor_whitebox/scripts/generate_sam2_video_masks.py \
  donor_whitebox/scripts/segcut_3dgs_ply.py
do
  src="${WORK_ROOT}/../${rel}"
  "${SCP[@]}" "${src}" "${REMOTE}:${REMOTE_SCRIPTS_DIR}/"
done

copy_patch() {
  local src="$1"
  local dst="$2"
  "${SCP[@]}" "${src}" "${REMOTE}:${dst}"
}

if [[ "${PATCH_HI_CORE}" == "1" ]]; then
  echo "[sync-goldenv] applying local HI-SLAM2 core patches"
  copy_patch "${LOCAL_HI_REPO}/hislam2/gs_backend.py" "${REMOTE_HI_REPO}/hislam2/gs_backend.py"
  copy_patch "${LOCAL_HI_REPO}/hislam2/hi2.py" "${REMOTE_HI_REPO}/hislam2/hi2.py"
  copy_patch "${LOCAL_HI_REPO}/hislam2/midas/omnidata.py" "${REMOTE_HI_REPO}/hislam2/midas/omnidata.py"
  copy_patch "${LOCAL_HI_REPO}/hislam2/motion_filter.py" "${REMOTE_HI_REPO}/hislam2/motion_filter.py"
  copy_patch "${LOCAL_HI_REPO}/scripts/preprocess_owndata.py" "${REMOTE_HI_REPO}/scripts/preprocess_owndata.py"
  copy_patch "${LOCAL_HI_REPO}/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/forward.cu" "${REMOTE_HI_REPO}/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/forward.cu"
  copy_patch "${LOCAL_HI_REPO}/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h" "${REMOTE_HI_REPO}/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h"
else
  echo "[sync-goldenv] keeping upstream HI-SLAM2 core at ${OFFICIAL_COMMIT}"
fi

"${SSH[@]}" "bash -lc \"
  set -euo pipefail
  chmod +x '${REMOTE_SCRIPTS_DIR}/remote_install_hislam2_realvideo_stack_5090.sh'
  chmod +x '${REMOTE_SCRIPTS_DIR}/remote_setup_unik3d_runtime.sh'
  chmod +x '${REMOTE_SCRIPTS_DIR}/remote_patch_torch_scatter_cuda_guard.sh'
  HI_REPO='${REMOTE_HI_REPO}' HI_ENV_PREFIX=/venv/hislam2 UNI_SETUP_SCRIPT='${REMOTE_SCRIPTS_DIR}/remote_setup_unik3d_runtime.sh' \
    bash '${REMOTE_SCRIPTS_DIR}/remote_install_hislam2_realvideo_stack_5090.sh'
  /venv/hislam2/bin/python - <<'PY'
import torch, pytorch_lightning, cv2, trimesh, rich, timm, torchmetrics
print(\"goldenv torch\", torch.__version__)
print(\"goldenv pytorch_lightning\", pytorch_lightning.__version__)
print(\"goldenv cv2\", cv2.__version__)
print(\"goldenv trimesh\", trimesh.__version__)
print(\"goldenv rich\", rich.__version__ if hasattr(rich, \"__version__\") else \"ok\")
print(\"goldenv timm\", timm.__version__)
print(\"goldenv torchmetrics\", torchmetrics.__version__)
PY
  /venv/unik3d/bin/python - <<'PY'
import torch, cv2, numpy, timm, huggingface_hub
print(\"unik3d torch\", torch.__version__)
print(\"unik3d cv2\", cv2.__version__)
print(\"unik3d numpy\", numpy.__version__)
print(\"unik3d timm\", timm.__version__)
print(\"unik3d huggingface_hub\", huggingface_hub.__version__)
PY
\""

echo "[sync-goldenv] complete for ${REMOTE}:${PORT}"
