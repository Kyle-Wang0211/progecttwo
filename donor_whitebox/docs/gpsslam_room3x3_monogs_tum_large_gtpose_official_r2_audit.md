# GPS-SLAM room3x3 monogs_tum large gtpose official r2 audit

Date: 2026-03-15

## Scope

This note records the first larger-translation recapture for GPS-SLAM using an existing non-hacky exporter protocol:

- exporter protocol: `monogs_tum`
- algorithm: unchanged official GPS-SLAM 2025
- objective: move the trajectory spread much closer to official Indoor `scene_scale` without changing the algorithm

## Protocol choice

The exporter protocol was not invented for this room.

Among the existing protocols, `monogs_tum` is the closest to official `activity_room` on the exact trajectory quantity that GPS-SLAM uses:

- official `activity_room`
  - `SCENE_SCALE_EXPECTED_M = 3.587004`
- current `monogs_tum` recapture
  - `SCENE_SCALE_EXPECTED_M = 2.111066`
- previous tiny-translation `p2d` baseline
  - `SCENE_SCALE_EXPECTED_M = 0.091079`

Real recaptured trajectory spread:

- `frames = 600`
- `MAX_DIST_FROM_CENTER_M = 1.919151`
- `P95_DIST_FROM_CENTER_M = 1.610831`
- `SCENE_SCALE_EXPECTED_M = 2.111066`
- `MAX_DIST_FROM_FIRST_M = 2.163448`
- `P95_DIST_FROM_FIRST_M = 1.894871`

## Run state

- runner:
  - `/root/donor_whitebox/scripts/remote_run_gpsslam_room3x3_monogs_tum_large_gtpose_official.sh`
- config:
  - `/root/donor_whitebox/configs/gpsslam_room3x3_monogs_tum_large_gtpose_official_r2.yaml`
- workspace:
  - `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_monogs_tum_large_gtpose_official_r2`

The full official run completed successfully:

- `train finish`
- `keyframe num: 78`
- `save model, mesh and tsdf`
- `render eval images`

## Produced artifacts

- gaussian point cloud:
  - `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_monogs_tum_large_gtpose_official_r2/gs_model/point_cloud/point_cloud.ply`
  - format: `binary_little_endian`
  - vertices: `578098`
- tsdf mesh:
  - `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_monogs_tum_large_gtpose_official_r2/tsdf_mesh.ply`
  - format: `ascii`
  - vertices: `15487869`
  - faces: `5162623`

## Color result

PSNR was recomputed directly from:

- GT: `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_monogs_tum_large_gtpose_official_r2/val/gt`
- render: `/root/donor_whitebox/outputs/gpsslam_runs/room3x3_monogs_tum_large_gtpose_official_r2/val/render`

Observed values:

- `PSNR_FRAME_COUNT = 600`
- `PSNR_MEAN = 7.0053`
- `PSNR_MEDIAN = 7.0560`
- `PSNR_MIN = 3.0687`
- `PSNR_MAX = 10.9323`
- `PSNR_LAST = 4.5656`

Compared with `r1`:

- `r1 PSNR_MEAN = 8.5068`
- `r2 PSNR_MEAN = 7.0053`

Current conclusion:

- larger translation did not improve final color fidelity on this run
- color got worse versus the smaller-translation `r1` baseline

## Geometry result

As with `r1`, GPS-SLAM outputs were evaluated only after mapping them back from local GPS coordinates with `pose000000`.

### Gaussian point cloud after first-pose world alignment

Evaluated on a `100000` point sample.

- `POINTS_EVAL = 100000`
- `L_BBOX_EXTENT_M = 4.5668 x 3.7061 x 6.3362`
- `L_CENTROID_OFFSET_MM = 1387.186`
- `L_INROOM_RATE = 0.408700`
- `K_P50_MM = 293.192`
- `K_P95_MM = 1000.0`
- `K_MEAN_MM = 479.533`
- `Q1_STRICT_RATE = 0.0`

### TSDF mesh after first-pose world alignment

Evaluated on a `50000` vertex sample.

- `POINTS_EVAL = 50000`
- `L_BBOX_EXTENT_M = 4.1620 x 3.6330 x 6.3552`
- `L_CENTROID_OFFSET_MM = 762.602`
- `L_INROOM_RATE = 0.202780`
- `K_P50_MM = 1000.0`
- `K_P95_MM = 1000.0`
- `K_MEAN_MM = 653.285`

## Interpretation

This experiment answered the main workpoint question directly:

- yes, the new sequence really moved GPS-SLAM from the tiny `scene_scale ~0.09m` regime to a much larger `scene_scale ~2.11m` regime
- no, this larger-translation recapture did not fix final artifact fidelity

Compared with `r1`:

- `keyframe num` increased strongly:
  - `15 -> 78`
- point-cloud geometry became worse, not better:
  - `K_P95_MM: 562.512 -> 1000.0`
  - `L_CENTROID_OFFSET_MM: 207.656 -> 1387.186`
- TSDF mesh also remained poor:
  - `K_P95_MM = 1000.0`
  - `L_CENTROID_OFFSET_MM = 762.602`
- color also got worse:
  - `PSNR_MEAN: 8.5068 -> 7.0053`

## Final conclusion of this round

This run is strong evidence that GPS-SLAM on our room3x3 data is not mainly blocked by the previously identified tiny-translation trajectory regime.

After moving to a much larger existing exporter protocol without changing the algorithm:

- the run completed cleanly
- the workpoint scale gap was reduced substantially
- but final color, final volume, and final geometry all still failed to become realistic

Therefore GPS-SLAM should not be treated as a line that will become good merely by moving from small translation to larger translation within the current procedural room generator.
