# UI Borrowing And Donor Workpoint Playbook

Current date: 2026-03-12

This file answers two practical questions:

1. what can be copied directly into the product-owned UI/world-state layer
2. how donor/workpoint failures should be debugged without drifting into `3x3`-specific hacks

## Rule Zero

Do not special-case the algorithm for the `3x3` room.

Allowed:

- runtime compatibility fixes
- parser / camera / cadence alignment
- benchmark-faithful input reconstruction
- product-owned evaluator / state export

Not allowed:

- room-size-specific thresholds
- texture-specific hacks
- logic that only works for the current `3x3` scene

Any algorithm-side change should satisfy both:

1. official benchmark does not regress
2. `3x3` white-box result improves

## Product-Owned Layer: What To Copy

### nvblox

Use it for:

- cell / block ownership
- spatial indexing
- coverage bookkeeping
- stable occupancy-style bookkeeping for `N`, `Q3`, `V1`

Do not use it to replace:

- tile state machine
- UI tile semantics

Most relevant ideas to copy:

- voxel/block keys as stable ownership ids
- per-block update bookkeeping
- occupancy-driven coverage accumulation

### voxblox_ground_truth

Use it for:

- distance-to-surface evaluation
- comparing generated geometry against a known reference surface

This directly serves:

- `K`
- `Q1`
- `Q2`

Most relevant idea to copy:

- nearest-surface error query against a ground-truth mesh / surface

### Open3D RaycastingScene

Use it for:

- nearest-surface distance queries
- support checks for corners
- overlap prechecks
- point-to-surface and ray hit tests

This directly serves:

- `Q1`
- `Q2`
- `Q4`
- part of `K`

Most relevant idea to copy:

- treat the current room surface as a queryable scene and evaluate tiles against it frame by frame

### GenNBV

Use it for:

- scan guidance
- coverage growth heuristics
- candidate-view ranking

Do not use it to replace:

- tile ownership
- tile state machine

This serves:

- capture guidance
- coverage monotonicity support
- operator feedback during scan

## Current Product Export Status

The product exporter now lives in:

- [/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift](/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift)

It currently exports:

- `frame_index`
- `timestamp_s`
- `coverage`
- `tile_id`
- `cell_id`
- `state`
- `visible`
- `center`
- `normal`
- `corners`
- `u`
- `v`
- `neighbors[].gap_mm`

That is already enough for:

- `Q3`
- `Q4`
- `Q5`
- `P1`
- `P3`
- `V1`
- `V2`

Still missing for `Q1/Q2`:

- `surface_center_distance_mm`
- `surface_normal_dot`
- `surface_corner_support`

## Donor / Workpoint Resolution

### Photo-SLAM

Role:

- main mapper / densify / color path
- strongest donor for `F/G/I/J`

Keep:

- official mapper logic
- long-running densify / training window behavior

Do not:

- retune it specifically for `3x3`
- use it as the source of tile state

### WildGS

Role:

- geometry correction donor
- strongest path for `K/L/Q1/P2`

How to debug when `3x3` still fails:

1. first confirm official benchmark still passes
2. compare parser family
3. compare camera model
4. compare motion cadence
5. compare depth prior distribution
6. compare texture / gradient statistics

If official benchmark passes and `3x3` fails:

- treat the issue as a workpoint mismatch first
- do not immediately rewrite the algorithm

### MonoGS

Role:

- trigger / window / backpressure reference
- donor for `D/O` structure, not the final low-drift mapper

Do not force MonoGS to become the final geometry solution if:

- official benchmark stays cm-level
- but `3x3` still drifts badly

That means:

- keep the trigger/window lessons
- stop using it as the final geometry truth source

### HI-SLAM2

Role:

- baseline full-path / integrated-frame sanity
- additional tracking/backend reference

Use it for:

- `O`
- full trajectory evidence

Do not depend on it as the only answer for:

- surface adhesion
- final geometry quality

## Execution Order For Unpassed Items

1. keep `Photo-SLAM` as the passed main mapper for `I/J`
2. keep `MonoGS` only as `D/O` mechanism reference
3. drive `WildGS` with benchmark-faithful inputs for `K/L/Q1/P2`
4. export product world-state JSON and evaluate:
   - `Q3`
   - `Q4`
   - `Q5`
   - `P1`
   - `P3`
   - `V1`
   - `V2`
5. add surface-truth fields so the same evaluator can also do:
   - `Q1`
   - `Q2`

## What "Done" Means

The work is only acceptable when:

- official benchmark behavior remains healthy
- `3x3` white-box results improve
- no change depends on knowing the room is `3x3`
- product UI / world-state can export a stable JSON record
- the evaluator can run without manual interpretation
