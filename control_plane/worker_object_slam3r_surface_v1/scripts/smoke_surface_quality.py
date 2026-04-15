#!/usr/bin/env python3
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
import trimesh

try:
    from control_plane.worker_object_slam3r_surface_v1.adapters.contract_bridge_adapter import (
        _greedy_diverse_selection,
    )
    import control_plane.worker_object_slam3r_surface_v1.adapters.default_delivery_adapter as dda
    from control_plane.worker_object_slam3r_surface_v1.pipeline.curate_frames import (
        _FrameMetrics,
        _supplement_temporal_coverage,
    )
    from control_plane.worker_object_slam3r_surface_v1.config import config
except ModuleNotFoundError:
    from worker_object_slam3r_surface_v1.adapters.contract_bridge_adapter import (
        _greedy_diverse_selection,
    )
    import worker_object_slam3r_surface_v1.adapters.default_delivery_adapter as dda
    from worker_object_slam3r_surface_v1.pipeline.curate_frames import (
        _FrameMetrics,
        _supplement_temporal_coverage,
    )
    from worker_object_slam3r_surface_v1.config import config


@dataclass
class SmokeResult:
    name: str
    passed: bool
    metrics: dict[str, Any]
    notes: list[str]


@contextmanager
def _override_config(**overrides: Any):
    originals = {key: getattr(config, key) for key in overrides}
    try:
        for key, value in overrides.items():
            object.__setattr__(config, key, value)
        yield
    finally:
        for key, value in originals.items():
            object.__setattr__(config, key, value)


def _geometry_selection_smoke() -> SmokeResult:
    frame_count = 12
    target_count = 8
    angles = np.deg2rad(
        np.array([0, 10, 20, 30, 90, 135, 180, 225, 270, 315, 45, 160], dtype=np.float64)
    )
    center_dirs = np.stack(
        [np.cos(angles), np.sin(angles), np.zeros_like(angles)],
        axis=1,
    )
    center_dirs /= np.linalg.norm(center_dirs, axis=1, keepdims=True)
    sector_ids = np.array([0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 3], dtype=np.int64)
    valid_mask = np.ones(frame_count, dtype=bool)
    quality_scores = np.array(
        [1.0, 0.98, 0.96, 0.94, 0.25, 0.24, 0.23, 0.22, 0.21, 0.20, 0.19, 0.18],
        dtype=np.float64,
    )
    descriptors = center_dirs.copy()
    selected = _greedy_diverse_selection(
        descriptors=descriptors,
        target_count=target_count,
        quality_scores=quality_scores,
        geometry_context={
            "center_dirs": center_dirs,
            "valid_mask": valid_mask,
            "sector_ids": sector_ids,
        },
    )
    selected_sectors = sorted({int(sector_ids[index]) for index in selected})
    selected_dirs = center_dirs[selected]
    angle_matrix = np.degrees(
        np.arccos(np.clip(selected_dirs @ selected_dirs.T, -1.0, 1.0))
    )
    np.fill_diagonal(angle_matrix, 180.0)
    min_angles = angle_matrix.min(axis=1)
    sector_gate = int(config.geometry_hq_min_coverage_sectors)
    baseline_gate = float(config.geometry_hq_min_baseline_median_deg)
    metrics = {
        "selected": selected,
        "selected_sector_count": len(selected_sectors),
        "selected_sectors": selected_sectors,
        "median_min_angle_deg": round(float(np.median(min_angles)), 3),
        "target_sector_gate": sector_gate,
        "target_baseline_gate_deg": baseline_gate,
    }
    passed = bool(
        metrics["selected_sector_count"] >= sector_gate
        and metrics["median_min_angle_deg"] >= baseline_gate
    )
    return SmokeResult(
        name="geometry_selection",
        passed=passed,
        metrics=metrics,
        notes=[
            "Synthetic cluster-heavy input should still force broad sector coverage.",
            "This guards against keeping too many nearly identical high-quality views.",
        ],
    )


def _curate_temporal_coverage_smoke() -> SmokeResult:
    frames: list[_FrameMetrics] = []
    for index in range(12):
        frames.append(
            _FrameMetrics(
                path=Path(f"{index:04d}.jpg"),
                blur_score=40.0 + index,
                mean_brightness=128.0,
                global_variance=18.0 + index,
                orb_feature_count=900 + index,
                target_texture_score=0.6 + index * 0.01,
                target_contrast_score=0.55 + index * 0.01,
                signature=bytes([index]) * 16,
            )
        )
    # Simulate novelty_filter keeping only the first half of orbit sectors.
    accepted = [frames[index] for index in [0, 1, 2, 3, 4, 5]]
    supplemented = _supplement_temporal_coverage(
        frames=frames,
        accepted=accepted,
        max_frames=12,
        sector_count=8,
    )
    order_by_name = {frame.path.name: index for index, frame in enumerate(frames)}
    sectors = {
        min(7, int(order_by_name[frame.path.name] * 8 / len(frames)))
        for frame in supplemented
    }
    metrics = {
        "accepted_before": [frame.path.name for frame in accepted],
        "accepted_after": [frame.path.name for frame in supplemented],
        "sector_count_after": len(sectors),
    }
    return SmokeResult(
        name="curate_temporal_coverage",
        passed=bool(len(sectors) >= 7),
        metrics=metrics,
        notes=[
            "Server-side curate should supplement missing orbit sectors instead of only keeping novelty-sorted frames.",
        ],
    )


def _curate_live_selection_hq_backfill_smoke() -> SmokeResult:
    frames: list[_FrameMetrics] = []
    for index in range(24):
        frames.append(
            _FrameMetrics(
                path=Path(f"{index:04d}.jpg"),
                blur_score=45.0 + index * 0.1,
                mean_brightness=126.0,
                global_variance=22.0 + index * 0.2,
                orb_feature_count=950 + index,
                target_texture_score=0.65 + index * 0.003,
                target_contrast_score=0.60 + index * 0.002,
                signature=bytes([index % 251]) * 16,
            )
        )

    # Simulate a live-captured set that keeps 24 valid frames but clusters them into only
    # six temporal sectors, leaving a long uncovered gap in the orbit.
    accepted_indices = [0, 1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 18, 19, 20, 21, 22, 23]
    accepted = [frames[index] for index in accepted_indices]
    accepted_before = _supplement_temporal_coverage(
        frames=frames,
        accepted=accepted,
        max_frames=24,
        sector_count=8,
    )
    sector_index = {frame.path.name: index for index, frame in enumerate(frames)}
    sectors_after = {
        min(7, int(sector_index[frame.path.name] * 8 / len(frames))) for frame in accepted_before
    }
    metrics = {
        "accepted_before_count": len(accepted),
        "accepted_after_count": len(accepted_before),
        "accepted_after": [frame.path.name for frame in accepted_before],
        "sector_count_after": len(sectors_after),
        "selection_policy_expected": "client_live_selected_plus_temporal_coverage_hq",
    }
    return SmokeResult(
        name="curate_live_selection_hq_backfill",
        passed=bool(len(sectors_after) >= 7 and len(accepted_before) > len(accepted)),
        metrics=metrics,
        notes=[
            "When client live selection preserves all valid HQ frames but leaves orbit gaps, server-side HQ backfill should add missing sectors instead of accepting clustered coverage.",
        ],
    )


def _shape_preserving_simplify_smoke() -> SmokeResult:
    reference_mesh = trimesh.creation.icosphere(subdivisions=3, radius=1.0)
    bad_candidate = reference_mesh.copy()
    bad_candidate.apply_scale([1.0, 1.0, 0.35])
    good_candidate = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
    first_target = 150

    def fake_simplify(mesh, *, target_faces):
        return bad_candidate.copy() if target_faces <= first_target else good_candidate.copy()

    with mock.patch.object(dda, "_simplify_mesh", side_effect=fake_simplify):
        simplified, summary = dda._shape_preserving_simplify_mesh(
            reference_mesh,
            reference_mesh=reference_mesh,
            target_faces=first_target,
            min_extent_retention_floor=float(config.mesh_fidelity_hq_min_extent_retention),
            min_surface_area_retention_floor=float(
                config.mesh_fidelity_hq_min_surface_area_retention
            ),
        )
    fidelity = dda._compute_mesh_fidelity_metrics(
        reference_mesh=reference_mesh,
        candidate_mesh=simplified,
    )
    metrics = {
        "accepted": bool(summary.get("accepted")),
        "reason": summary.get("reason"),
        "attempted_targets": summary.get("attempted_targets"),
        "accepted_target": summary.get("accepted_target"),
        "result_faces": int(len(simplified.faces)),
        "min_extent_retention": round(float(fidelity["min_extent_retention"]), 5),
        "surface_area_retention": round(float(fidelity["surface_area_retention"]), 5),
    }
    passed = bool(
        metrics["accepted"]
        and metrics["accepted_target"] != first_target
        and metrics["min_extent_retention"] >= float(config.mesh_fidelity_hq_min_extent_retention)
    )
    return SmokeResult(
        name="shape_preserving_simplify",
        passed=passed,
        metrics=metrics,
        notes=[
            "A flattened candidate should be rejected.",
            "A less aggressive fallback should be accepted once it preserves extent and area.",
        ],
    )


def _drop_small_components_smoke() -> SmokeResult:
    main = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
    small = trimesh.creation.icosphere(subdivisions=1, radius=0.25)
    small.apply_translation([3.0, 0.0, 0.0])
    combo = trimesh.util.concatenate([main, small])
    reduced = dda._drop_small_components(combo.copy())
    combo_bounds = dda._mesh_bounds(combo)
    reduced_bounds = dda._mesh_bounds(reduced)
    retention = dda._bounds_extent_retention(
        reference_bounds=combo_bounds,
        candidate_bounds=reduced_bounds,
    )
    metrics = {
        "input_faces": int(len(combo.faces)),
        "output_faces": int(len(reduced.faces)),
        "extent_retention": [round(float(value), 5) for value in retention.tolist()],
        "kept_small_component_signal": bool(len(reduced.faces) > len(main.faces)),
    }
    passed = bool(
        metrics["kept_small_component_signal"]
        and min(metrics["extent_retention"]) >= max(0.55, float(config.mesh_fidelity_hq_min_extent_retention) * 0.95)
    )
    return SmokeResult(
        name="drop_small_components",
        passed=passed,
        metrics=metrics,
        notes=[
            "A small discarded component should still be kept when it protects bbox extent.",
        ],
    )


def _self_intersection_cleanup_smoke() -> SmokeResult:
    vertices = np.array(
        [
            [-1.0, -1.0, 0.0],
            [1.0, -1.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, -1.0, -1.0],
            [0.0, 1.0, -1.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    ratio_before = dda._estimate_self_intersection_ratio(mesh)
    cleaned, summary = dda._reduce_self_intersections(mesh.copy())
    ratio_after = dda._estimate_self_intersection_ratio(cleaned)
    metrics = {
        "ratio_before": round(float(ratio_before), 5),
        "ratio_after": round(float(ratio_after), 5),
        "removed_faces": int(summary.get("removed_faces", 0)),
        "applied": bool(summary.get("applied")),
    }
    passed = bool(metrics["applied"] and metrics["ratio_after"] < metrics["ratio_before"])
    return SmokeResult(
        name="self_intersection_cleanup",
        passed=passed,
        metrics=metrics,
        notes=[
            "Two intersecting triangles should trip the detector and be reduced to zero intersection ratio.",
        ],
    )


def _multilayer_prune_smoke() -> SmokeResult:
    front = trimesh.creation.box(extents=[2.0, 2.0, 0.05])
    rear = front.copy()
    rear.apply_translation([0.0, 0.0, 0.01])
    combo = trimesh.util.concatenate([front, rear])
    ratio_before = dda._estimate_self_intersection_ratio(combo)
    pruned, summary = dda._prune_multilayer_faces(combo.copy())
    ratio_after = dda._estimate_self_intersection_ratio(pruned)
    metrics = {
        "ratio_before": round(float(ratio_before), 5),
        "ratio_after": round(float(ratio_after), 5),
        "removed_faces": int(summary.get("removed_faces", 0)),
        "applied": bool(summary.get("applied")),
    }
    return SmokeResult(
        name="multilayer_prune",
        passed=bool(metrics["applied"] and metrics["ratio_after"] < metrics["ratio_before"]),
        metrics=metrics,
        notes=[
            "A double-layer near-coplanar shell should lose the weaker layer before topology repair.",
        ],
    )


def _bake_mesh_proxy_smoke() -> SmokeResult:
    reference_mesh = trimesh.creation.icosphere(subdivisions=4, radius=1.0)
    vertex_colors = np.tile(np.array([[180, 160, 140, 255]], dtype=np.uint8), (len(reference_mesh.vertices), 1))
    proxy_candidate = trimesh.creation.icosphere(subdivisions=3, radius=1.0)

    with (
        mock.patch.object(dda, "_simplify_mesh", return_value=proxy_candidate.copy()),
        _override_config(
            delivery_bake_mesh_proxy_trigger_faces=1000,
            delivery_bake_mesh_proxy_face_cap=3000,
            delivery_bake_mesh_proxy_min_faces=1200,
            delivery_bake_mesh_proxy_target_ratio=0.55,
        ),
    ):
        proxy_mesh, proxy_colors, summary = dda._prepare_bake_mesh_proxy(
            reference_mesh.copy(),
            vertex_colors=vertex_colors,
        )
    fidelity = dda._compute_mesh_fidelity_metrics(reference_mesh=reference_mesh, candidate_mesh=proxy_mesh)
    metrics = {
        "applied": bool(summary.get("applied")),
        "proxy_faces": int(len(proxy_mesh.faces)),
        "proxy_vertices": int(len(proxy_mesh.vertices)),
        "proxy_colors": int(len(proxy_colors)),
        "min_extent_retention": round(float(fidelity["min_extent_retention"]), 5),
    }
    return SmokeResult(
        name="bake_mesh_proxy",
        passed=bool(
            metrics["applied"]
            and metrics["proxy_faces"] < len(reference_mesh.faces)
            and metrics["proxy_colors"] == len(proxy_mesh.vertices)
            and metrics["min_extent_retention"] >= 0.75
        ),
        metrics=metrics,
        notes=[
            "UV bake should be able to switch to a shape-preserving proxy mesh instead of stalling inside xatlas on a huge mesh.",
        ],
    )


def _hole_fill_guard_smoke() -> SmokeResult:
    open_box = trimesh.creation.box(extents=[1.0, 1.0, 0.2])
    open_box = trimesh.Trimesh(
        vertices=open_box.vertices.copy(),
        faces=open_box.faces[:-2].copy(),
        process=False,
    )
    fill_called = {"value": False}

    def fake_fill_holes(mesh):
        fill_called["value"] = True
        return True

    with mock.patch("trimesh.repair.fill_holes", side_effect=fake_fill_holes):
        _, summary = dda._repair_mesh_topology(open_box.copy())
    metrics = {
        "native_fill_holes_enabled": bool(config.delivery_native_fill_holes_enabled),
        "trimesh_fill_holes_called": bool(fill_called["value"]),
        "small_hole_patches": int(summary.get("small_hole_patches", 0)),
        "boundary_length_before": round(float(summary.get("boundary_length_before", 0.0)), 5),
        "boundary_length_after": round(float(summary.get("boundary_length_after", 0.0)), 5),
    }
    passed = not metrics["trimesh_fill_holes_called"]
    return SmokeResult(
        name="hole_fill_guard",
        passed=passed,
        metrics=metrics,
        notes=[
            "Generic trimesh fill_holes must stay disabled in HQ mode.",
        ],
    )


def main() -> int:
    results = [
        _geometry_selection_smoke(),
        _curate_temporal_coverage_smoke(),
        _curate_live_selection_hq_backfill_smoke(),
        _shape_preserving_simplify_smoke(),
        _drop_small_components_smoke(),
        _self_intersection_cleanup_smoke(),
        _multilayer_prune_smoke(),
        _bake_mesh_proxy_smoke(),
        _hole_fill_guard_smoke(),
    ]
    payload = {
        "all_passed": all(result.passed for result in results),
        "results": [asdict(result) for result in results],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
