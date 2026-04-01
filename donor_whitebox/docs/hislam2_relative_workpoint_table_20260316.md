# HI-SLAM2 Relative Workpoint Table (2026-03-16)

## Compared Runs

- Official baseline:
  - input traj: `/root/gs_refs/HI-SLAM2/data/Replica/room0/traj.txt`
  - output dir: `/root/donor_whitebox/outputs/hislam2_official_room0_r17_baseline`
- Our migrated full-official run:
  - input traj: `/root/donor_whitebox/outputs/hislam2_room3x3_seq_replica_like_2000f_r1/traj_gt.txt`
  - output dir: `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_replica_like_full_official_20260316_r2`

## Relative Metrics

| Metric | Official room0 | Our full_official_r2 |
| --- | ---: | ---: |
| frames | 2000 | 2000 |
| frames_per_meter | 81.0013 | 94.9522 |
| step_mean_m | 0.012352 | 0.010537 |
| step_p95_m | 0.015479 | 0.013205 |
| raw_depth_p50_m | 2.9941 | 0.5377 |
| step_mean / raw_depth_p50 | 0.00413 | 0.01959 |
| step_p95 / raw_depth_p50 | 0.00517 | 0.02456 |
| raw near ratio `<0.5m` mean | 0.0000 | 0.4627 |
| raw near ratio `<1.0m` mean | 0.0015 | 0.7828 |
| raw near ratio `<2.0m` mean | 0.1506 | 0.9775 |
| keyframe density | 0.0445 | 0.4375 |
| depth inflation p50 | 0.7644 | 9.7529 |

## Takeaway

- Motion cadence was brought close to official Replica room0.
- The remaining gap is dominated by scene/depth workpoint mismatch:
  - our scene is dramatically more near-field
  - raw near-pixel occupancy is much higher
  - optimized depth inflates much more strongly
  - keyframe density stays far above official
