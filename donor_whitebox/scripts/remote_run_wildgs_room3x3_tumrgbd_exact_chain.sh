#!/bin/bash
set -euo pipefail

SEQ="${SEQ:-/root/donor_whitebox/outputs/hislam2_room3x3_seq_wildgs_tumrgbd_exact_20260310}"
OUT="${OUT:-/root/donor_whitebox/outputs/wildgs_room3x3_tumrgbd_exact_static_20260310}"
CONFIG_PATH="${CONFIG_PATH:-/root/donor_whitebox/configs/wildgs_room3x3_tumrgbd_exact_static.yaml}"

cd /root/donor_whitebox
bash scripts/build_room_sequence.sh \
  --output "$SEQ" \
  --width 640 \
  --height 480 \
  --frames 600 \
  --duration 18.81 \
  --seed 7 \
  --fx 535.4 \
  --fy 539.2 \
  --cx 320.1 \
  --cy 247.6 \
  --protocol monogs_tum \
  --write-depth-bin

python3 /root/donor_whitebox/scripts/make_wildgs_room3x3_tumrgbd_exact.py \
  --sequence-root "$SEQ" \
  --output-dir "$OUT" \
  --width 640 \
  --height 480 \
  --overwrite

/venv/hislam2/bin/python /root/donor_whitebox/scripts/preflight_wildgs_inputs.py --cfg "$CONFIG_PATH"

cd /root/gs_refs/WildGS-SLAM.clean
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export PYTHONPATH=/root/gs_refs/WildGS-SLAM.clean/thirdparty/diff-gaussian-rasterization-w-pose:/root/gs_refs/WildGS-SLAM.clean/thirdparty/diff-gaussian-rasterization-w-pose/build/lib.linux-x86_64-cpython-310:${PYTHONPATH:-}
/venv/hislam2/bin/python run.py "$CONFIG_PATH"
