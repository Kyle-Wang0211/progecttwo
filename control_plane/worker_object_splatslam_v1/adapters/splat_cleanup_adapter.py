from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..config import config
from ..context import JobContext
from ..utils.gaussian_ply import apply_transform_to_vertices, filter_vertices, load_ply_vertices, save_ply


def cleanup_splat(ctx: JobContext) -> dict[str, Any]:
    assert ctx.splat_dir is not None
    assert ctx.splatslam_dir is not None
    assert ctx.support_dir is not None

    splatslam_summary = _read_json(ctx.splatslam_dir / "splatslam.json")
    support_summary = _read_json(ctx.support_dir / "support_plane.json")

    source_asset = _resolve_splat_asset(splatslam_summary)
    if source_asset is None:
        raise RuntimeError("splatslam_default_asset_missing")

    source_ply, xyz = load_ply_vertices(source_asset)
    rotation = np.asarray(support_summary.get("yaw_rotation") or np.eye(3), dtype=np.float64)
    translation = np.asarray(support_summary.get("translation") or [0.0, 0.0, 0.0], dtype=np.float64)
    apply_transform_to_vertices(source_ply, rotation, translation)
    transformed = (rotation @ xyz.T).T + translation

    object_bounds = np.asarray(
        [
            support_summary["object_bounds"]["min"],
            support_summary["object_bounds"]["max"],
        ],
        dtype=np.float64,
    )
    support_patch_bounds = np.asarray(
        [
            support_summary["support_patch_bounds"]["min"],
            support_summary["support_patch_bounds"]["max"],
        ],
        dtype=np.float64,
    )
    support_band_height = float(support_summary.get("support_band_height") or support_patch_bounds[1, 1])
    object_size = object_bounds[1] - object_bounds[0]
    object_padding = max(float(max(object_size[0], object_size[2])) * config.cleanup_object_padding_ratio, 0.04)
    support_padding = max(float(max(object_size[0], object_size[2])) * config.cleanup_support_padding_ratio, 0.025)
    max_object_height = float(object_bounds[1, 1] + max(object_size[1] * 0.35, 0.04))

    object_region = (
        (transformed[:, 0] >= object_bounds[0, 0] - object_padding)
        & (transformed[:, 0] <= object_bounds[1, 0] + object_padding)
        & (transformed[:, 2] >= object_bounds[0, 2] - object_padding)
        & (transformed[:, 2] <= object_bounds[1, 2] + object_padding)
        & (transformed[:, 1] <= max_object_height)
    )
    support_region = (
        (transformed[:, 0] >= support_patch_bounds[0, 0] - support_padding)
        & (transformed[:, 0] <= support_patch_bounds[1, 0] + support_padding)
        & (transformed[:, 2] >= support_patch_bounds[0, 2] - support_padding)
        & (transformed[:, 2] <= support_patch_bounds[1, 2] + support_padding)
        & (transformed[:, 1] >= -config.cleanup_drop_below_plane)
        & (transformed[:, 1] <= support_band_height * 1.35)
    )
    valid_height = (
        (transformed[:, 1] >= -config.cleanup_drop_below_plane)
        & (transformed[:, 1] <= max_object_height)
    )
    keep_mask = valid_height & (object_region | support_region)
    if int(np.count_nonzero(keep_mask)) < 32:
        keep_mask = valid_height
    if int(np.count_nonzero(keep_mask)) < 16:
        raise RuntimeError("splat_cleanup_removed_every_point")

    destination = ctx.splat_dir / f"default_object_cleaned{source_asset.suffix.lower()}"
    cleaned_ply = filter_vertices(source_ply, keep_mask)
    save_ply(destination, cleaned_ply)
    kept_points = transformed[keep_mask]
    raw_point_count = int(len(transformed))
    kept_point_count = int(np.count_nonzero(keep_mask))
    removed_point_count = max(0, raw_point_count - kept_point_count)
    kept_ratio = float(kept_point_count / raw_point_count) if raw_point_count > 0 else 0.0
    removed_ratio = float(removed_point_count / raw_point_count) if raw_point_count > 0 else 0.0
    raw_bounds = {
        "min": [float(v) for v in transformed.min(axis=0)],
        "max": [float(v) for v in transformed.max(axis=0)],
    }

    payload = {
        "backend": "splat_cleanup_adapter",
        "source_asset": str(source_asset),
        "raw_asset_kind": "splat",
        "raw_asset_filename": source_asset.name,
        "cleaned_asset_kind": "splat",
        "cleaned_asset_filename": destination.name,
        "default_asset_kind": "splat",
        "default_asset_filename": destination.name,
        "camera_preset": support_summary.get("camera_preset"),
        "support_patch_bounds": support_summary.get("support_patch_bounds"),
        "object_bounds": support_summary.get("object_bounds"),
        "support_band_height": support_band_height,
        "raw_point_count": raw_point_count,
        "kept_point_count": kept_point_count,
        "removed_point_count": removed_point_count,
        "kept_ratio": kept_ratio,
        "removed_ratio": removed_ratio,
        "raw_bounds": raw_bounds,
        "cleaned_bounds": {
            "min": [float(v) for v in kept_points.min(axis=0)],
            "max": [float(v) for v in kept_points.max(axis=0)],
        },
    }
    output_path = ctx.splat_dir / "cleanup.json"
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _resolve_splat_asset(summary: dict[str, Any]) -> Path | None:
    for key in ("default_asset", "gaussian_ply", "gaussian_splat", "exported_ply", "exported_splat"):
        candidate = summary.get(key)
        if isinstance(candidate, str) and candidate.strip():
            path = Path(candidate).expanduser()
            if path.exists():
                return path
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
