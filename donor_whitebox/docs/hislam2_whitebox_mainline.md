# HI-SLAM2 White-Box Mainline

This donor white-box baseline does not use the legacy Aether training path as the main runtime.
It establishes an external donor-first path:

1. Generate a procedural 3m x 3m handheld room sequence.
2. Feed that sequence into HI-SLAM2.
3. Validate donor outputs against Aether white-box checkpoints.
4. Only after donor behavior is validated, migrate effective modules back into the native runtime.

## Entry points

- Sequence exporter:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/build_room_sequence.sh`
- HI-SLAM2 runner:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/run_hislam2_whitebox.sh`

## Generated dataset layout

- `images/*.ppm`
- `calib.txt`
- `traj_gt.txt`
- `manifest.txt`

Default dataset path:
- `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/outputs/hislam2_room3x3`

## Notes

- The exporter uses the same procedural 3x3 room geometry and handheld path semantics as the existing Aether white-box test.
- The runner probes Python modules and CUDA availability first. If donor dependencies are missing, it fails fast with a concrete environment report.
- This path is for white-box donor validation, not the final iPhone runtime.

## Current 5090 status

- Clean 5090 rebuilds now align the dirty donor changes in:
  - `demo.py`
  - `setup.py`
  - `hislam2/hi2.py`
  - `hislam2/midas/base_model.py`
  - `hislam2/midas/omnidata.py`
  - `thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h`
  - `thirdparty/lietorch/setup.py`
  - `thirdparty/lietorch/lietorch/extras/corr_index_kernel.cu`
  - `thirdparty/lietorch/lietorch/extras/extras.cpp`
  - `thirdparty/lietorch/*`
- Remaining dirty donor diffs outside those files do not include `hislam2/modules/corr.py` or `hislam2/motion_filter.py`.
- Latest clean 5090 run still fails at the same place:
  - log: `hislam2_clean_5090_run_20260309_024219.log`
  - site: `hislam2/modules/corr.py:70`
  - call: `torch.matmul(fmap1.transpose(1,2), fmap2)`
  - runtime: `CUBLAS_STATUS_INVALID_VALUE`
- Current conclusion:
  - this crash has not yet been explained by any direct dirty-donor diff in `corr.py` or `motion_filter.py`
  - next comparison target remains donor-truth source differences, not new logic

## Current Verified Baselines

### Official Replica

- `HI-SLAM2` official `Replica` has already been fully reproduced on the old `5090`.
- Official `room0` baseline is fixed at:
  - `/root/donor_whitebox/outputs/hislam2_official_room0_r17_baseline`
- Same-machine `room0` evidence:
  - `traj_full = 2000`
  - `traj_kf = 89`
  - `3dgs_final = 170,026` vertices
  - `tsdf_mesh_w2.0 = 1,263,086` vertices / `2,481,925` faces
  - `mean_psnr = 35.1794`
  - `mean_ssim = 0.9584`

### Our 3x3 Room

- The first fully completed migrated baseline is:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_20260315_r6b`
- This proves the already-validated official runtime chain can now run end-to-end on our own `3x3` sequence.
- Final emitted artifacts:
  - `traj_full.txt`
  - `traj_kf.txt`
  - `3dgs_final.ply`
  - `renders/image_after_opt`
  - `renders/depth_after_opt`
  - `tsdf_mesh_w2.0.ply`
- Main results:
  - `traj_full = 600`
  - `traj_kf = 327`
  - `3dgs_final = 127,016` vertices
  - `tsdf_mesh_w2.0 = 532,592` vertices / `1,037,241` faces
  - `mean_psnr = 21.2272`
  - `mean_ssim = 0.8384`

### Replica-Like Motion Workpoint Rerun

- To remove the first hard workpoint mismatch, we generated a new `Replica-like` `3x3` sequence by fitting the official `Replica room0` camera path into the same procedural room:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_replica_like_camtraj_r1.txt`
  - `/root/donor_whitebox/outputs/hislam2_room3x3_seq_replica_like_2000f_r1`
- This is still the same room and the same algorithm path. Only the input motion/cadence protocol was changed.
- New input trajectory stats:
  - `frames = 2000`
  - `step_mean_m = 0.010537`
  - `step_p95_m = 0.013205`
  - `step_max_m = 0.014603`
  - `frames_per_meter = 94.95`
- These are now very close to official `Replica room0`:
  - official `step_mean_m = 0.012352`
  - official `step_p95_m = 0.015479`
  - official `step_max_m = 0.017118`
  - official `frames_per_meter = 81.00`
- The first allocator-compatible rerun is:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_replica_like_20260315_r1_alloc`
- This rerun successfully passed the previous terminal `Global BA` OOM and completed:
  - tracking / mapping
  - `Global BA`
  - color refinement
  - render evaluation
- Final emitted artifacts so far:
  - `traj_full.txt`
  - `traj_kf.txt`
  - `3dgs_final.ply`
  - `renders/image_after_opt`
  - `renders/depth_after_opt`
  - `psnr/after_opt/final_result.json`
  - `psnr/after_opt/final_result_kf.json`
- Current emitted metrics:
  - `traj_full = 2000`
  - `traj_kf = 548`
  - `keyframe density = 27.4%`
  - `3dgs_final = 310,078` sampled points in geometry eval path
  - `mean_psnr = 30.3285`
  - `mean_ssim = 0.9244`
  - `kf mean_psnr = 32.7744`
  - `kf mean_ssim = 0.9595`
- Compared with the old `r6b` baseline:
  - keyframe density improved from `54.5%` to `27.4%`
  - render quality improved sharply
- But geometry did not recover:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 25487.081`
  - `Q1_STRICT_RATE = 0.0`
- So the motion/cadence correction improved front-end behavior and render quality, but it did not restore world geometry.
- The current TSDF branch is still incomplete:
  - the first `tsdf_integrate.py` run failed on `No block is touched in TSDF volume`
  - a minimal runtime-only compatibility patch now skips those offending frames and logs them
  - later CPU-only retries narrowed the failure to mesh extraction
  - a final `legacy CPU TSDF` fallback was then used to force mesh extraction to complete
  - final `Replica-like alloc` TSDF result:
    - `tsdf_mesh_w2.0.ply` emitted
    - `33` frames skipped for `no_block_touched`
    - `407` frames skipped because `dep.min() >= depth_max`
    - geometry still fails hard:
      - `K_P95_MM = 1000.0`
      - `L_CENTROID_OFFSET_MM = 12488.45`
      - `Q1_STRICT_RATE = 0.00032`
  - this confirms the current failure is not "3DGS only":
    - `3DGS` is worse
    - but `TSDF` is also geometrically wrong on the same rerun

### Replica-Like Depth-Scale Rerun (`mono_depth_alpha = 0.001`)

- To align the migrated path more closely with official `Replica`, we kept the same `Replica-like` input but changed only:
  - `Tracking.frontend.mono_depth_alpha: 0.01 -> 0.001`
- Config used:
  - `/root/donor_whitebox/configs/hislam2_owndata_replica_alpha001.yaml`
- Output directory:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_replica_like_alpha001_20260316_r1`
- Main emitted artifacts:
  - `traj_full.txt`
  - `traj_kf.txt`
  - `3dgs_final.ply`
  - `renders/image_after_opt`
  - `renders/depth_after_opt`
  - `psnr/after_opt/final_result.json`
  - `psnr/after_opt/final_result_kf.json`
  - `tsdf_mesh_w2.0.ply`
- Main metrics:
  - `traj_full = 2000`
  - `traj_kf = 518`
  - `keyframe density = 25.9%`
  - `mean_psnr = 27.9770`
  - `mean_ssim = 0.9193`
  - `kf mean_psnr = 30.6467`
  - `kf mean_ssim = 0.9505`
- Compared with the previous `Replica-like alloc` run:
  - keyframe density improves slightly:
    - `27.4% -> 25.9%`
  - but render quality drops:
    - `PSNR 30.3285 -> 27.9770`
- Geometry still fails hard for `3dgs_final`:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 36344.909`
  - `Q1_STRICT_RATE = 0.0`
- A more stable `legacy CPU TSDF` postprocess fallback was then used to force mesh extraction to complete on the same output folder.
- That TSDF mesh also fails hard:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 7932.715`
  - `Q1_STRICT_RATE = 0.00046`
- This means the current failure is no longer explainable as "`3DGS` alone is inflating while TSDF would still be healthy".
- Instead, both outputs are already geometrically wrong, with `3DGS` worse than `TSDF`.
- So the current best explanation remains:
  - motion/cadence correction helped front-end behavior
  - but the migrated depth/scale regime still does not match official `Replica`
  - and changing only `mono_depth_alpha` is not enough to pull geometry back into the healthy range

### Depth/Scale Config Diff: `replica_config` vs `owndata_config`

- The direct config diff that still matters for depth/scale is now very small and explicit.
- Same between the two official configs:
  - `pcd_downsample = 64`
  - `pcd_downsample_init = 32`
  - `adaptive_pointsize = True`
  - `point_size = 0.05`
  - `scale_multiplier = 2.0`
  - `init_gaussian_extent = 30`
  - `gaussian_extent = 1.0`
  - `size_threshold = 20`
  - `rgb_boundary_threshold = 0.01`
  - `percent_dense = 0.01`
- Remaining hard differences:
  - `mono_depth_alpha`
    - `Replica = 0.001`
    - `owndata = 0.01`
  - `lambda_dnormal`
    - `Replica = 0.1`
    - `owndata = 0.5`
  - `position_lr_max_steps`
    - `Replica = 2000`
    - `owndata = 26000`
- There are also broader front-end / backend behavior differences:
  - `motion_filter.init_thresh` exists only in `owndata`
  - `skip_blur = False -> True`
  - `backend_thresh = 22.0 -> 40.0`
  - `backend_nms = 3 -> 1`
  - `pgba.active = False -> True`
  - `compensate_exposure = false -> true`
  - `exposure_lr` exists only in `owndata`
- But after the `Replica-like` motion fix, the next depth/scale investigation should stay focused on:
  - `mono_depth_alpha`
  - `lambda_dnormal`
  - `position_lr_max_steps`

### Depth/Scale Official-Aligned Rerun (`mono_depth_alpha=0.001`, `lambda_dnormal=0.1`, `position_lr_max_steps=2000`)

- To align the migrated path more closely with official `Replica`, we kept the same `Replica-like` input and pulled back all three depth/scale-related config differences at once:
  - `Tracking.frontend.mono_depth_alpha: 0.01 -> 0.001`
  - `Training.mapping.lambda_dnormal: 0.5 -> 0.1`
  - `Training.mapping.position_lr_max_steps: 26000 -> 2000`
- Config used:
  - `/root/donor_whitebox/configs/hislam2_owndata_replica_depthscale_official_r1.yaml`
- Output directory:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_replica_like_depthscale_20260316_r2`
- Main emitted artifacts:
  - `traj_full.txt`
  - `traj_kf.txt`
  - `3dgs_final.ply`
  - `renders/image_after_opt`
  - `renders/depth_after_opt`
  - `psnr/after_opt/final_result.json`
  - `psnr/after_opt/final_result_kf.json`
  - `tsdf_mesh_w2.0.ply`
- Main metrics:
  - `traj_full = 2000`
  - `traj_kf = 561`
  - `keyframe density = 28.05%`
  - `mean_psnr = 26.9744`
  - `mean_ssim = 0.9372`
  - `kf mean_psnr = 27.4017`
  - `kf mean_ssim = 0.9417`
- Depth alignment against raw `depth_raw` on matched frames:
  - `matched_frames = 853`
  - raw frame-median depth:
    - `p50 = 0.598m`
    - `p95 = 1.722m`
  - rendered `depth_after_opt` frame-median depth:
    - `p50 = 4.097m`
    - `p95 = 10.000m`
  - frame-median inflation ratio:
    - `p50 = 4.52x`
    - `p95 = 19.19x`
    - `mean = 8.63x`
- Compared with the previous `Replica-like alloc` run:
  - depth inflation improves materially:
    - frame-median `p50` inflation `10.11x -> 4.52x`
  - but keyframe density does not improve:
    - `27.4% -> 28.05%`
  - render quality also drops:
    - `PSNR 30.3285 -> 26.9744`
- `3dgs_final` geometry still fails hard:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 13945.864`
  - `Q1_STRICT_RATE = 0.0`
- The same run's `legacy CPU TSDF` mesh also fails:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 20824.707`
  - `Q1_STRICT_RATE = 0.00032`
- This run is still a hard fail geometrically, but it adds one important new fact:
  - pulling back the official depth/scale knobs reduces the depth inflation from roughly `10x` to roughly `4.5x`
  - yet both `3DGS` and `TSDF` still remain far outside the healthy geometric regime
- So the current state is:
  - `motion/cadence` mismatch was real and fixing it helped front-end / render
  - `depth/scale` mismatch was also real and fixing it helped inflation
  - but those two fixes still do not recover world geometry on our `3x3 room`
  - the next variable group should move to official `pgba.active + backend_thresh + backend_nms`, without mixing `skip_blur` or exposure changes

### Backend Official-Aligned Rerun (`pgba.active=False`, `backend_thresh=22.0`, `backend_nms=3`)

- To test the next isolated variable group, we kept the same `Replica-like` input and the same depth/scale-aligned values, then changed only:
  - `Tracking.backend.backend_thresh: 40.0 -> 22.0`
  - `Tracking.backend.backend_nms: 1 -> 3`
  - `Tracking.pgba.active: True -> False`
- Config used:
  - `/root/donor_whitebox/configs/hislam2_owndata_replica_depthscale_backend_official_r1.yaml`
- Output directory:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_replica_like_depthscale_backend_20260316_r1`
- Main emitted artifacts:
  - `traj_full.txt`
  - `traj_kf.txt`
  - `3dgs_final.ply`
  - `renders/image_after_opt`
  - `renders/depth_after_opt`
  - `psnr/after_opt/final_result.json`
  - `psnr/after_opt/final_result_kf.json`
  - `tsdf_mesh_w2.0.ply`
- Main metrics:
  - `traj_full = 2000`
  - `traj_kf = 574`
  - `keyframe density = 28.7%`
  - `mean_psnr = 28.5133`
  - `mean_ssim = 0.9353`
  - `kf mean_psnr = 28.2976`
  - `kf mean_ssim = 0.9454`
- Depth alignment against raw `depth_raw` on matched frames:
  - `matched_frames = 865`
  - raw frame-median depth:
    - `p50 = 0.592m`
    - `p95 = 1.785m`
  - rendered `depth_after_opt` frame-median depth:
    - `p50 = 10.000m`
    - `p95 = 10.000m`
  - frame-median inflation ratio:
    - `p50 = 9.93x`
    - `p95 = 38.94x`
    - `mean = 14.51x`
- `3dgs_final` geometry still fails hard:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 42070.57`
  - `Q1_STRICT_RATE = 0.0`
- `legacy CPU TSDF` mesh also fails hard:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 24660.878`
  - `Q1_STRICT_RATE = 0.0004`
- Compared with the previous `depthscale_official_r2` run:
  - keyframe density gets worse:
    - `28.05% -> 28.7%`
  - render does not recover the previous best:
    - `PSNR 26.9744 -> 28.5133`, still below `alloc`
  - depth inflation gets worse again:
    - frame-median `p50` inflation `4.52x -> 9.93x`
  - `3DGS` geometry gets worse:
    - `L 13945.864 -> 42070.57`
  - `TSDF` geometry also gets worse:
    - `L 20824.707 -> 24660.878`
- So this backend-aligned rerun answers the next main question directly:
  - pulling `pgba.active + backend_thresh + backend_nms` back to official does **not** continue to pull geometry toward the healthy official regime
  - on our current `Replica-like` 3x3 sequence, it pushes both depth inflation and world geometry farther away again

### Current White-Box Conclusion

- `HI-SLAM2` is no longer blocked at the runtime layer for our target sequence.
- The remaining problem is now downstream quality, not "can it run".
- On our `3x3` white-box geometry evaluator, the first real migrated baseline still fails hard:
  - `3DGS K_P95_MM = 1000.0`
  - `TSDF K_P95_MM = 1000.0`
  - `3DGS L_CENTROID_OFFSET_MM = 3154.568`
  - `TSDF L_CENTROID_OFFSET_MM = 2648.299`
  - `Q1_STRICT_RATE = 0.0 ~ 0.00442`
- The first hard migration explanation is now visible at the trajectory/workpoint layer:
  - official `Replica room0` mean step = `0.012352m`, mean rotation step = `0.390599deg`
  - our `room3x3 dense_600f_12s` mean step = `0.114346m`, mean rotation step = `8.473660deg`
  - that is about `9.3x` larger translation per frame and `21.7x` larger rotation per frame on our sequence
  - spatial cadence is about `9.25x` sparser on our sequence:
    - official `81.00 frames / meter`
    - ours `8.76 frames / meter`
- The keyframe trigger itself is not looser on our side:
  - official `Replica` config and official `owndata` config both keep
    - `motion_filter.thresh = 2.4`
    - `frontend.keyframe_thresh = 4.0`
    - `frontend.frontend_thresh = 16.0`
- So the current `327 / 600` keyframe density is first explained by the more aggressive input motion/cadence regime, not by a relaxed front-end threshold.
- The new `Replica-like` rerun proves that this was only the first mismatch layer:
  - motion/cadence can be pulled much closer to official `room0`
  - keyframe density and render quality improve substantially
  - but geometry can still remain badly wrong
- The second hard mismatch layer is now visible at depth workpoint:
  - official `Replica room0` sampled depth stats:
    - `p50 = 19.015m`
    - `p95 = 31.248m`
    - `mean = 19.870m`
  - our `Replica-like` raw depth stats:
    - `p50 = 0.685m`
    - `p95 = 1.933m`
    - `mean = 0.828m`
- The matched-frame `depth_raw -> depth_after_opt` comparison shows that depth expands drastically inside the finished `3dgs_final` path:
  - matched frames = `851`
  - raw frame-median depth:
    - `p50 = 0.586m`
    - `p95 = 1.780m`
  - rendered `depth_after_opt` frame-median depth:
    - `p50 = 9.242m`
    - `p95 = 10.000m`
  - frame-median inflation ratio:
    - `p50 = 10.11x`
    - `p95 = 37.79x`
    - `mean = 14.84x`
- The current config evidence is consistent with that depth inflation direction:
  - official `replica_config` uses `mono_depth_alpha = 0.001`
  - official `owndata_config` uses `mono_depth_alpha = 0.01`
  - so our migrated path is currently weighting monocular depth prior about `10x` more strongly than the official `Replica` path
- This explains why keyframe density and render metrics can improve while geometry still becomes worse:
  - front-end motion workpoint moved closer to official `room0`
  - but the solved depth field still inflates the scene much farther away than the raw input
  - so `3dgs_final` can render more stably while world geometry expands catastrophically
- So even after motion/cadence was corrected, the same camera path is still operating in a dramatically nearer depth field than official `room0`.
- The current best explanation is:
  - motion/cadence was the first front-end blocker
  - but after that was corrected, depth/scale workpoint mismatch remained large enough that geometry still did not converge to the official regime
- So the next investigation should stay on this already-running chain and explain why official `Replica` remains healthy while the migrated `3x3` sequence does not, with depth/scale now treated as the next main mismatch layer.

### Workpoint-Variant Result

- We then stopped chasing single config knobs and moved to workpoint variants on the same `full_official` chain:
  - `farther_cam`
  - `denser_sampling`
  - `farther_plus_denser`
- The halfway screen originally mis-picked `denser_sampling` because the log parser failed on tqdm-style progress lines.
- After fixing the parser, the true halfway keyframe densities were:
  - `farther_plus_denser = 0.117`
  - `denser_sampling = 0.161`
  - `farther_cam = 0.193`
- So the true best workpoint variant is `farther_plus_denser`.

- The finished full run for that best variant is:
  - `hislam2_workpoint_farther_plus_denser_full_official_best_v4`
- Its final top-line metrics are:
  - `traj_full = 3000`
  - `traj_kf = 267`
  - `keyframe density = 8.9%`
  - `mean_psnr = 36.1800`
  - `mean_ssim = 0.9806`

- The matched-frame raw-depth probe for that best variant shows much healthier depth inflation than `full_official_r2`:
  - matched frames = `813`
  - raw frame-median depth:
    - `p50 = 0.9167m`
    - `p95 = 1.8084m`
  - rendered `depth_after_opt` frame-median depth:
    - `p50 = 1.0291m`
    - `p95 = 1.9785m`
  - frame-median inflation ratio:
    - `p50 = 0.9236x`
    - `p95 = 1.8931x`
    - `mean = 1.1207x`

- Compared with the previous `full_official_r2` on the same white-box target:
  - keyframe density improves dramatically:
    - `43.75% -> 8.9%`
  - render improves strongly:
    - `PSNR 28.6480 -> 36.1800`
  - depth inflation improves strongly:
    - frame-median `p50` inflation `9.75x -> 0.92x`

- Geometry improves materially, but still fails the white-box thresholds:
  - `3DGS`
    - `K_P95_MM = 1000.0`
    - `L_CENTROID_OFFSET_MM = 4860.147`
    - `Q1_STRICT_RATE = 0.0`
  - `TSDF`
    - `K_P95_MM = 1000.0`
    - `L_CENTROID_OFFSET_MM = 3643.712`
    - `Q1_STRICT_RATE = 0.00396`

- So the best current workpoint variant proves something important:
  - the `3x3` sequence can be pulled much closer to the healthy official regime at the front-end and depth-workpoint layers
  - but even after that large recovery, geometry still does not pass on our white-box evaluator
  - this is therefore no longer a pure config-replication problem
