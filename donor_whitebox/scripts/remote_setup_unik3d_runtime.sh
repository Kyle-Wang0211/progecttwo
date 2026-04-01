#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${UNI_K3D_REPO_DIR:-/root/gs_refs/UniK3D}"
VENV_DIR="${UNI_K3D_VENV_DIR:-/venv/unik3d}"
PYTHON_BIN="${UNI_K3D_BOOTSTRAP_PYTHON:-python3}"
TORCH_INDEX_URL="${UNI_K3D_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu121}"

if [[ ! -d "${REPO_DIR}/.git" ]]; then
  git clone https://github.com/lpiccinelli-eth/UniK3D "${REPO_DIR}"
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/pip" install --upgrade pip "setuptools<81" wheel
"${VENV_DIR}/bin/pip" install -e "${REPO_DIR}" --extra-index-url "${TORCH_INDEX_URL}"

echo "[setup-unik3d] repo=${REPO_DIR}"
echo "[setup-unik3d] venv=${VENV_DIR}"
echo "[setup-unik3d] python=$("${VENV_DIR}/bin/python" --version 2>&1)"
