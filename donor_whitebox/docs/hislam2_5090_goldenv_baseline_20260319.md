# HI-SLAM2 5090 Gold Environment Baseline (2026-03-19)

This document fixes the current Denmark RTX 5090 machine as the baseline template for future replicas.

## Golden Host

- Host IP: `62.107.25.198`
- SSH port: `48601`
- GPU: `NVIDIA GeForce RTX 5090`
- Driver: `590.48.01`

## Runtime Paths

- HI-SLAM2 repo:
  - `/root/gs_refs/HI-SLAM2`
- HI-SLAM2 env:
  - `/venv/hislam2`
- UniK3D repo:
  - `/root/gs_refs/UniK3D`
- UniK3D env:
  - `/venv/unik3d`

## Core Versions

### `/venv/hislam2`

- Python: `3.11.15`
- torch: `2.10.0`
- torchvision: `0.25.0`
- torchaudio: `2.10.0`
- numpy: `1.26.4`
- pytorch-lightning: `1.5.10.post0`
- rich: `14.3.3`
- timm: `1.0.25`
- torchmetrics: `1.9.0`
- trimesh: `4.11.3`
- cv2: `4.13.0`

### `/venv/unik3d`

- Python: `3.12.3`
- torch: `2.10.0+cu128`
- cv2: `4.13.0`
- numpy: `2.4.3`
- timm: `1.0.25`
- huggingface_hub: `1.7.1`

## System Tools

- `colmap`
- `ffmpeg`
- `gcc 13.3.0`
- `cmake 3.28.3`

## Official Base Commit

- Official HI-SLAM2 commit:
  - `76c833c7d8ed474f0f3ba18056c1803e032a537f`

## Local Patches Applied On Top Of Official

The current gold environment syncs these locally modified files into the remote repo:

- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/hislam2/gs_backend.py`
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/hislam2/hi2.py`
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/hislam2/midas/omnidata.py`
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/hislam2/motion_filter.py`
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/scripts/preprocess_owndata.py`
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/forward.cu`
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/third_party/HI-SLAM2/thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h`

## Bootstrap / Sync Script

Use this local script to reproduce the gold environment on another 5090 host:

- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/sync_hislam2_goldenv_to_host.sh`

Usage:

```bash
bash /Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/sync_hislam2_goldenv_to_host.sh <HOST> <PORT>
```

What it does:

1. Installs required Ubuntu packages on the target host.
2. Clones official HI-SLAM2 at the pinned commit.
3. Copies the local `droid.pth` weight.
4. Copies patched HI-SLAM2 source files.
5. Installs `/venv/hislam2`.
6. Installs `/venv/unik3d`.
7. Verifies the key Python modules on both environments.

## Current Limitation

This script still needs a reachable SSH endpoint for the target host. If Vast direct SSH is not on port `22`, the target instance SSH popup must be opened to obtain the correct direct or proxy port.
