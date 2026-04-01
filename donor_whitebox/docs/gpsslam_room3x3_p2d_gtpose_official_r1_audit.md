# GPS-SLAM room3x3 p2d gtpose official r1 audit

Date: 2026-03-15

## Scope

This note records the first complete official GPS-SLAM 2025 reproduction on the old machine for:

- official build/runtime viability on RTX 5090
- first full `r1` artifact quality
- official input contract audit

It does not change checkpoint gating by itself.

## Build and run state

- `ThirdLibs.zip` was downloaded and extracted into `/root/gs_refs/GPS-SLAM.clean/ThirdLibs`
- official build path completed on the old machine:
  - `build_third_libs.sh`
  - `cmake`
  - `make`
- full run completed:
  - output dir: `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_p2d_gtpose_official_r1`
  - config: `/root/donor_whitebox/configs/gpsslam_room3x3_p2d_gtpose_official_r1.yaml`
  - runner: `/root/donor_whitebox/scripts/remote_run_gpsslam_room3x3_p2d_gtpose_official.sh`

## Produced artifacts

- gaussian point cloud:
  - `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_p2d_gtpose_official_r1/gs_model/point_cloud/point_cloud.ply`
  - vertices: `939556`
- tsdf mesh:
  - `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_p2d_gtpose_official_r1/tsdf_mesh.ply`
  - vertices: `30754059`
  - faces: `10251353`
- rendered eval images:
  - `592` frames under `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_p2d_gtpose_official_r1/val/render`
- log facts:
  - `train finish`
  - `keyframe num: 15`
  - `save model, mesh and tsdf`
  - `render eval images`

## Color result

- `PSNR_FRAME_COUNT = 592`
- `PSNR_MEAN = 8.5068`
- `PSNR_MEDIAN = 8.1201`
- `PSNR_MIN = 6.3171`
- `PSNR_MAX = 13.1784`

## Geometry result

The raw GPS-SLAM outputs are in the local frame defined by `inv(pose000000) * c2w`.
For comparison against the room evaluator, the outputs must first be mapped back with `pose000000`.

### Gaussian point cloud after first-pose world alignment

- `K_P95_MM = 562.512`
- `K_P50_MM = 178.791`
- `K_MEAN_MM = 227.477`
- `L_CENTROID_OFFSET_MM = 207.656`
- `L_BBOX_EXTENT_M = 3.9258 x 3.0037 x 3.9736`
- `L_INROOM_RATE = 0.808544`
- `Q1_STRICT_RATE = 0.0`

### TSDF mesh after first-pose world alignment

- `K_P95_MM = 1000.0`
- `K_P50_MM = 141.167`
- `K_MEAN_MM = 248.194`
- `L_CENTROID_OFFSET_MM = 216.726`
- `L_BBOX_EXTENT_M = 4.0632 x 3.1238 x 3.9953`
- `L_INROOM_RATE = 0.608829`

### Interpretation

- First-pose world alignment fixes a real evaluation issue.
- After alignment, centroid offset becomes moderate (`~208-217 mm`) instead of multi-meter nonsense.
- Even after alignment, geometry remains far from the desired surface quality:
  - gaussian point cloud `K_P95` is still `562.512 mm`
  - tsdf mesh tail is worse, saturating at `1000.0 mm`

## Input contract audit

### Pose matrix convention

Confirmed to match official reader expectations:

- GPS-SLAM expects camera-to-world matrices
- reader normalizes them internally with:
  - `ref_c2w_pose = poseInv(init_c2w) * c2w_tensor`
- our converted `pose000000.txt` is a valid rigid transform:
  - `det(R) = 1.0`
  - orthonormal error is negligible

### Depth contract

Confirmed to match official reader expectations:

- stored as `uint16 png`
- shape `480 x 640`
- config uses `depth_scale: 5000`
- official reader does:
  - `cv::imread(..., IMREAD_UNCHANGED)`
  - convert to float
  - divide by `depth_scale`

Observed first-frame raw depth stats:

- `DEPTH_NONZERO_RATE = 1.0`
- `DEPTH_RAW_P50 = 7068`
- `DEPTH_RAW_P95 = 8564`
- `DEPTH_RAW_MAX = 9502`

### sampled_pcd_50w.ply

This file is missing in the converted dataset.

- config requests `pcd_name: sampled_pcd_50w.ply`
- reader tries to load it
- the real `r1` log says:
  - `no init scene point cloud.`

However, the current code trace shows:

- `scene_points.readPly(...)` exists only in dataset loading
- no downstream use of `scene_points` was found in the training path

Current conclusion:

- missing `sampled_pcd_50w.ply` is a real preprocess difference
- but it is not yet proven to be an active behavior difference for this run

### scene_scale

This is the strongest active preprocess/workpoint difference found so far.

GPS-SLAM does not derive scene scale from room extent or depth extent.
It recomputes it from camera translation spread only:

- `scene_centor = mean(camera translations)`
- `scene_scale = 1.1 * max_dist_from_scene_center`

Real `r1` log:

- `scene centor: -0.032917 -0.024103 0.071423`
- `scene scale: 0.091079`

Measured from the converted poses:

- `GPS_LOCAL_MAX_DIST_FROM_CENTER_M = 0.082799`
- `GPS_LOCAL_SCENE_SCALE_EXPECTED = 0.091079`
- source GT trajectory max distance from first frame:
  - `0.15055 m`

Current conclusion:

- this sequence is rotation-dominant with very small translation radius
- GPS-SLAM therefore runs with a very small `scene_scale`
- `scene_scale` directly affects optimizer lr and gaussian pruning/scale thresholds
- this is a real workpoint mismatch with the official indoor regime
- this is not a converter bug by itself

## Final conclusion of this audit round

- official GPS-SLAM 2025 build/runtime is real and working on the old machine
- first full `r1` artifact is real and currently not good enough
- first-pose world alignment was a real evaluator-side correction and has been accounted for
- no confirmed hard converter bug remains in:
  - pose matrix convention
  - depth contract
  - first-pose world alignment
- the missing `sampled_pcd_50w.ply` is real but not yet shown to affect the active training path
- the strongest remaining issue found in preprocess/workpoint space is:
  - tiny translation-driven `scene_scale = 0.091079`

This means GPS-SLAM should not be treated as a line that will become good simply by fixing the current converter unless a new hard preprocessing mismatch is found.

## Official indoor sample comparison

On `2026-03-15`, the official GPS-SLAM Indoor archive from the README Google Drive link was downloaded and the `activity_room` sample was extracted for direct comparison.

Extracted official sample path:

- `/root/gs_refs/GPS-SLAM.clean/data/data/gps_slam/activity_room`

Observed official sample facts:

- pose files: `2681`
- color frames: `2680`
- depth frames: `2680`
- `pose000000.txt` is identity
- depth image stats for `depth000000.png`:
  - `dtype = uint16`
  - `shape = 720 x 1280`
  - `nonzero rate = 0.916531`
  - `raw p50 = 1248`
  - `raw p95 = 2095`
  - `raw max = 2306`

Computed official trajectory spread from `activity_room/camera/pose*.txt`:

- `ACTIVITY_ROOM_MAX_DIST_FROM_CENTER_M = 3.260913`
- `ACTIVITY_ROOM_P95_DIST_FROM_CENTER_M = 2.635520`
- `ACTIVITY_ROOM_SCENE_SCALE_EXPECTED = 3.587004`
- `ACTIVITY_ROOM_MAX_DIST_FROM_FIRST_M = 4.535453`
- `ACTIVITY_ROOM_P95_DIST_FROM_FIRST_M = 4.495759`

Compared against our converted `room3x3_p2d_gtpose_r1`:

- our `GT_MAX_DIST_FROM_CENTER_M = 0.083178`
- our `GPS_LOCAL_SCENE_SCALE_EXPECTED = 0.091079`
- ratio:
  - official `activity_room` scene scale is about `39.4x` larger than ours

Interpretation:

- this is a very large official-vs-current workpoint gap
- the gap is not coming from a converter typo in pose or depth contract
- it comes from the trajectory regime itself:
  - official `activity_room` is translation-heavy
  - current `room3x3_p2d_gtpose_r1` is rotation-dominant with very small translation radius

Therefore, the new official evidence strengthens the conclusion that GPS-SLAM is currently outside its official indoor success regime mainly because of workpoint scale, not because of a confirmed converter bug.

## Existing exporter protocol comparison

To avoid inventing a new room-specific protocol, the existing `export_room_sequence` camera-path families were compared directly on the exact trajectory-spread quantities that GPS-SLAM uses.

- `handheld`
  - `MAX_DIST_FROM_CENTER_M = 1.885451`
  - `P95_DIST_FROM_CENTER_M = 1.684465`
  - `SCENE_SCALE_EXPECTED_M = 2.073996`
  - `MAX_DIST_FROM_FIRST_M = 2.679479`
- `monogs_tum`
  - `MAX_DIST_FROM_CENTER_M = 1.918836`
  - `P95_DIST_FROM_CENTER_M = 1.608552`
  - `SCENE_SCALE_EXPECTED_M = 2.110720`
  - `MAX_DIST_FROM_FIRST_M = 2.164259`
- `wildgs_tum_scan`
  - `MAX_DIST_FROM_CENTER_M = 1.838519`
  - `P95_DIST_FROM_CENTER_M = 1.642060`
  - `SCENE_SCALE_EXPECTED_M = 2.022371`
  - `MAX_DIST_FROM_FIRST_M = 2.882637`
- `wildgs_tum_static`
  - `MAX_DIST_FROM_CENTER_M = 0.081128`
  - `P95_DIST_FROM_CENTER_M = 0.081044`
  - `SCENE_SCALE_EXPECTED_M = 0.089241`
  - `MAX_DIST_FROM_FIRST_M = 0.150114`

Interpretation:

- all three non-static existing protocols already move GPS-SLAM into the `~2.0m` scene-scale regime
- `monogs_tum` is the closest existing non-hacky protocol to official `activity_room` on `MAX_DIST_FROM_CENTER`, which is the exact quantity that feeds GPS-SLAM `scene_scale`
- this makes `monogs_tum` the first protocol to try for a larger-translation recapture without changing the algorithm
