#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
REF_TRAJ="${REF_TRAJ:-/root/gs_refs/HI-SLAM2/data/Replica/room0/traj.txt}"
MODE_SPEC="${MODE_SPEC:-all}"
OUT_ROOT="${OUT_ROOT:-${ROOT}/outputs/hislam2_workpoint_variants_20260316}"
WIDTH="${WIDTH:-960}"
HEIGHT="${HEIGHT:-720}"
FX="${FX:-831.384399}"
FY="${FY:-831.384399}"
CX="${CX:-480}"
CY="${CY:-360}"
DURATION="${DURATION:-37}"
FARTHER_SHRINK="${FARTHER_SHRINK:-0.72}"
DENSER_FACTOR="${DENSER_FACTOR:-1.5}"
TARGET_FRAMES="${TARGET_FRAMES:-0}"
WRITE_DEPTH_BIN="${WRITE_DEPTH_BIN:-0}"
COMPRESS_IMAGES="${COMPRESS_IMAGES:-1}"
REALTIME_SCALE="${REALTIME_SCALE:-0}"

BUILD_SCRIPT="${ROOT}/scripts/build_room_sequence.sh"
PATH_SCRIPT="${ROOT}/scripts/make_hislam2_replica_like_camera_path.py"

if [[ ! -f "${REF_TRAJ}" ]]; then
  echo "[prepare-workpoint] missing reference traj: ${REF_TRAJ}" >&2
  exit 2
fi

if [[ ! -x "${BUILD_SCRIPT}" && ! -f "${BUILD_SCRIPT}" ]]; then
  echo "[prepare-workpoint] missing build script: ${BUILD_SCRIPT}" >&2
  exit 3
fi

if [[ ! -f "${PATH_SCRIPT}" ]]; then
  echo "[prepare-workpoint] missing path generator: ${PATH_SCRIPT}" >&2
  exit 4
fi

mkdir -p "${OUT_ROOT}"

declare -a MODES=()
if [[ "${MODE_SPEC}" == "all" ]]; then
  MODES=("farther_cam" "denser_sampling" "farther_plus_denser")
else
  IFS=',' read -r -a MODES <<< "${MODE_SPEC}"
fi

for mode in "${MODES[@]}"; do
  mode="$(echo "${mode}" | xargs)"
  [[ -z "${mode}" ]] && continue
  cam_path="${OUT_ROOT}/camtraj_${mode}.txt"
  stats_path="${OUT_ROOT}/camtraj_${mode}.json"
  seq_out="${OUT_ROOT}/seq_${mode}"

  echo "[prepare-workpoint] mode=${mode}"
  python3 "${PATH_SCRIPT}" \
    --reference-traj "${REF_TRAJ}" \
    --output "${cam_path}" \
    --stats-out "${stats_path}" \
    --mode "${mode}" \
    --duration "${DURATION}" \
    --farther-shrink "${FARTHER_SHRINK}" \
    --denser-factor "${DENSER_FACTOR}" \
    --target-frames "${TARGET_FRAMES}"

  build_args=(
    --output "${seq_out}"
    --camera-path "${cam_path}"
    --width "${WIDTH}"
    --height "${HEIGHT}"
    --fx "${FX}"
    --fy "${FY}"
    --cx "${CX}"
    --cy "${CY}"
  )
  if [[ "${WRITE_DEPTH_BIN}" == "1" ]]; then
    build_args+=(--write-depth-bin)
  fi
  if [[ "${REALTIME_SCALE}" != "0" && "${REALTIME_SCALE}" != "0.0" ]]; then
    build_args+=(--realtime-scale "${REALTIME_SCALE}")
  fi
  bash "${BUILD_SCRIPT}" "${build_args[@]}"

  if [[ -f "${stats_path}" ]]; then
    cp "${stats_path}" "${seq_out}/capture_protocol.json"
  fi

  if [[ "${COMPRESS_IMAGES}" == "1" ]]; then
    python3 - "${seq_out}" <<'PY'
from pathlib import Path
import sys

pil = None
cv2 = None
try:
    from PIL import Image
    pil = Image
except Exception:
    pil = None
if pil is None:
    try:
        import cv2  # type: ignore
    except Exception as exc:
        raise SystemExit(f"image backend import failed: {exc}")

seq = Path(sys.argv[1])
images = seq / "images"
ppm_files = sorted(images.glob("*.ppm"))
for ppm in ppm_files:
    jpg = ppm.with_suffix(".jpg")
    if pil is not None:
        with pil.open(ppm) as img:
            img.save(jpg, format="JPEG", quality=95)
    else:
        img = cv2.imread(str(ppm), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"failed reading {ppm}")
        if not cv2.imwrite(str(jpg), img, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
            raise RuntimeError(f"failed writing {jpg}")
    ppm.unlink()
print(f"[prepare-workpoint] compressed {len(ppm_files)} frames to jpg for {seq}")
PY
  fi
done

echo "[prepare-workpoint] done out_root=${OUT_ROOT}"
