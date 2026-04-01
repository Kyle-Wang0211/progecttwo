#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import numpy as np

from postprocess_3dgs_ply import (
    cluster_keep_mask,
    dominant_cluster_mask,
    field_names,
    load_surface_points,
    load_vertex,
    percentile_threshold,
    radius_outlier_mask,
    recover_body_fill_mask,
    recover_surface_fill_mask,
    recover_ground_support_mask,
    sigmoid,
    splat_quality_keep_mask,
    stack_fields,
    statistical_outlier_mask,
    surface_support_mask,
    write_vertex_like,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Support-surface guided prune before final artifact publication.")
    parser.add_argument("--input-ply", required=True)
    parser.add_argument("--output-ply", required=True)
    parser.add_argument("--surface-ply", default="")
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--min-opacity", type=float, default=0.015)
    args = parser.parse_args()

    input_ply = Path(args.input_ply)
    output_ply = Path(args.output_ply)
    summary_path = Path(args.summary_json)
    surface_path = Path(args.surface_ply) if args.surface_ply else None
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    output_ply.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "input_ply": str(input_ply),
        "output_ply": str(output_ply),
        "applied": False,
        "reason": None,
    }

    if not input_ply.is_file():
        payload["reason"] = "missing_input_ply"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    ply, vertex = load_vertex(input_ply)
    names = field_names(vertex)
    required = {"x", "y", "z", "opacity"}
    scale_names = sorted(name for name in names if name.startswith("scale_"))
    if not required.issubset(names) or len(scale_names) < 3:
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "missing_expected_fields"
        payload["fields"] = list(names)
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    xyz = stack_fields(vertex, ["x", "y", "z"])
    raw_opacity = np.asarray(vertex["opacity"], dtype=np.float32)
    raw_scales = stack_fields(vertex, scale_names[:3])

    valid_mask = np.isfinite(xyz).all(axis=1)
    valid_mask &= np.isfinite(raw_opacity)
    valid_mask &= np.isfinite(raw_scales).all(axis=1)

    filtered_xyz = xyz[valid_mask]
    filtered_opacity = sigmoid(raw_opacity[valid_mask])
    filtered_scales = np.exp(np.clip(raw_scales[valid_mask], -30.0, 30.0))
    max_scales = filtered_scales.max(axis=1)
    median_scale = float(np.median(max_scales)) if max_scales.size else 0.0
    scene_span = float(np.linalg.norm(filtered_xyz.max(axis=0) - filtered_xyz.min(axis=0))) if filtered_xyz.size else 0.0
    giant_threshold = percentile_threshold(max_scales, scene_span=scene_span)
    surface_points = load_surface_points(surface_path) if surface_path else np.empty((0, 3), dtype=np.float32)

    if surface_points.shape[0] == 0:
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "missing_surface_support"
        payload["surface_points"] = 0
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    keep_valid_mask = filtered_opacity >= float(args.min_opacity)
    keep_valid_mask &= max_scales <= giant_threshold * 1.15

    kept_xyz = filtered_xyz[keep_valid_mask]
    kept_scales = filtered_scales[keep_valid_mask]
    kept_opacity = filtered_opacity[keep_valid_mask]

    surface_distance_threshold = max(scene_span * 0.035, median_scale * 14.0, 0.08)
    surface_keep, surface_distances = surface_support_mask(
        kept_xyz,
        surface_points,
        max_distance=surface_distance_threshold,
    )

    surface_kept_xyz = kept_xyz[surface_keep]
    surface_kept_scales = kept_scales[surface_keep]
    surface_kept_opacity = kept_opacity[surface_keep]
    radius_threshold = max(
        scene_span * 0.015,
        float(np.median(surface_kept_scales.max(axis=1))) * 6.0 if surface_kept_scales.size else median_scale * 6.0,
        0.05,
    )
    radius_keep = radius_outlier_mask(
        surface_kept_xyz,
        nb_points=10,
        radius=radius_threshold,
    )

    radius_kept_xyz = surface_kept_xyz[radius_keep]
    cluster_eps = max(
        scene_span * 0.020,
        float(np.median(surface_kept_scales[radius_keep].max(axis=1))) * 10.0 if np.count_nonzero(radius_keep) else median_scale * 10.0,
        0.08,
    )
    cluster_keep = cluster_keep_mask(
        radius_kept_xyz,
        eps=cluster_eps,
        min_points=10,
        min_cluster_fraction=0.006,
    )

    dominant_keep, dominant_stats = dominant_cluster_mask(
        radius_kept_xyz[cluster_keep],
        eps=max(cluster_eps * 0.90, 0.06),
        min_points=10,
        scene_span=scene_span,
    )

    dominant_xyz = radius_kept_xyz[cluster_keep][dominant_keep]
    dominant_scales = surface_kept_scales[radius_keep][cluster_keep][dominant_keep]
    dominant_opacity = surface_kept_opacity[radius_keep][cluster_keep][dominant_keep]
    dominant_surface_supported = np.ones(dominant_xyz.shape[0], dtype=bool)
    dominant_surface_distances = surface_distances[surface_keep][radius_keep][cluster_keep][dominant_keep]
    splat_keep, splat_stats = splat_quality_keep_mask(
        dominant_xyz,
        dominant_scales,
        dominant_opacity,
        dominant_surface_supported,
        dominant_surface_distances,
        scene_span=scene_span,
        median_scale=median_scale,
    )

    outlier_keep = statistical_outlier_mask(
        dominant_xyz[splat_keep],
        nb_neighbors=20,
        std_ratio=3.0,
    )

    final_valid_mask = np.zeros_like(keep_valid_mask, dtype=bool)
    keep_valid_indices = np.nonzero(keep_valid_mask)[0]
    surface_indices = keep_valid_indices[surface_keep]
    radius_indices = surface_indices[radius_keep]
    cluster_indices = radius_indices[cluster_keep]
    dominant_indices = cluster_indices[dominant_keep]
    splat_indices = dominant_indices[splat_keep]
    if outlier_keep.size:
        final_valid_mask[splat_indices[outlier_keep]] = True

    recovered_surface = np.zeros_like(final_valid_mask, dtype=bool)
    if dominant_indices.size > 0:
        recovered_surface_local, fill_stats = recover_surface_fill_mask(
            filtered_xyz[keep_valid_mask],
            np.isin(np.arange(np.count_nonzero(keep_valid_mask)), dominant_indices),
            surface_keep,
            kept_scales.max(axis=1),
            kept_opacity,
            scene_span=scene_span,
            median_scale=median_scale,
        )
        recovered_surface[keep_valid_indices[recovered_surface_local]] = True
    else:
        fill_stats = {
            "recovered": 0,
            "band_low": 0.0,
            "band_high": 0.0,
            "radius": 0.0,
            "opacity_floor": 0.0,
            "scale_limit": 0.0,
        }
    final_valid_mask |= recovered_surface

    recovered_body = np.zeros_like(final_valid_mask, dtype=bool)
    if dominant_indices.size > 0:
        recovered_body_local, body_stats = recover_body_fill_mask(
            filtered_xyz[keep_valid_mask],
            np.isin(np.arange(np.count_nonzero(keep_valid_mask)), dominant_indices),
            kept_scales.max(axis=1),
            kept_opacity,
            scene_span=scene_span,
            median_scale=median_scale,
        )
        recovered_body[keep_valid_indices[recovered_body_local]] = True
    else:
        body_stats = {
            "recovered": 0,
            "band_low": 0.0,
            "band_high": 0.0,
            "radius": 0.0,
            "opacity_floor": 0.0,
            "scale_limit": 0.0,
            "support75": 0.0,
        }
    final_valid_mask |= recovered_body

    recovered_ground = np.zeros_like(final_valid_mask, dtype=bool)
    if dominant_indices.size > 0:
        recovered_ground_local, ground_stats = recover_ground_support_mask(
            filtered_xyz[keep_valid_mask],
            np.isin(np.arange(np.count_nonzero(keep_valid_mask)), dominant_indices),
            surface_keep,
            kept_scales.max(axis=1),
            kept_opacity,
            scene_span=scene_span,
            median_scale=median_scale,
        )
        recovered_ground[keep_valid_indices[recovered_ground_local]] = True
    else:
        ground_stats = {
            "recovered": 0,
            "y10": 0.0,
            "y20": 0.0,
            "y35": 0.0,
            "slab_low": 0.0,
            "slab_high": 0.0,
            "radius": 0.0,
            "columns": 0,
        }
    final_valid_mask |= recovered_ground

    final_mask = np.zeros_like(valid_mask, dtype=bool)
    final_mask[np.nonzero(valid_mask)[0][final_valid_mask]] = True

    original_count = int(vertex.shape[0])
    kept_count = int(np.count_nonzero(final_mask))
    if kept_count > 0 and kept_count < original_count:
        write_vertex_like(vertex, final_mask, output_ply)
        payload["applied"] = True
    else:
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "no_safe_reduction" if kept_count >= original_count else "all_points_removed_guarded"

    payload.update(
        {
            "counts": {
                "original": original_count,
                "kept": kept_count,
                "removed_surface_far": int(np.count_nonzero(keep_valid_mask) - np.count_nonzero(surface_keep)),
                "removed_radius_outlier": int(np.count_nonzero(surface_keep) - np.count_nonzero(radius_keep)),
                "removed_small_cluster": int(np.count_nonzero(radius_keep) - np.count_nonzero(cluster_keep)),
                "removed_outlier": int(np.count_nonzero(cluster_keep) - np.count_nonzero(dominant_keep) + np.count_nonzero(dominant_keep) - np.count_nonzero(outlier_keep)),
            },
            "surface_support": {
                "surface_path": str(surface_path) if surface_path else None,
                "surface_points": int(surface_points.shape[0]),
                "surface_distance_threshold": surface_distance_threshold,
            },
            "dominant_cluster": dominant_stats,
            "surface_fill_recovery": fill_stats,
            "body_fill_recovery": body_stats,
            "splat_quality": splat_stats,
            "ground_recovery": ground_stats,
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
