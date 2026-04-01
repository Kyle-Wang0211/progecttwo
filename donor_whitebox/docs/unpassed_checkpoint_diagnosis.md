# Unpassed Checkpoint Diagnosis

Current date: 2026-03-15

This file separates current unpassed / unverified checkpoints into:

- `copy_gap`: we have not yet matched the donor or official benchmark setup closely enough
- `official_diff`: the local tree still differs from upstream algorithm behavior
- `env_adaptation`: runtime / CUDA / ABI / multiprocessing compatibility work still matters
- `data_workpoint`: donor code is healthy, but the current `3x3` data distribution is far from the official working point
- `product_state_missing`: donor repos do not emit the required product-layer objects
- `evaluator_missing`: data may exist, but there is no pass/fail evaluator yet

Official benchmark sanity checks already established:

- `WildGS official TUM static`: full-traj RMSE about `2.52cm`
- `MonoGS official fr3_office`: final RMSE about `3.73cm`
- `Photo-SLAM`: `I/J` already passed in local white-box runs

Those results mean the donor code/env mainline is not fundamentally broken. Remaining failures are now more concentrated in data workpoint and product-state layers.

## A-Q

| Checkpoint | Current status | Primary donor / source | Cause bucket | Current strongest evidence | Next action |
| --- | --- | --- | --- | --- | --- |
| B | unverified | product pipeline | `evaluator_missing` | No donor burst/backpressure stress output | Add a burst/queue replay input to the product-side evaluator; donor repos do not provide this directly. |
| D | failed | `MonoGS` | `data_workpoint` + `official_diff` | Official benchmark is cm-level, but `3x3` runs still drift badly; keyframe gate behavior is still not matching a healthy product trigger path | Keep `MonoGS` as trigger/window reference only; compare `is_keyframe()/add_to_window()` behavior against product trigger logic instead of forcing MonoGS to be the final low-drift mapper. |
| K | failed | `WildGS` + `HI-SLAM2` | `data_workpoint` | `WildGS` latest current report stays at `K_P95_MM=906.854`; `HI-SLAM2 r6b` now also completes end-to-end on our `3x3`, but still lands at `3DGS K_P95_MM=1000.0`, `TSDF K_P95_MM=1000.0` | Hold `r6b` as the first real `HI-SLAM2 on our 3x3` baseline and compare it directly against official `Replica room0`; focus on why the migrated sequence drives geometry far off the true surface despite a fully running chain. |
| L | failed | `WildGS` + `HI-SLAM2` | `data_workpoint` | `WildGS` latest current report stays at `L=520.565mm`; `HI-SLAM2 r6b` now fully runs but still gives `3DGS L=3154.568mm`, `TSDF L=2648.299mm`, with `in-room rate` still very low | Use the same `r6b` baseline to investigate why official `Replica` keeps a healthy map while our `3x3` sequence explodes in extent / centroid even after the runtime chain is fully unblocked. |
| M | unverified | product TSDF | `product_state_missing` | Donor repos do not output TSDF active blocks | Export TSDF block count from product true-source and bind it into the white-box report. |
| N | unverified | product UI | `product_state_missing` | No tile hit-rate world state exists in donor outputs | Export tile/cell assignment stats from product UI/world-state. |
| P (coarse) | failed | product state machine | `product_state_missing` + `evaluator_missing` | Split into `P1/P2/P3`; not all sub-items are passing | Use the JSON world-state export and run `eval_world_state.py` plus product-side `P2` geometry evidence. |
| Q (coarse) | failed | product tile/surface layer | `product_state_missing` + `evaluator_missing` | Split into `Q1-Q5`; `Q1` fails, `Q2-Q5` still need product export | Same as above. |

## Q1-Q5 / P1-P3 / V1-V2

| Checkpoint | Current status | Primary donor / source | Cause bucket | Current strongest evidence | Next action |
| --- | --- | --- | --- | --- | --- |
| Q1 | failed | `WildGS` + `HI-SLAM2` | `data_workpoint` + `evaluator_missing` | Donor-side geometry still fails in both lines: current `WildGS` remains at `Q1_STRICT_RATE = 0.0`; new `HI-SLAM2 r6b` completes but only reaches `3DGS Q1_STRICT_RATE = 0.0`, `TSDF Q1_STRICT_RATE = 0.00442` | Keep product-side `world_state` evaluation for product `Q1`, but donor-side diagnosis should now treat `r6b` as a second hard proof that the migrated `3x3` workpoint still does not adhere to the real surface. |
| Q2 | unverified | product tile geometry | `product_state_missing` | No `surface_corner_support` export yet | Export per-tile `surface_corner_support[4]` and run `eval_world_state.py`. |
| Q3 | unverified | product cell ownership | `product_state_missing` | No stable per-frame `cell_id` export yet | Export `cell_id` aligned to `AetherSTCellId` and run `eval_world_state.py`. |
| Q4 | unverified | product tile geometry | `product_state_missing` | No per-frame tile corner export yet | Export `corners[4]` and run `eval_world_state.py` overlap checks. |
| Q5 | unverified | product grid state | `product_state_missing` | No `(u,v)` grid export yet | Export tile `(u,v)` plus centers/corners and run `eval_world_state.py`. |
| P1 | unverified | product state machine | `product_state_missing` | No per-frame tile `state` history export yet | Export tile state history and run `eval_world_state.py`. |
| P2 | failed | `WildGS` + product camera motion | `data_workpoint` | Latest `3x3` runs still report `P2_STATIC_WINDOWS = 0` or insufficient static evidence | Capture / synthesize a true near-static validation segment; current handheld trajectory is too dynamic for strict `P2`. |
| P3 | unverified | product visibility history | `product_state_missing` | No per-frame `visible` history export yet | Export `visible` per tile per frame and run `eval_world_state.py`. |
| V1 | unverified | product coverage state | `product_state_missing` | No per-frame `coverage` export yet | Export frame-level coverage and run `eval_world_state.py`. |
| V2 | unverified | product gap bookkeeping | `product_state_missing` | No per-neighbor `gap_mm` export yet | Export neighbor gap measurements and run `eval_world_state.py`. |

## What Is Already Good Enough To Reuse

- `Photo-SLAM`
  - `I/J` are already passed
  - can remain the strongest donor proof for main mapper / densify / color output
- `WildGS official benchmark`
  - proves the code/env can achieve cm-level tracking on the official working point
- `MonoGS official benchmark`
  - proves the trigger/window code path is not fundamentally broken on its own benchmark
- `HI-SLAM2 official Replica`
  - full `Replica` benchmark is already proven on the same old machine
  - official `room0` baseline reaches:
    - `mean_psnr = 35.1794`
    - `mean_ssim = 0.9584`
    - `traj_full = 2000`
    - `traj_kf = 89`
  - this means the remaining `HI-SLAM2 -> 3x3` failure is no longer "official chain cannot run here"

## New HI-SLAM2 3x3 Baseline

The first fully completed `HI-SLAM2 on our 3x3 room` baseline is now:

- audit:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/hislam2_room3x3_official_runtime_r6b_audit.md`
- remote output:
  - `/root/donor_whitebox/outputs/hislam2_room3x3_official_runtime_20260315_r6b`

What this baseline proves:

- the migrated `HI-SLAM2` chain now runs to completion on our target sequence
- the chain emits:
  - `traj_full`
  - `3dgs_final`
  - `renderings`
  - `tsdf mesh`
- but geometry still fails badly:
  - `3DGS K_P95_MM = 1000.0`
  - `TSDF K_P95_MM = 1000.0`
  - `3DGS L_CENTROID_OFFSET_MM = 3154.568`
  - `TSDF L_CENTROID_OFFSET_MM = 2648.299`
  - `Q1_STRICT_RATE = 0.0 ~ 0.00442`

Current implication:

- the next `HI-SLAM2` investigation should stay on this already-running chain
- and focus on why official `Replica` stays in a healthy regime while our migrated `3x3` sequence does not

## Current Product-Owned Bridge

To unlock the missing product-state checkpoints, use:

- Schema/alignment doc:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/world_state_evaluator_alignment.md`
- Example JSON:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/world_state_schema_example.json`
- Evaluator:
  - `/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/eval_world_state.py`

Example command:

```bash
python3 /Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/eval_world_state.py \
  /path/to/world_state.json --json
```
