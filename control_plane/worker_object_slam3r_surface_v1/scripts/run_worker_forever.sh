#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
ROOT_DIR="${ROOT_DIR:-${DEFAULT_ROOT_DIR}}"
CONTROL_PLANE_DIR="${CONTROL_PLANE_DIR:-${ROOT_DIR}/control_plane}"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/venv}"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/worker_object_slam3r_surface_v1.env}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "missing env file: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "missing python venv: ${VENV_DIR}/bin/python" >&2
  exit 1
fi

set -a
source "${ENV_FILE}"
set +a

if [[ -n "${OBJECT_SLAM3R_SURFACE_CLAIM_ENABLED_OVERRIDE:-}" ]]; then
  export OBJECT_SLAM3R_SURFACE_CLAIM_ENABLED="${OBJECT_SLAM3R_SURFACE_CLAIM_ENABLED_OVERRIDE}"
fi

source "${SCRIPT_DIR}/load_hf_token.sh"

export PYTHONPATH="${CONTROL_PLANE_DIR}:${PYTHONPATH:-}"
export PYTHONNOUSERSITE=1

mkdir -p "${ROOT_DIR}/local" "${ROOT_DIR}/local/jobs"

cd "${CONTROL_PLANE_DIR}"
SMOKE_SCRIPT="${CONTROL_PLANE_DIR}/worker_object_slam3r_surface_v1/scripts/smoke_surface_quality.py"
RUN_SMOKE_PREFLIGHT="${RUN_SMOKE_PREFLIGHT:-1}"
while true; do
  if [[ "${RUN_SMOKE_PREFLIGHT}" == "1" ]]; then
    echo "[object_slam3r_surface_v1] running smoke preflight ${SMOKE_SCRIPT}" >&2
    "${VENV_DIR}/bin/python" "${SMOKE_SCRIPT}"
  fi
  set +e
  "${VENV_DIR}/bin/python" -m worker_object_slam3r_surface_v1.main
  exit_code=$?
  set -e
  echo "[object_slam3r_surface_v1] worker exited exit_code=${exit_code}; restarting in 2s" >&2
  sleep 2
done
