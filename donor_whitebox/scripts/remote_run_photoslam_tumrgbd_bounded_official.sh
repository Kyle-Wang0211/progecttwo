#!/usr/bin/env bash
set -euo pipefail

PHOTO_SLAM_ROOT="${PHOTO_SLAM_ROOT:-/root/gs_refs/Photo-SLAM.clean}"
SEQ_DIR="${SEQ_DIR:-/root/donor_whitebox/outputs/wildgs_room3x3_tumrgbd_exact_static_20260310}"
VOCAB="${VOCAB:-${PHOTO_SLAM_ROOT}/ORB-SLAM3/Vocabulary/ORBvoc.txt}"
ORB_CFG="${ORB_CFG:-/root/donor_whitebox/configs/photoslam_capture_tumrgbd_orb_official_rgbd.yaml}"
MAPPER_CFG="${MAPPER_CFG:-/root/donor_whitebox/configs/photoslam_tumrgbd_mapper_official.yaml}"
ASSOC_PATH="${ASSOC_PATH:-${SEQ_DIR}/association.txt}"
OUT_DIR="${OUT_DIR:-/root/donor_whitebox/outputs/photoslam_runs/tumrgbd_bounded_official_r1}"
LOG_DIR="${LOG_DIR:-/root/donor_whitebox/logs}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
GEOM_SAMPLE_MAX="${GEOM_SAMPLE_MAX:-50000}"
PHOTO_SLAM_ENV="${PHOTO_SLAM_ENV:-/venv/photo_slam}"
CUDA_ROOT="${CUDA_ROOT:-/usr/local/cuda-12.9}"
OPENCV_PREFIX="${OPENCV_PREFIX:-/opt/opencv-4.10.0-cuda}"

mkdir -p "${OUT_DIR}" "${LOG_DIR}"
RUN_LOG="${LOG_DIR}/photoslam_tumrgbd_bounded_$(date +%Y%m%d_%H%M%S).log"

if [[ -f "${PHOTO_SLAM_ENV}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PHOTO_SLAM_ENV}/bin/activate"
fi
TORCH_LIB_DIR="$("${PYTHON_BIN}" - <<'PY'
import pathlib
import torch
print(pathlib.Path(torch.__file__).resolve().parent / "lib")
PY
)"
export LD_LIBRARY_PATH="${CUDA_ROOT}/lib64:${OPENCV_PREFIX}/lib:${TORCH_LIB_DIR}:${LD_LIBRARY_PATH:-}"

if [[ ! -f "${ASSOC_PATH}" ]]; then
  "${PYTHON_BIN}" - <<'PY' "${SEQ_DIR}" "${ASSOC_PATH}"
from pathlib import Path
import sys

seq_dir = Path(sys.argv[1])
assoc_path = Path(sys.argv[2])

def read_index(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        stamp, rel = line.split(maxsplit=1)
        rows.append((stamp, rel))
    return rows

rgb_rows = read_index(seq_dir / "rgb.txt")
depth_rows = read_index(seq_dir / "depth.txt")
count = min(len(rgb_rows), len(depth_rows))
with assoc_path.open("w", encoding="utf-8") as handle:
    for i in range(count):
        ts_rgb, rel_rgb = rgb_rows[i]
        ts_depth, rel_depth = depth_rows[i]
        handle.write(f"{ts_rgb} {rel_rgb} {ts_depth} {rel_depth}\n")
print(f"ASSOCIATION_PATH={assoc_path}")
print(f"ASSOCIATION_COUNT={count}")
PY
fi

cd "${PHOTO_SLAM_ROOT}"
./bin/tum_rgbd \
  "${VOCAB}" \
  "${ORB_CFG}" \
  "${MAPPER_CFG}" \
  "${SEQ_DIR}" \
  "${ASSOC_PATH}" \
  "${OUT_DIR}" \
  no_viewer > "${RUN_LOG}" 2>&1

FINAL_PLY="$(find "${OUT_DIR}" -type f -name 'point_cloud.ply' | sort | tail -n 1 || true)"
if [[ -n "${FINAL_PLY}" && -f "${OUT_DIR}/CameraTrajectory_TUM.txt" && -f "${SEQ_DIR}/groundtruth.txt" ]]; then
  "${PYTHON_BIN}" /root/donor_whitebox/scripts/eval_room3x3_geometry.py \
    --ply "${FINAL_PLY}" \
    --traj "${OUT_DIR}/CameraTrajectory_TUM.txt" \
    --gt-traj "${SEQ_DIR}/groundtruth.txt" \
    --sample-max "${GEOM_SAMPLE_MAX}" \
    > "${OUT_DIR}/geometry_eval_sample${GEOM_SAMPLE_MAX}.txt"
fi

echo "RUN_LOG=${RUN_LOG}"
echo "OUT_DIR=${OUT_DIR}"
