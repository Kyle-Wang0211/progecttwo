from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ..config import config
from ..context import JobContext
from ..utils.gaussian_ply import load_ply_vertices


def estimate_canonical_pose(ctx: JobContext) -> dict[str, Any]:
    assert ctx.support_dir is not None
    assert ctx.splatslam_dir is not None

    summary_path = ctx.splatslam_dir / "splatslam.json"
    if not summary_path.exists():
        raise RuntimeError("splatslam_summary_missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    source_asset = _resolve_splat_asset(summary)
    if source_asset is None or not source_asset.exists():
        raise RuntimeError("splatslam_default_asset_missing")

    _, xyz = load_ply_vertices(source_asset)
    if len(xyz) < 16:
        raise RuntimeError("splatslam_point_cloud_too_small")

    support_quantile = float(np.quantile(xyz[:, 1], 0.03))
    centered = xyz.copy()
    centered[:, 1] -= support_quantile

    support_band_height = max(float(np.ptp(centered[:, 1])) * config.support_band_height_ratio, 0.015)
    object_points = centered[centered[:, 1] > support_band_height]
    if len(object_points) < 32:
        object_points = centered

    yaw_rotation = _yaw_align_rotation(object_points[:, [0, 2]])
    canonical = (yaw_rotation @ centered.T).T
    object_points = canonical[canonical[:, 1] > support_band_height]
    if len(object_points) < 32:
        object_points = canonical

    object_bounds = np.stack([object_points.min(axis=0), object_points.max(axis=0)])
    center_x = float((object_bounds[0, 0] + object_bounds[1, 0]) * 0.5)
    center_z = float((object_bounds[0, 2] + object_bounds[1, 2]) * 0.5)
    translation = np.asarray([-center_x, 0.0, -center_z], dtype=np.float64)

    canonical += translation
    object_points += translation
    object_bounds = np.stack([object_points.min(axis=0), object_points.max(axis=0)])
    canonical_bounds = np.stack([canonical.min(axis=0), canonical.max(axis=0)])

    object_size = object_bounds[1] - object_bounds[0]
    radius = max(float(np.linalg.norm(object_size)) * 0.55, 0.45)
    footprint_padding = max(float(max(object_size[0], object_size[2])) * config.support_patch_padding_ratio, 0.03)
    support_patch_bounds = {
        "min": [float(object_bounds[0, 0] - footprint_padding), 0.0, float(object_bounds[0, 2] - footprint_padding)],
        "max": [float(object_bounds[1, 0] + footprint_padding), float(support_band_height), float(object_bounds[1, 2] + footprint_padding)],
    }
    center = [0.0, float((object_bounds[0, 1] + object_bounds[1, 1]) * 0.5), 0.0]
    up = [0.0, 1.0, 0.0]
    orbit_distance = max(radius * config.splatslam_default_camera_distance_scale, 0.8)

    payload = {
        "backend": "canonical_pose_adapter",
        "center": center,
        "radius": radius,
        "up": up,
        "canonical_bounds": _bounds_dict(canonical_bounds),
        "object_bounds": _bounds_dict(object_bounds),
        "support_band_height": float(support_band_height),
        "up_rotation": np.eye(3, dtype=np.float64).tolist(),
        "yaw_rotation": yaw_rotation.tolist(),
        "translation": translation.tolist(),
        "canonical_rotation_quat_xyzw": [0.0, 0.0, 0.0, 1.0],
        "support_patch_bounds": support_patch_bounds,
        "camera_preset": {
            "orbit_distance": orbit_distance,
            "pitch_degrees": config.splatslam_default_camera_pitch_deg,
            "yaw_degrees": config.splatslam_default_camera_yaw_deg,
            "look_at": center,
            "up": up,
            "near_clip": max(radius * 0.02, 0.01),
            "far_clip": max(radius * 6.0, 4.0),
        },
    }
    output_path = ctx.support_dir / "support_plane.json"
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


def _yaw_align_rotation(projected: np.ndarray) -> np.ndarray:
    projected = projected - projected.mean(axis=0)
    cov = projected.T @ projected
    if np.allclose(cov, 0.0):
        return np.eye(3, dtype=np.float64)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    principal = eigenvectors[:, int(np.argmax(eigenvalues))]
    yaw = math.atan2(float(principal[1]), float(principal[0]))
    cos_yaw = math.cos(-yaw)
    sin_yaw = math.sin(-yaw)
    return np.array(
        [
            [cos_yaw, 0.0, sin_yaw],
            [0.0, 1.0, 0.0],
            [-sin_yaw, 0.0, cos_yaw],
        ],
        dtype=np.float64,
    )


def _bounds_dict(bounds: np.ndarray) -> dict[str, list[float]]:
    return {
        "min": [float(v) for v in bounds[0]],
        "max": [float(v) for v in bounds[1]],
    }
