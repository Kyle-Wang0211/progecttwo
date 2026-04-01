# World-State Evaluator Alignment

## Purpose

Donor repos do not ship the product-layer objects needed for:

- `N`
- `Q2`
- `Q3`
- `Q4`
- `Q5`
- `P1`
- `P3`
- `V1`
- `V2`

Those checkpoints depend on product-owned UI/world-state concepts:

- tile geometry
- cell ownership
- tile state machine
- per-frame visibility
- coverage history
- gap / overlap history

The correct approach is:

1. Reuse product true-source naming and semantics where they already exist.
2. Emit one unified per-frame world-state record.
3. Run an automatic evaluator over that record.

This file defines the minimum schema and the mapping to product true-source modules.

Sample export:

- [/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/world_state_schema_example.json](/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/world_state_schema_example.json)

## Product True-Source Mapping

### Cell Identity

Reuse:

- [/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/geo/asc_cell.h](/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/geo/asc_cell.h)

Use:

- `AetherSTCellId.spatial` as the canonical spatial cell key
- `AetherSTCellId.temporal_bucket` when a temporal cell bucket is needed

For world-state JSON, encode `cell_id` as a stable string such as:

```text
"spatial:1234567890123456789|bucket:478901"
```

### Coverage / Monotonicity

Reuse:

- [/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/evidence/coverage_estimator.h](/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/evidence/coverage_estimator.h)
- [/Users/kaidongwang/Documents/Aether3D/Core/Evidence/PR3/GateCoverageTracker.swift](/Users/kaidongwang/Documents/Aether3D/Core/Evidence/PR3/GateCoverageTracker.swift)

Use:

- `CoverageResult.coverage` as the global coverage scalar for `V1`
- per-patch / per-cell angular coverage buckets as the source of visibility evidence

### State Machine

Reuse:

- [/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/evidence/evidence_state_machine.h](/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/evidence/evidence_state_machine.h)

That file exposes a monotonic state machine and an ordering function. The product UI checkpoint `P1` uses:

```text
empty -> provisional -> confirmed -> locked
```

The evaluator keeps that product-facing state order while borrowing the same monotonic principle already used in the evidence state machine.

### Tile Geometry

Reuse geometry helpers from:

- [/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/geo/map_tile_mesh.h](/Users/kaidongwang/Documents/Aether3D/aether_cpp/include/aether/geo/map_tile_mesh.h)

The evaluator expects each tile to provide:

- `center`
- `normal`
- `corners[4]`

That is enough to measure:

- support coverage (`Q2`)
- overlap (`Q4`)
- grid regularity (`Q5`)
- drift (`P2`)
- gap consistency (`V2`)

## Unified World-State JSON

The evaluator script reads a single JSON file with this shape:

```json
{
  "version": "aether_world_state_v1",
  "meta": {
    "scene_id": "room3x3",
    "cell_level": 0,
    "notes": "optional"
  },
  "frames": [
    {
      "frame_index": 0,
      "timestamp_s": 0.0,
      "coverage": 0.12,
      "tiles": [
        {
          "tile_id": "tile_0001",
          "cell_id": "spatial:123|bucket:456",
          "state": "confirmed",
          "visible": true,
          "center": [0.0, 0.0, 0.0],
          "normal": [0.0, 1.0, 0.0],
          "corners": [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 1.0],
            [0.0, 0.0, 1.0]
          ],
          "surface_center_distance_mm": 7.5,
          "surface_normal_dot": 0.92,
          "surface_corner_support": [true, true, true, false],
          "u": 3,
          "v": 4,
          "neighbors": [
            {
              "tile_id": "tile_0002",
              "gap_mm": 1.4
            }
          ]
        }
      ]
    }
  ]
}
```

You can validate a real export with:

```bash
python3 /Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/eval_world_state.py \
  /path/to/world_state.json --json
```

## Current Exporter Status

The exporter is now wired into product true-source:

- [/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift](/Users/kaidongwang/Documents/Aether3D/App/Scan/ScanViewModel.swift)

Current behavior:

- a sidecar JSON is written next to the exported PLY
- output path:

```text
Documents/Aether3D/exports/<UUID>.world_state.json
```

- current exported fields are derived from the product overlay geometry already exposed by the coordinator bridge
- the exporter reuses the same `recordId` as the PLY export so the artifacts stay paired

Current evaluator coverage from that exporter:

- runnable now:
  - `Q3`
  - `Q4`
  - `Q5`
  - `P1`
  - `P3`
  - `V1`
  - `V2`
- runnable when surface truth fields are added:
  - `Q1`
  - `Q2`

Current limitation:

- the exporter does not yet emit
  - `surface_center_distance_mm`
  - `surface_normal_dot`
  - `surface_corner_support`
- so `Q1/Q2` are still blocked on a surface-truth query layer, not on the JSON exporter itself

## Field Semantics

### Required top-level fields

- `version`
- `frames`

### Required frame fields

- `frame_index`
- `coverage`
- `tiles`

### Required tile fields

- `tile_id`
- `cell_id`
- `state`
- `visible`
- `center`
- `normal`
- `corners`

### Optional evaluator fields

- `surface_center_distance_mm`
- `surface_normal_dot`
- `surface_corner_support`
- `u`
- `v`
- `neighbors[].gap_mm`

If optional fields are missing, the evaluator marks the corresponding metric as unavailable instead of inventing data.

## Metric Mapping

### Q2 Support Coverage

Computation:

- Count supported corners from `surface_corner_support`
- Tile passes if at least 3 of 4 corners are supported

Threshold:

- tile pass condition: `supported_corners >= 3`
- global pass condition: pass rate `>= 80%`

### Q3 Unique Cell Ownership

Computation:

- For each frame, count duplicate `cell_id` among active tiles

Threshold:

- `duplicate_cells == 0`

### Q4 No Overlap

Computation:

- Project neighboring tile quads to a shared local 2D plane
- Compute polygon intersection area
- `overlap_ratio = intersection_area / min(area_a, area_b)`

Threshold:

- every pair `<= 1%`

### Q5 Grid Regularity

Computation:

- Use tile `(u, v)` indices
- Check uniqueness of `(u, v)`
- Check horizontal and vertical neighbor spacing variance
- Check regular-pair rate

Threshold:

- `regular_pair_rate >= 85%`

### P1 State Non-Regression

Computation:

- For each `tile_id`, compare state order across frames
- Disallow:
  - `confirmed -> provisional`
  - `confirmed -> empty`
  - `locked -> confirmed`
  - `locked -> provisional`
  - `locked -> empty`

Threshold:

- `state_regressions == 0`

### P3 Temporal Flicker

Computation:

- For each `tile_id` in state `confirmed` or `locked`
- In every 30-frame window, count visibility toggles

Threshold:

- toggles per 30-frame window `<= 1`

### V1 Coverage Monotonicity

Computation:

- Compare `coverage` across frames
- Flag regression if `coverage[t] + epsilon < coverage[t-1]`

Threshold:

- `coverage_regressions == 0`

### V2 Gap Consistency

Computation:

- Gather all `neighbors[].gap_mm`
- Compute global standard deviation

Threshold:

- `gap_stddev_mm <= 2.0`

## Recommended Source of Truth Per Field

Use product true-source outputs whenever available:

- `cell_id`
  - derived from `AetherSTCellId`
- `coverage`
  - derived from `CoverageResult.coverage`
- `state`
  - derived from product tile state machine
- `visible`
  - derived from product renderer / visibility pass
- `u, v`
  - derived from product local tangent-grid placement
- `corners / center / normal`
  - derived from product tile overlay geometry

## External Borrowing Targets

These are useful to borrow from, but none should replace the product world-state layer:

- [nvblox](https://github.com/nvidia-isaac/nvblox)
  - use for voxel / block ownership, occupancy bookkeeping, and coverage-style spatial indexing
- [voxblox_ground_truth](https://github.com/ethz-asl/voxblox_ground_truth)
  - use for mesh-to-ground-truth distance evaluation and surface-distance benchmarking
- [Open3D RaycastingScene](https://www.open3d.org/docs/latest/python_api/open3d.t.geometry.RaycastingScene.html)
  - use for nearest-surface distance, support tests, and point-to-surface queries
- [GenNBV](https://github.com/dmar-bonn/GenNBV)
  - use for next-best-view / coverage scheduling ideas for scan guidance

## Implementation Rule

Do not ask donor repos to emit product tiles.

Instead:

1. emit product-aligned world-state JSON
2. run evaluator
3. map evaluator output back to the white-box matrix
