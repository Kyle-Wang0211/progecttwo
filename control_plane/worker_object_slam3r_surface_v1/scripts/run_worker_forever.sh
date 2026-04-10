#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/opt/object_slam3r_surface_v1}"
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

export PYTHONPATH="${CONTROL_PLANE_DIR}:${PYTHONPATH:-}"

mkdir -p "${ROOT_DIR}/local" "${ROOT_DIR}/local/jobs"

cd "${CONTROL_PLANE_DIR}"
exec "${VENV_DIR}/bin/python" -m worker_object_slam3r_surface_v1.main
