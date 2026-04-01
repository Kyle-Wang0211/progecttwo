#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/Users/kaidongwang/Documents/progecttwo}"
WORLD_STATE_JSON="${WORLD_STATE_JSON:-}"
OUT_MD="${OUT_MD:-$ROOT/donor_whitebox/docs/whitebox_checkpoint_report_current.md}"
OUT_JSON="${OUT_JSON:-$ROOT/donor_whitebox/docs/whitebox_checkpoint_report_current.json}"

if [[ -z "$WORLD_STATE_JSON" ]]; then
  for candidate_dir in \
    "/Users/kaidongwang/Documents/Aether3D/exports" \
    "$HOME/Documents/Aether3D/exports"
  do
    if [[ -d "$candidate_dir" ]]; then
      latest_json="$(find "$candidate_dir" -maxdepth 1 -name '*.world_state.json' -type f -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -n 1 || true)"
      if [[ -n "$latest_json" ]]; then
        WORLD_STATE_JSON="$latest_json"
        break
      fi
    fi
  done
fi

ARGS=(
  "$ROOT/donor_whitebox/scripts/aggregate_whitebox_report.py"
  --output-md "$OUT_MD"
  --output-json "$OUT_JSON"
)

if [[ -n "$WORLD_STATE_JSON" ]]; then
  ARGS+=(--world-state-json "$WORLD_STATE_JSON")
fi

python3 "${ARGS[@]}"
