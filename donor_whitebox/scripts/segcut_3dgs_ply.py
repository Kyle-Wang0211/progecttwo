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
    load_cameras_json,
    load_surface_points,
    load_vertex,
    percentile_threshold,
    radius_outlier_mask,
    semantic_color_validation_mask,
    semantic_whitelist_mask,
    sigmoid,
    stack_fields,
    statistical_outlier_mask,
    surface_support_mask,
    write_vertex_like,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Segmentation-first cutout export for 3DGS PLY artifacts.")
    parser.add_argument("--input-ply", required=True)
    parser.add_argument("--output-ply", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--masks-dir", required=True)
    parser.add_argument("--cameras-json", required=True)
    parser.add_argument("--surface-ply", default="")
    parser.add_argument("--min-opacity", type=float, default=0.015)
    parser.add_argument("--semantic-min-views", type=int, default=2)
    parser.add_argument("--semantic-color-threshold", type=float, default=0.30)
    parser.add_argument("--radius-outlier-points", type=int, default=8)
    parser.add_argument("--outlier-neighbors", type=int, default=18)
    parser.add_argument("--outlier-std-ratio", type=float, default=2.8)
    parser.add_argument("--min-cluster-fraction", type=float, default=0.003)
    args = parser.parse_args()

    input_ply = Path(args.input_ply)
    output_ply = Path(args.output_ply)
    summary_path = Path(args.summary_json)
    images_dir = Path(args.images_dir)
    masks_dir = Path(args.masks_dir)
    cameras_path = Path(args.cameras_json)
    surface_path = Path(args.surface_ply) if args.surface_ply else None
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    output_ply.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "input_ply": str(input_ply),
        "output_ply": str(output_ply),
        "applied": False,
        "reason": None,
        "mode": "segmentation_first",
    }

    if not input_ply.is_file():
        payload["reason"] = "missing_input_ply"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not images_dir.is_dir():
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "missing_images_dir"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not masks_dir.is_dir():
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "missing_masks_dir"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not any(path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"} for path in masks_dir.iterdir()):
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "missing_mask_files"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not cameras_path.is_file():
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "missing_cameras_json"
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

    rgb_colors = None
    if {"f_dc_0", "f_dc_1", "f_dc_2"}.issubset(names):
        c0 = 0.28209479177387814
        dc0 = np.asarray(vertex["f_dc_0"], dtype=np.float32)[valid_mask]
        dc1 = np.asarray(vertex["f_dc_1"], dtype=np.float32)[valid_mask]
        dc2 = np.asarray(vertex["f_dc_2"], dtype=np.float32)[valid_mask]
        rgb_colors = np.clip(np.stack([dc0, dc1, dc2], axis=1) * c0 + 0.5, 0.0, 1.0)

    keep_valid_mask = filtered_opacity >= float(args.min_opacity)
    keep_valid_mask &= max_scales <= giant_threshold * 1.35
    initial_keep_mask = keep_valid_mask.copy()
    if not np.any(keep_valid_mask):
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "all_points_filtered_initially"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    cameras = load_cameras_json(cameras_path)
    semantic_stats: dict[str, object] = {
        "enabled": False,
        "available_views": 0,
        "whitelist_removed": 0,
        "color_removed": 0,
        "source": "provided",
    }

    keep_valid_indices = np.flatnonzero(keep_valid_mask)
    semantic_mask = np.ones(np.count_nonzero(keep_valid_mask), dtype=bool)
    if rgb_colors is not None and cameras:
        semantic_stats["enabled"] = True
        semantic_whitelist, _, whitelist_views = semantic_whitelist_mask(
            filtered_xyz[keep_valid_mask],
            cameras,
            masks_dir,
            min_views=int(args.semantic_min_views),
        )
        semantic_stats["available_views"] = int(whitelist_views)
        semantic_stats["whitelist_removed"] = int(np.count_nonzero(keep_valid_mask) - np.count_nonzero(semantic_whitelist))
        semantic_mask = semantic_whitelist
        semantic_color_keep, _, _, color_views = semantic_color_validation_mask(
            filtered_xyz[keep_valid_mask],
            rgb_colors[keep_valid_mask],
            cameras,
            masks_dir,
            images_dir,
            semantic_whitelist,
            color_threshold=float(args.semantic_color_threshold),
        )
        semantic_stats["available_views"] = int(max(whitelist_views, color_views))
        semantic_stats["color_removed"] = int(np.count_nonzero(semantic_whitelist) - np.count_nonzero(semantic_color_keep))
        semantic_mask = semantic_color_keep

    keep_valid_mask_updated = np.zeros_like(keep_valid_mask, dtype=bool)
    keep_valid_mask_updated[keep_valid_indices[semantic_mask]] = True
    keep_valid_mask = keep_valid_mask_updated
    if not np.any(keep_valid_mask):
        shutil.copy2(input_ply, output_ply)
        payload["reason"] = "semantic_cutout_empty"
        payload["semantic_cleanup"] = semantic_stats
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    candidate_xyz = filtered_xyz[keep_valid_mask]
    candidate_scales = max_scales[keep_valid_mask]
    surface_points = load_surface_points(surface_path) if surface_path else np.empty((0, 3), dtype=np.float32)
    if surface_points.shape[0] > 0:
        surface_keep, surface_distances = surface_support_mask(
            candidate_xyz,
            surface_points,
            max_distance=max(scene_span * 0.18, median_scale * 48.0, 0.25),
        )
    else:
        surface_keep = np.ones(candidate_xyz.shape[0], dtype=bool)
        surface_distances = np.zeros(candidate_xyz.shape[0], dtype=np.float32)

    surface_kept_xyz = candidate_xyz[surface_keep]
    surface_kept_scales = candidate_scales[surface_keep]
    radius_threshold = max(
        scene_span * 0.015,
        float(np.median(surface_kept_scales)) * 8.0 if surface_kept_scales.size else median_scale * 8.0,
        0.06,
    )
    radius_keep = radius_outlier_mask(
        surface_kept_xyz,
        nb_points=int(args.radius_outlier_points),
        radius=radius_threshold,
    )

    radius_kept_xyz = surface_kept_xyz[radius_keep]
    cluster_eps = max(
        scene_span * 0.020,
        float(np.median(surface_kept_scales[radius_keep])) * 11.0 if np.count_nonzero(radius_keep) else median_scale * 11.0,
        0.08,
    )
    cluster_keep = cluster_keep_mask(
        radius_kept_xyz,
        eps=cluster_eps,
        min_points=max(int(args.radius_outlier_points), 8),
        min_cluster_fraction=float(args.min_cluster_fraction),
    )

    dominant_keep, dominant_stats = dominant_cluster_mask(
        radius_kept_xyz[cluster_keep],
        eps=max(cluster_eps * 0.95, 0.08),
        min_points=max(int(args.radius_outlier_points), 8),
        scene_span=scene_span,
    )

    outlier_keep = statistical_outlier_mask(
        radius_kept_xyz[cluster_keep][dominant_keep],
        nb_neighbors=int(args.outlier_neighbors),
        std_ratio=float(args.outlier_std_ratio),
    )

    final_valid_mask = np.zeros_like(keep_valid_mask, dtype=bool)
    keep_valid_indices = np.nonzero(keep_valid_mask)[0]
    surface_indices = keep_valid_indices[surface_keep]
    radius_indices = surface_indices[radius_keep]
    cluster_indices = radius_indices[cluster_keep]
    dominant_indices = cluster_indices[dominant_keep]
    if outlier_keep.size:
        final_valid_mask[dominant_indices[outlier_keep]] = True

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
                "removed_invalid": int(np.count_nonzero(~valid_mask)),
                "removed_low_opacity_or_giant": int(np.count_nonzero(valid_mask) - np.count_nonzero(initial_keep_mask)),
                "removed_semantic": int(np.count_nonzero(initial_keep_mask) - np.count_nonzero(keep_valid_mask)),
                "removed_surface_far": int(np.count_nonzero(keep_valid_mask) - np.count_nonzero(surface_keep)),
                "removed_radius_outlier": int(np.count_nonzero(surface_keep) - np.count_nonzero(radius_keep)),
                "removed_small_cluster": int(np.count_nonzero(radius_keep) - np.count_nonzero(cluster_keep)),
                "removed_outlier": int(np.count_nonzero(dominant_keep) - np.count_nonzero(outlier_keep)),
            },
            "semantic_cleanup": semantic_stats,
            "surface_support": {
                "surface_path": str(surface_path) if surface_path else None,
                "surface_points": int(surface_points.shape[0]),
                "mean_distance": float(np.mean(surface_distances)) if surface_distances.size else 0.0,
            },
            "dominant_cluster": dominant_stats,
            "thresholds": {
                "scene_span": scene_span,
                "median_scale": median_scale,
                "giant_scale_threshold": giant_threshold,
                "radius_outlier_radius": radius_threshold,
                "cluster_eps": cluster_eps,
            },
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
