#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <variants_root> [sleep_s]" >&2
  exit 2
fi

ROOT="$1"
SLEEP_S="${2:-10}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/remote_stream_compress_ppm.sh"

if [[ ! -d "$ROOT" ]]; then
  echo "variants root not found: $ROOT" >&2
  exit 2
fi

while true; do
  all_done=1
  for d in seq_farther_cam seq_denser_sampling seq_farther_plus_denser; do
    img_dir="$ROOT/$d/images"
    done_file="$ROOT/$d/.complete"
    if [[ ! -d "$img_dir" ]]; then
      all_done=0
      continue
    fi
    ppm_count="$(find "$img_dir" -maxdepth 1 -type f -name '*.ppm' | wc -l | tr -d ' ')"
    if [[ ! -f "$done_file" || "$ppm_count" != "0" ]]; then
      all_done=0
    fi
    if ps -eo cmd | grep -F "$HELPER $img_dir" | grep -v grep >/dev/null 2>&1; then
      continue
    fi
    nohup bash "$HELPER" "$img_dir" "$SLEEP_S" > "$ROOT/${d}_stream_compress.log" 2>&1 &
    echo "[variants-root] started compressor for $d"
  done
  if [[ "$all_done" == "1" ]]; then
    echo "[variants-root] all complete"
    break
  fi
  sleep "$SLEEP_S"
done
