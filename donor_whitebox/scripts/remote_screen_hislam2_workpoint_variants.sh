#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/root/donor_whitebox}"
VARIANT_ROOT="${VARIANT_ROOT:-${ROOT}/outputs/hislam2_workpoint_variants_20260316}"
RUNNER="${ROOT}/scripts/remote_run_hislam2_workpoint_variant_full_official.sh"
ANALYZER="${ROOT}/scripts/analyze_hislam2_halfway_log.py"
SUMMARY_JSON="${SUMMARY_JSON:-${ROOT}/outputs/hislam2_workpoint_variants_20260316/screening_summary.json}"
HALFWAY_FRAME="${HALFWAY_FRAME:-1000}"
POLL_S="${POLL_S:-20}"
WAIT_S="${WAIT_S:-30}"

variants=(farther_cam denser_sampling farther_plus_denser)

wait_for_sequences() {
  while true; do
    ready=1
    for v in "${variants[@]}"; do
      if [[ ! -f "${VARIANT_ROOT}/seq_${v}/.complete" ]]; then
        ready=0
        break
      fi
    done
    if [[ "${ready}" == "1" ]]; then
      break
    fi
    sleep "${WAIT_S}"
  done
}

screen_variant() {
  local variant="$1"
  local log="${ROOT}/logs/hislam2_workpoint_${variant}_halfway_screen.log"
  local out="${ROOT}/outputs/hislam2_workpoint_${variant}_halfway_screen"

  rm -rf "${out}"
  mkdir -p "${out}"

  (
    export VARIANT_NAME="${variant}"
    export HI_SLAM2_OUT_DIR="${out}"
    bash "${RUNNER}"
  ) >"${log}" 2>&1 &
  local pid=$!

  local density=""
  local frame=""
  while kill -0 "${pid}" 2>/dev/null; do
    json="$(python3 "${ANALYZER}" --log "${log}" --halfway-frame "${HALFWAY_FRAME}" --json 2>/dev/null || true)"
    if [[ -n "${json}" ]]; then
      density="$(python3 - <<'PY' "${json}"
import json, sys
j = json.loads(sys.argv[1])
d = j.get("halfway_keyframe_density")
print("" if d is None else d)
PY
)"
      frame="$(python3 - <<'PY' "${json}"
import json, sys
j = json.loads(sys.argv[1])
f = j.get("halfway_frame_observed")
print("" if f is None else f)
PY
)"
      if [[ -n "${density}" ]]; then
        break
      fi
    fi
    sleep "${POLL_S}"
  done

  if kill -0 "${pid}" 2>/dev/null; then
    pkill -TERM -P "${pid}" 2>/dev/null || true
    kill "${pid}" 2>/dev/null || true
    sleep 3
    pkill -KILL -P "${pid}" 2>/dev/null || true
    kill -KILL "${pid}" 2>/dev/null || true
  fi

  if [[ -z "${density}" ]]; then
    density="999.0"
    frame="0"
  fi

  printf '%s\t%s\t%s\t%s\n' "${variant}" "${density}" "${frame}" "${log}"
}

wait_for_sequences

tmp="$(mktemp)"
for v in "${variants[@]}"; do
  screen_variant "${v}" >> "${tmp}"
done

best_variant="$(sort -k2,2g "${tmp}" | head -n1 | cut -f1)"

python3 - <<'PY' "${tmp}" "${SUMMARY_JSON}" "${best_variant}"
import json, sys
from pathlib import Path

rows = []
for line in Path(sys.argv[1]).read_text().splitlines():
    if not line.strip():
        continue
    variant, density, frame, log = line.split("\t")
    rows.append({
        "variant": variant,
        "halfway_keyframe_density": float(density),
        "halfway_frame_observed": int(frame),
        "log": log,
    })

payload = {
    "variants": rows,
    "best_variant": sys.argv[3],
}
Path(sys.argv[2]).write_text(json.dumps(payload, indent=2, sort_keys=True))
print(json.dumps(payload, indent=2, sort_keys=True))
PY

best_out="${ROOT}/outputs/hislam2_workpoint_${best_variant}_full_official_best"
best_log="${ROOT}/logs/hislam2_workpoint_${best_variant}_full_official_best.log"

(
  export VARIANT_NAME="${best_variant}"
  export HI_SLAM2_OUT_DIR="${best_out}"
  bash "${RUNNER}"
) > "${best_log}" 2>&1 &

echo "[screen-variants] best_variant=${best_variant}"
echo "[screen-variants] best_out=${best_out}"
echo "[screen-variants] best_log=${best_log}"
