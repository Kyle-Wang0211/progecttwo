# HI-SLAM2 Official vs Best Real Gap (2026-03-16)

## Scope

- Official baseline:
  - same-machine `HI-SLAM2 Replica room0`
  - fixed evidence root: `/root/donor_whitebox/outputs/hislam2_official_room0_r17_baseline`
- Best current real-sequence result:
  - `farther_plus_denser_full_official_best_v4`
  - evidence root: `/root/donor_whitebox/outputs/hislam2_workpoint_farther_plus_denser_full_official_best_v4`

## Comparable Metrics

| Metric | Official room0 | Best real (`farther_plus_denser v4`) | Gap / Direction | Reading |
| --- | ---: | ---: | ---: | --- |
| `traj_full` | `2000` | `3000` | `1.50x` | Not a problem by itself |
| `traj_kf` | `89` | `267` | `3.00x` | Much more keyframes on our real sequence |
| `keyframe density` | `4.45%` | `8.90%` | `2.00x` | Still clearly above official healthy regime |
| `mean_psnr` | `35.1794` | `36.1800` | `+1.0006 dB` | Render is already better than official room0 |
| `mean_ssim` | `0.9584` | `0.9806` | `+0.0222` | Render is already better than official room0 |
| `frames_per_meter` | `81.0013` | `197.8405` | `2.44x denser` | Sampling is denser than official, not sparser |
| `step_mean_m` | `0.012352` | `0.005056` | `0.41x` | We are moving less per frame than official |
| `step_p95_m` | `0.015479` | `0.006338` | `0.41x` | Same trend: motion itself is no longer aggressive |
| `raw_depth_p50_m` | `2.9941` | `0.9167` | `3.27x nearer` | Biggest remaining capture-side gap |
| `step_mean / raw_depth_p50` | `0.00413` | `0.00552` | `1.34x` | Relative motion is now fairly close |
| `step_p95 / raw_depth_p50` | `0.00517` | `0.00691` | `1.34x` | Relative motion is now fairly close |
| `depth inflation p50` | `0.7644x` | `0.9236x` | `1.21x` | Much healthier now; no longer the first blocker |

## Geometry Outcome

These are the current best real-sequence geometry outcomes on our white-box evaluator.

| Metric | Best real 3DGS | Best real TSDF | Reading |
| --- | ---: | ---: | --- |
| `K_P95_MM` | `1000.0` | `1000.0` | Still hard fail |
| `L_CENTROID_OFFSET_MM` | `4860.147` | `3643.712` | Better than older runs, but still meters away |
| `Q1_STRICT_RATE` | `0.0` | `0.00396` | Still essentially fail |

Important note:
- We do **not** have same-evaluator `K/L/Q1` for official `room0`.
- But on the same old machine, official `room0` is already healthy on tracking + rendering, while our best real run is still failing geometry by a large margin.

## What Is No Longer The Main Problem

- Not primarily `speed / jitter`
  - `step_mean / raw_depth_p50` is already only about `1.34x` off official.
- Not primarily `render`
  - `PSNR` and `SSIM` are already above official `room0`.
- Not primarily `sampling density`
  - we are already denser than official in `frames_per_meter`.
- Not primarily “official config not reproduced”
  - we already reproduced official benchmark on the same machine, and the best real workpoint variant recovered strongly without changing algorithm logic.

## What Is Most Likely Still Wrong

### Priority 1: Scene is still too near-field

The strongest remaining capture-side mismatch is:
- `raw_depth_p50: 2.9941m -> 0.9167m`

This means our best real run is still seeing a scene that is about `3.27x` nearer than official `room0`.

Interpretation:
- The camera is still too close to surfaces for too much of the sequence.
- We still do not have enough mid/far-range pixels dominating the view.
- This is the most likely reason geometry still fails even after motion/cadence and depth inflation recovered.

### Priority 2: Global room anchors are probably still insufficient

Because:
- keyframe density is still `2x` official (`8.9%` vs `4.45%`)
- geometry is still meters off
- render is already good

Interpretation:
- The system can render the scene stably
- but it still does not get enough strong global geometric constraints:
  - opposite walls
  - diagonals across the room
  - corners
  - floor-wall-ceiling junctions
  - revisit closure from separated viewpoints

### Priority 3: Compact `3x3` scene may itself be outside the easiest success regime

Even after aggressive workpoint repair:
- front-end behavior recovered a lot
- depth inflation recovered a lot
- geometry still fails

Interpretation:
- This is now consistent with the possibility that `HI-SLAM2` is simply much happier in larger, less near-field indoor scenes than in this compact `3x3` setup.

## Product-Side Recommendation

If we must pass all geometry tests, the next thing to optimize should be capture/scene protocol, not donor config:

1. Push camera farther from surfaces on average.
2. Increase center-of-room and diagonal coverage.
3. Make opposite-wall / corner / ceiling-floor junction coverage mandatory.
4. Reduce sequences dominated by close wall-facing shots.
5. End with a strong revisit/closure pass over early poses.

If those changes still cannot bring geometry close, then the likely product conclusion is:
- this donor is not suitable for this compact near-field `3x3` workpoint, even though it is fully healthy on official `Replica`.
