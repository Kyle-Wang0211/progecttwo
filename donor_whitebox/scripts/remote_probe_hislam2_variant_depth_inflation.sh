#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
VARIANT_ROOT="${VARIANT_ROOT:-${ROOT}/outputs/hislam2_workpoint_variants_20260316}"
VARIANT_NAME="${VARIANT_NAME:-farther_plus_denser}"
RESULT_ROOT="${RESULT_ROOT:-${ROOT}/outputs/hislam2_workpoint_farther_plus_denser_full_official_best_v4}"
PROBE_ROOT="${PROBE_ROOT:-${ROOT}/outputs/hislam2_depth_probe_${VARIANT_NAME}_v1}"
WIDTH="${WIDTH:-960}"
HEIGHT="${HEIGHT:-720}"
FX="${FX:-831.384399}"
FY="${FY:-831.384399}"
CX="${CX:-480}"
CY="${CY:-360}"

CAM_TRAJ="${VARIANT_ROOT}/camtraj_${VARIANT_NAME}.txt"
OPT_DIR="${RESULT_ROOT}/renders/depth_after_opt"
SUBSET_TRAJ="${PROBE_ROOT}/camtraj_subset.txt"
RAW_JSON="${PROBE_ROOT}/raw_depth_stats.json"
SUMMARY_JSON="${PROBE_ROOT}/depth_probe_summary.json"

mkdir -p "${PROBE_ROOT}"
rm -rf "${PROBE_ROOT}/sequence"

python3 - "${OPT_DIR}" "${CAM_TRAJ}" "${SUBSET_TRAJ}" <<'PY'
from pathlib import Path
import sys

opt_dir = Path(sys.argv[1])
cam_traj = Path(sys.argv[2])
subset = Path(sys.argv[3])

indices = sorted(int(p.stem) for p in opt_dir.glob("*.png"))
lines = cam_traj.read_text(encoding="utf-8").splitlines()
selected = [lines[i] for i in indices if 0 <= i < len(lines)]
subset.write_text("\n".join(selected) + ("\n" if selected else ""), encoding="utf-8")
print(f"[depth-probe] selected_frames={len(selected)}")
PY

python3 "${ROOT}/scripts/stream_collect_depth_stats.py" \
  --seq-root "${PROBE_ROOT}/sequence" \
  --out-json "${RAW_JSON}" &
collector_pid=$!

bash "${ROOT}/scripts/build_room_sequence.sh" \
  --output "${PROBE_ROOT}/sequence" \
  --camera-path "${SUBSET_TRAJ}" \
  --width "${WIDTH}" \
  --height "${HEIGHT}" \
  --fx "${FX}" \
  --fy "${FY}" \
  --cx "${CX}" \
  --cy "${CY}" \
  --write-depth-bin

wait "${collector_pid}"

python3 "${ROOT}/scripts/compare_hislam2_depth_probe.py" \
  --raw-json "${RAW_JSON}" \
  --opt-dir "${OPT_DIR}" \
  --json > "${SUMMARY_JSON}"

cat "${SUMMARY_JSON}"
