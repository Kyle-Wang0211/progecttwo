#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${HF_TOKEN:-}" || -n "${HUGGING_FACE_HUB_TOKEN:-}" || -n "${HUGGINGFACE_HUB_TOKEN:-}" ]]; then
  return 0
fi

declare -a HF_TOKEN_CANDIDATES=()
if [[ -n "${OBJECT_SLAM3R_SURFACE_HF_TOKEN_FILE:-}" ]]; then
  HF_TOKEN_CANDIDATES+=("${OBJECT_SLAM3R_SURFACE_HF_TOKEN_FILE}")
fi
if [[ -n "${HF_HOME:-}" ]]; then
  HF_TOKEN_CANDIDATES+=("${HF_HOME}/token")
fi
HF_TOKEN_CANDIDATES+=("${HOME}/.cache/huggingface/token" "${HOME}/.huggingface/token")

for token_file in "${HF_TOKEN_CANDIDATES[@]}"; do
  [[ -f "${token_file}" ]] || continue
  token_value="$(tr -d '\r\n' < "${token_file}")"
  if [[ -n "${token_value}" ]]; then
    export HF_TOKEN="${token_value}"
    export HUGGING_FACE_HUB_TOKEN="${token_value}"
    export HUGGINGFACE_HUB_TOKEN="${token_value}"
    echo "[object_slam3r_surface_v1] loaded HF token from ${token_file}" >&2
    return 0
  fi
done

echo "[object_slam3r_surface_v1] HF token not configured; Hugging Face downloads will be unauthenticated" >&2
