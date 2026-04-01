#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-/venv/wildgs128/bin/python}"
SEQ_ROOT="${SEQ_ROOT:-/root/donor_whitebox/outputs/hislam2_room3x3_seq_dense_600f_12s}"
INPUT_ROOT="${INPUT_ROOT:-/root/donor_whitebox/outputs/wildgs_room3x3_dense600_input}"
CFG_PATH="${CFG_PATH:-/root/donor_whitebox/configs/wildgs_room3x3_dense600.yaml}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/root/donor_whitebox/outputs/wildgs_runs}"
SCENE_NAME="${SCENE_NAME:-room3x3_dense600_whitebox}"
MAX_FRAMES="${MAX_FRAMES:-600}"
export SEQ_ROOT INPUT_ROOT CFG_PATH OUTPUT_ROOT SCENE_NAME MAX_FRAMES

mkdir -p /root/gs_refs/WildGS-SLAM/pretrained
cp -f /root/gs_refs/HI-SLAM2/pretrained_models/droid.pth /root/gs_refs/WildGS-SLAM/pretrained/droid.pth
cd /root/gs_refs/WildGS-SLAM

if [ ! -f /root/gs_refs/WildGS-SLAM/pretrained/depth_anything_v2_metric_hypersim_vitl.pth ]; then
  wget -O /root/gs_refs/WildGS-SLAM/pretrained/depth_anything_v2_metric_hypersim_vitl.pth \
    "https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Hypersim-Large/resolve/main/depth_anything_v2_metric_hypersim_vitl.pth?download=true"
fi

mkdir -p "${INPUT_ROOT}/rgb"
"$PYTHON_BIN" - <<'PY'
import os
from pathlib import Path
from PIL import Image
src = Path(os.environ['SEQ_ROOT']) / 'images'
dst = Path(os.environ['INPUT_ROOT']) / 'rgb'
dst.mkdir(parents=True, exist_ok=True)
count = 0
for i, p in enumerate(sorted(src.glob('*.ppm'))):
    out = dst / f'frame_{i:05d}.png'
    if not out.exists():
        Image.open(p).save(out)
    count += 1
print(f'converted={count}')
PY

mkdir -p /root/donor_whitebox/configs
cat > "${CFG_PATH}" <<YAML
inherit_from: /root/gs_refs/WildGS-SLAM/configs/wildgs_slam.yaml
scene: ${SCENE_NAME}

dataset: 'wild_slam_iphone'
stride: 1
max_frames: ${MAX_FRAMES}
setup_seed: 43
fast_mode: False
device: 'cuda:0'
gui: False
verbose: True

data:
  input_folder: ${INPUT_ROOT}
  output: ${OUTPUT_ROOT}

cam:
  H: 720
  W: 960
  H_out: 360
  W_out: 480
  fx: 831.384399
  fy: 831.384399
  cx: 480.0
  cy: 360.0
  H_edge: 0
  W_edge: 0

mono_prior:
  depth: 'dpt2_vitl_hypersim_20'
  feature_extractor: 'dinov2_vits14'

tracking:
  pretrained: /root/gs_refs/WildGS-SLAM/pretrained/droid.pth
  force_keyframe_every_n_frames: 9
  uncertainty_params:
    activate: False
  backend:
    metric_depth_reg: True

mapping:
  final_refine_iters: 40000
  deform_gaussians: True
  uncertainty_params:
    activate: False
  Training:
    alpha: 0.8
    mapping_itr_num: 600
    window_size: 12
YAML

printf '== config ready ==\n'
sed -n '1,220p' "${CFG_PATH}"
printf '\n== smoke metric depth ==\n'
WILDGS_BUILD_DIR="$(find /root/gs_refs/WildGS-SLAM/build -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
LIETORCH_BUILD_DIR="$(find /root/gs_refs/WildGS-SLAM/thirdparty/lietorch/build -maxdepth 1 -type d -name 'lib.linux-*' | head -n 1)"
TORCH_LIB_DIR=$("$PYTHON_BIN" - <<'PY'
import torch, pathlib
print(pathlib.Path(torch.__file__).resolve().parent / 'lib')
PY
)
LD_LIBRARY_PATH="${TORCH_LIB_DIR}:${LD_LIBRARY_PATH:-}" \
PYTHONPATH=/root/wildgs_pydeps:/root/gs_refs/WildGS-SLAM${WILDGS_BUILD_DIR:+:$WILDGS_BUILD_DIR}${LIETORCH_BUILD_DIR:+:$LIETORCH_BUILD_DIR}:/root/gs_refs/WildGS-SLAM/thirdparty/lietorch \
"$PYTHON_BIN" - <<'PY'
import os
from src import config
from src.utils.mono_priors.metric_depth_estimators import get_metric_depth_estimator
cfg = config.load_config(os.environ['CFG_PATH'])
model = get_metric_depth_estimator(cfg)
print(type(model).__name__)
PY
