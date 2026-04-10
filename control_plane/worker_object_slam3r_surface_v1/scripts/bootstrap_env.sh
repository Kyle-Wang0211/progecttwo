#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/opt/object_slam3r_surface_v1}"
VENV_DIR="${ROOT_DIR}/venv"
THIRD_PARTY_DIR="${ROOT_DIR}/third_party"
SLAM3R_DIR="${THIRD_PARTY_DIR}/SLAM3R"
SPARSE2DGS_DIR="${THIRD_PARTY_DIR}/Sparse2DGS"
MATCHA_DIR="${THIRD_PARTY_DIR}/MAtCha"
SUGAR_DIR="${THIRD_PARTY_DIR}/SuGaR"
HGS_DIR="${THIRD_PARTY_DIR}/3D-HGS"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  git curl ca-certificates pkg-config \
  build-essential cmake ninja-build \
  ffmpeg python3 python3-venv python3-pip \
  libglib2.0-0 libgl1

mkdir -p "${THIRD_PARTY_DIR}"

if [[ ! -d "${SLAM3R_DIR}" ]]; then
  git clone https://github.com/PKU-VCL-3DV/SLAM3R.git "${SLAM3R_DIR}"
fi
if [[ ! -d "${SPARSE2DGS_DIR}" ]]; then
  git clone https://github.com/Wuuu3511/Sparse2DGS.git "${SPARSE2DGS_DIR}"
fi
if [[ ! -d "${MATCHA_DIR}" ]]; then
  git clone https://github.com/Anttwo/MAtCha.git "${MATCHA_DIR}"
fi
if [[ ! -d "${SUGAR_DIR}" ]]; then
  git clone https://github.com/Anttwo/SuGaR.git "${SUGAR_DIR}"
fi
if [[ ! -d "${HGS_DIR}" ]]; then
  git clone https://github.com/lihaolin88/3DHGS "${HGS_DIR}"
fi

python3 -m venv "${VENV_DIR}"
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$(dirname "$0")/../requirements.txt"
python -m pip install \
  torch==2.10.0 \
  torchvision==0.25.0 \
  torchaudio==2.10.0 \
  --index-url https://download.pytorch.org/whl/cu128

cat <<EOF
Environment bootstrap finished.

Pinned paper stack:
  - SLAM3R (CVPR 2025): ${SLAM3R_DIR}
  - Sparse2DGS (CVPR 2025): ${SPARSE2DGS_DIR}
  - MAtCha (CVPR 2025): ${MATCHA_DIR}
  - SuGaR (CVPR 2024): ${SUGAR_DIR}
  - 3D-HGS (CVPR 2025, optional HQ): ${HGS_DIR}

Next step:
  Configure OBJECT_SLAM3R_SURFACE_*_COMMAND env vars so the worker wrappers invoke the official repos directly.
  Keep OBJECT_SLAM3R_SURFACE_CLAIM_ENABLED=0 until the native data contracts between papers are validated.
EOF
