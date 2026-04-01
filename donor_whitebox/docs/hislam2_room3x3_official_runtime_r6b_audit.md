# HI-SLAM2 Room3x3 Official Runtime R6B Audit

Current date: 2026-03-15

This document fixes `r6b` as the first real `HI-SLAM2 on our 3x3 room` baseline evidence.

## Run Identity

- Donor:
  - `HI-SLAM2`
- Input sequence:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_seq_dense_600f_12s`
- Config:
  - `/root/gs_refs/HI-SLAM2/config/owndata_config.yaml`
- Runner:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh`
- Output:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_20260315_r6b`

## What Was Proven

- The already-validated `HI-SLAM2 official Replica` runtime chain can be migrated back onto our own `3x3 room` sequence.
- This run no longer stops at early `PGBA` / `pgo_buffer.py` runtime failures.
- The run reaches:
  - full `traj_full`
  - final `3DGS`
  - rendered RGB/depth outputs
  - final `TSDF mesh`

This means the donor chain is now truly `running to completion` on our target sequence, not just on official `Replica`.

## Final Artifacts

- `traj_full.txt`
  - `600` rows
- `traj_kf.txt`
  - `327` rows
- `3dgs_final.ply`
  - `127,016` vertices
  - file size `8.3M`
- `tsdf_mesh_w2.0.ply`
  - `532,592` vertices
  - `1,037,241` faces
  - file size `39M`
- `renders/image_after_opt`
  - `378` images
- `renders/depth_after_opt`
  - `378` depth maps
- total output size
  - `120M`

## Render Metrics

- `final_result.json`
  - `mean_psnr = 21.2272`
  - `mean_ssim = 0.8384`
  - `mean_lpips = 0.005833`
- `final_result_kf.json`
  - `mean_psnr = 21.2857`
  - `mean_ssim = 0.8996`
  - `mean_lpips = 0.002938`

## Geometry Evaluation On Our 3x3 White-Box

### 3DGS Final

- `K_P95_MM = 1000.0`
- `K_P50_MM = 1000.0`
- `K_MEAN_MM = 917.74`
- `L_CENTROID_OFFSET_MM = 3154.568`
- `L_BBOX_EXTENT_M = 38.3029 x 63.0340 x 81.7286`
- `L_INROOM_RATE = 0.067984`
- `Q1_STRICT_RATE = 0.0`

### TSDF Mesh

- `K_P95_MM = 1000.0`
- `K_P50_MM = 1000.0`
- `K_MEAN_MM = 795.07`
- `L_CENTROID_OFFSET_MM = 2648.299`
- `L_BBOX_EXTENT_M = 3.3018 x 4.1645 x 3.6695`
- `L_INROOM_RATE = 0.157915`
- `Q1_STRICT_RATE = 0.00442`

## Direct Comparison Against Official Replica Room0 Baseline

Reference baseline:
- `/root/donor_whitebox/outputs/hislam2_official_room0_r17_baseline`

Official `Replica room0` on the same old machine:
- `traj_full = 2000`
- `traj_kf = 89`
- `3dgs_final.ply = 170,026` vertices
- `tsdf_mesh_w2.0.ply = 1,263,086` vertices / `2,481,925` faces
- `image_after_opt = 473`
- `depth_after_opt = 473`
- `mean_psnr = 35.1794`
- `mean_ssim = 0.9584`

Current `room3x3 r6b`:
- `traj_full = 600`
- `traj_kf = 327`
- `3dgs_final.ply = 127,016` vertices
- `tsdf_mesh_w2.0.ply = 532,592` vertices / `1,037,241` faces
- `image_after_opt = 378`
- `depth_after_opt = 378`
- `mean_psnr = 21.2272`
- `mean_ssim = 0.8384`

Important comparison facts:
- render quality drops sharply:
  - `PSNR 35.1794 -> 21.2272`
  - `SSIM 0.9584 -> 0.8384`
- keyframe density explodes:
  - official `89 / 2000 = 4.45%`
  - ours `327 / 600 = 54.5%`
- geometry then fails hard on our white-box:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 2648.299~3154.568`
  - `Q1_STRICT_RATE = 0.0~0.00442`

## First-Layer Trajectory / Workpoint Evidence

Using the ground-truth input trajectories that feed the donor:

- official `Replica room0`
  - source: `/root/gs_refs/HI-SLAM2/data/Replica/room0/traj.txt`
  - `frames = 2000`
  - `step_mean_m = 0.012352`
  - `step_p95_m = 0.015479`
  - `step_max_m = 0.017118`
  - `total_path_m = 24.690949`
  - `max_from_start_m = 2.759799`
  - `rot_step_mean_deg = 0.390599`
  - `rot_step_p95_deg = 0.780693`
  - `rot_step_max_deg = 2.040836`

- our `room3x3 dense_600f_12s`
  - source: `/root/donor_whitebox/outputs/hislam2_room3x3_seq_dense_600f_12s/traj_gt.txt`
  - `frames = 600`
  - `step_mean_m = 0.114346`
  - `step_p95_m = 0.235634`
  - `step_max_m = 1.208583`
  - `total_path_m = 68.493368`
  - `max_from_start_m = 2.679437`
  - `rot_step_mean_deg = 8.473660`
  - `rot_step_p95_deg = 21.409063`
  - `rot_step_max_deg = 171.885332`

This is the first hard evidence that the migrated sequence is not driving `HI-SLAM2` in the same regime as official `Replica room0`:

- per-frame translation is about `9.3x` larger
- per-frame rotation is about `21.7x` larger
- total traversed path is about `2.8x` longer despite using only `600` frames instead of `2000`
- worst-case single-frame translation jump is about `70x` larger
- worst-case single-frame rotation jump is about `84x` larger
- spatial cadence is about `9.25x` sparser:
  - official `81.00 frames / meter`
  - ours `8.76 frames / meter`

## Keyframe / Depth Workpoint Evidence

The early keyframe gates are not looser on our side:

- official `Replica` config
  - `motion_filter.thresh = 2.4`
  - `frontend.keyframe_thresh = 4.0`
  - `frontend.frontend_thresh = 16.0`
- official `owndata` config used for `r6b`
  - `motion_filter.thresh = 2.4`
  - `frontend.keyframe_thresh = 4.0`
  - `frontend.frontend_thresh = 16.0`

So the `327 / 600` keyframe explosion is not explained by a looser front-end keyframe trigger.
The first hard explanation is still the input motion / cadence regime.

The depth-related workpoint is also different, but it currently looks secondary:

- official `Replica` uses `mono_depth_alpha = 0.001`
- official `owndata` uses `mono_depth_alpha = 0.01`
- final rendered depth distribution also shifts shallower on our `r6b` output:
  - official `depth_after_opt` sample `p50 = 16052`, `p95 = 23239`
  - our `r6b depth_after_opt` sample `p50 = 12895`, `p95 = 16872`

This means there is a depth/optimization-regime difference too, but the first thing that clearly blows up before geometry is still the much more aggressive motion workpoint.

So the current failure is no longer best explained as a donor runtime issue.
The first-layer explanation is already visible at the input motion / workpoint level: our `3x3` sequence is far more aggressive than the official `Replica` regime that `HI-SLAM2` was proven on.

## Current Conclusion

- `HI-SLAM2` is no longer blocked at the old-machine runtime layer for our `3x3 room`.
- The chain is now proven to run end-to-end on our target sequence.
- The remaining problem is not "it still cannot run".
- The remaining problem is:
  - it runs,
  - but the final geometry still fails badly on our white-box.

This suggests the next investigation should stay on the already-running chain and focus on:

- why official `Replica` stays in a healthy working regime
- while our `3x3` sequence drives `HI-SLAM2` into:
  - much denser keyframe insertion
  - much worse render fidelity
  - catastrophic geometry spread

That is now a `data / workpoint / migration` question, not a "can the donor run" question.
