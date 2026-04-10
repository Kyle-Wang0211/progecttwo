from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ..config import config
from ..context import JobContext


def _load_openmvs_summary(ctx: JobContext) -> dict[str, Any]:
    assert ctx.surface_dir is not None
    summary_path = ctx.surface_dir / "openmvs.json"
    if not summary_path.exists():
        raise RuntimeError("support_plane_missing_openmvs_summary")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _load_surface_mesh(ctx: JobContext):
    try:
        import trimesh
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError("support_plane_requires_trimesh") from exc

    summary = _load_openmvs_summary(ctx)
    source = Path(str(summary.get("textured_obj") or summary.get("mesh_ply") or ""))
    if not source.exists():
        raise RuntimeError(f"support_plane_missing_mesh:{source}")

    loaded = trimesh.load(source, process=False, maintain_order=True)
    if isinstance(loaded, trimesh.Scene):
        geometries = [geom.copy() for geom in loaded.geometry.values() if len(getattr(geom, "faces", [])) > 0]
        if not geometries:
            raise RuntimeError("support_plane_scene_has_no_mesh_geometry")
        mesh = geometries[0] if len(geometries) == 1 else trimesh.util.concatenate(geometries)
    else:
        mesh = loaded.copy()

    mesh.remove_unreferenced_vertices()
    if len(mesh.vertices) < 16 or len(mesh.faces) == 0:
        raise RuntimeError("support_plane_mesh_too_small")
    return trimesh, mesh


def _rotation_matrix_from_axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = axis / np.linalg.norm(axis)
    x, y, z = axis
    c = math.cos(angle)
    s = math.sin(angle)
    C = 1.0 - c
    return np.array(
        [
            [x * x * C + c, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, y * y * C + c, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, z * z * C + c],
        ],
        dtype=np.float64,
    )


def _rotation_from_to(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    source = source / np.linalg.norm(source)
    target = target / np.linalg.norm(target)
    dot = float(np.clip(np.dot(source, target), -1.0, 1.0))
    if dot > 0.999999:
        return np.eye(3, dtype=np.float64)
    if dot < -0.999999:
        axis = np.cross(source, np.array([1.0, 0.0, 0.0], dtype=np.float64))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(source, np.array([0.0, 0.0, 1.0], dtype=np.float64))
        return _rotation_matrix_from_axis_angle(axis, math.pi)
    axis = np.cross(source, target)
    angle = math.acos(dot)
    return _rotation_matrix_from_axis_angle(axis, angle)


def _plane_from_points(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    center = points.mean(axis=0)
    centered = points - center
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    normal = normal / np.linalg.norm(normal)
    return center, normal


def _estimate_plane_ransac(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    diag = float(np.linalg.norm(vertices.max(axis=0) - vertices.min(axis=0)))
    distance_threshold = max(config.support_plane_min_distance, diag * config.support_plane_distance_ratio)

    best_inliers: np.ndarray | None = None
    best_center: np.ndarray | None = None
    best_normal: np.ndarray | None = None

    sample_count = min(config.support_plane_ransac_iterations, max(64, len(vertices) * 2))
    for _ in range(sample_count):
        indices = rng.choice(len(vertices), size=3, replace=False)
        a, b, c = vertices[indices]
        normal = np.cross(b - a, c - a)
        norm = float(np.linalg.norm(normal))
        if norm < 1e-8:
            continue
        normal = normal / norm
        distances = np.abs((vertices - a) @ normal)
        inliers = distances <= distance_threshold
        if best_inliers is None or int(inliers.sum()) > int(best_inliers.sum()):
            best_inliers = inliers
            best_center = a
            best_normal = normal

    if best_inliers is None or best_inliers.sum() < 16 or best_center is None or best_normal is None:
        center = vertices.mean(axis=0)
        normal = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        return center, normal, np.ones(len(vertices), dtype=bool)

    refined_center, refined_normal = _plane_from_points(vertices[best_inliers])
    return refined_center, refined_normal, best_inliers


def _yaw_align_rotation(vertices: np.ndarray) -> np.ndarray:
    projected = vertices[:, [0, 2]]
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


def _matrix4(rotation: np.ndarray, translation: np.ndarray) -> np.ndarray:
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = rotation
    transform[:3, 3] = translation
    return transform


def _bounds_dict(bounds: np.ndarray) -> dict[str, list[float]]:
    return {
        "min": [float(v) for v in bounds[0]],
        "max": [float(v) for v in bounds[1]],
    }


def estimate_support_plane(ctx: JobContext) -> None:
    assert ctx.support_dir is not None
    assert ctx.surface_dir is not None

    trimesh, mesh = _load_surface_mesh(ctx)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    plane_point, plane_normal, inliers = _estimate_plane_ransac(vertices)

    centroid = vertices.mean(axis=0)
    if float(np.dot(centroid - plane_point, plane_normal)) < 0:
        plane_normal = -plane_normal

    up_rotation = _rotation_from_to(plane_normal, np.array([0.0, 1.0, 0.0], dtype=np.float64))
    rotated_vertices = (up_rotation @ vertices.T).T
    rotated_plane_point = up_rotation @ plane_point

    mesh_height = float(rotated_vertices[:, 1].max() - rotated_vertices[:, 1].min())
    support_band_height = max(mesh_height * config.support_band_height_ratio, 0.012)
    object_vertices = rotated_vertices[rotated_vertices[:, 1] > (rotated_plane_point[1] + support_band_height)]
    if len(object_vertices) < 32:
        object_vertices = rotated_vertices

    yaw_rotation = _yaw_align_rotation(object_vertices)
    canonical_vertices = (yaw_rotation @ rotated_vertices.T).T
    canonical_plane_point = yaw_rotation @ rotated_plane_point

    canonical_mesh = mesh.copy()
    canonical_mesh.apply_transform(_matrix4(up_rotation, np.zeros(3, dtype=np.float64)))
    canonical_mesh.apply_transform(_matrix4(yaw_rotation, np.zeros(3, dtype=np.float64)))

    canonical_vertices = np.asarray(canonical_mesh.vertices, dtype=np.float64)
    support_band_height = max(
        float(np.ptp(canonical_vertices[:, 1])) * config.support_band_height_ratio,
        0.012,
    )
    object_vertices = canonical_vertices[canonical_vertices[:, 1] > (canonical_plane_point[1] + support_band_height)]
    if len(object_vertices) < 32:
        object_vertices = canonical_vertices

    object_bounds = np.stack([object_vertices.min(axis=0), object_vertices.max(axis=0)])
    center_x = float((object_bounds[0, 0] + object_bounds[1, 0]) * 0.5)
    center_z = float((object_bounds[0, 2] + object_bounds[1, 2]) * 0.5)
    translation = np.array([-center_x, -float(canonical_plane_point[1]), -center_z], dtype=np.float64)
    canonical_mesh.apply_transform(_matrix4(np.eye(3, dtype=np.float64), translation))

    canonical_bounds = np.asarray(canonical_mesh.bounds, dtype=np.float64)
    object_vertices = np.asarray(canonical_mesh.vertices, dtype=np.float64)
    object_vertices_above_plane = object_vertices[object_vertices[:, 1] > support_band_height]
    if len(object_vertices_above_plane) < 32:
        object_vertices_above_plane = object_vertices
    object_bounds = np.stack([object_vertices_above_plane.min(axis=0), object_vertices_above_plane.max(axis=0)])

    object_size = object_bounds[1] - object_bounds[0]
    footprint_padding = max(float(max(object_size[0], object_size[2])) * config.support_patch_padding_ratio, 0.025)
    support_patch_bounds = np.array(
        [
            [object_bounds[0, 0] - footprint_padding, 0.0, object_bounds[0, 2] - footprint_padding],
            [object_bounds[1, 0] + footprint_padding, support_band_height, object_bounds[1, 2] + footprint_padding],
        ],
        dtype=np.float64,
    )

    radius = max(float(max(object_size[0], object_size[1], object_size[2])) * 2.1, 0.45)
    camera_preset = {
        "target": [0.0, float((object_bounds[0, 1] + object_bounds[1, 1]) * 0.5), 0.0],
        "distance": radius,
        "min_distance": radius * 0.55,
        "max_distance": radius * 2.6,
        "yaw_degrees": 22.0,
        "pitch_degrees": -12.0,
    }

    support_summary = {
        "backend": "open3d_trimesh",
        "plane_point_world": [float(v) for v in plane_point],
        "plane_normal_world": [float(v) for v in plane_normal],
        "inlier_ratio": float(inliers.sum()) / float(len(inliers)),
        "support_band_height": float(support_band_height),
        "canonical_bounds": _bounds_dict(canonical_bounds),
        "object_bounds": _bounds_dict(object_bounds),
        "support_patch_bounds": _bounds_dict(support_patch_bounds),
        "up_rotation": up_rotation.tolist(),
        "yaw_rotation": yaw_rotation.tolist(),
        "translation": translation.tolist(),
        "camera_preset": camera_preset,
    }
    (ctx.support_dir / "support_plane.json").write_text(
        json.dumps(support_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    canonical_glb = ctx.surface_dir / "scene_canonical.glb"
    canonical_glb.write_bytes(canonical_mesh.export(file_type="glb"))


def _load_support_summary(ctx: JobContext) -> dict[str, Any]:
    assert ctx.support_dir is not None
    summary_path = ctx.support_dir / "support_plane.json"
    if not summary_path.exists():
        raise RuntimeError("cleanup_missing_support_plane")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def cleanup_surface(ctx: JobContext) -> None:
    assert ctx.surface_dir is not None

    trimesh, mesh = _load_surface_mesh(ctx)
    support_summary = _load_support_summary(ctx)

    up_rotation = np.asarray(support_summary["up_rotation"], dtype=np.float64)
    yaw_rotation = np.asarray(support_summary["yaw_rotation"], dtype=np.float64)
    translation = np.asarray(support_summary["translation"], dtype=np.float64)
    support_band_height = float(support_summary["support_band_height"])
    object_bounds = np.asarray(
        [support_summary["object_bounds"]["min"], support_summary["object_bounds"]["max"]],
        dtype=np.float64,
    )
    support_patch_bounds = np.asarray(
        [support_summary["support_patch_bounds"]["min"], support_summary["support_patch_bounds"]["max"]],
        dtype=np.float64,
    )

    mesh.apply_transform(_matrix4(up_rotation, np.zeros(3, dtype=np.float64)))
    mesh.apply_transform(_matrix4(yaw_rotation, np.zeros(3, dtype=np.float64)))
    mesh.apply_transform(_matrix4(np.eye(3, dtype=np.float64), translation))

    face_centers = np.asarray(mesh.triangles_center, dtype=np.float64)
    object_size = object_bounds[1] - object_bounds[0]
    object_padding = max(float(max(object_size[0], object_size[2])) * config.cleanup_object_padding_ratio, 0.03)
    support_padding = max(float(max(object_size[0], object_size[2])) * config.cleanup_support_padding_ratio, 0.02)
    support_keep_height = support_band_height * 1.35
    max_object_height = float(object_bounds[1, 1] + max(object_size[1] * 0.30, 0.03))

    object_region = (
        (face_centers[:, 0] >= object_bounds[0, 0] - object_padding)
        & (face_centers[:, 0] <= object_bounds[1, 0] + object_padding)
        & (face_centers[:, 2] >= object_bounds[0, 2] - object_padding)
        & (face_centers[:, 2] <= object_bounds[1, 2] + object_padding)
    )
    support_region = (
        (face_centers[:, 0] >= support_patch_bounds[0, 0] - support_padding)
        & (face_centers[:, 0] <= support_patch_bounds[1, 0] + support_padding)
        & (face_centers[:, 2] >= support_patch_bounds[0, 2] - support_padding)
        & (face_centers[:, 2] <= support_patch_bounds[1, 2] + support_padding)
        & (face_centers[:, 1] <= support_keep_height)
    )
    valid_height = (
        (face_centers[:, 1] >= -config.cleanup_drop_below_plane)
        & (face_centers[:, 1] <= max_object_height)
    )
    keep_faces = np.nonzero(valid_height & (object_region | support_region))[0]
    if keep_faces.size == 0:
        raise RuntimeError("cleanup_removed_every_face")

    cleaned = mesh.submesh([keep_faces], append=True, repair=False)
    cleaned.remove_unreferenced_vertices()

    try:
        components = cleaned.split(only_watertight=False)
    except ImportError:
        components = []

    if components:
        filtered_components = []
        total_area = sum(float(component.area) for component in components)
        minimum_component_area = max(total_area * 0.01, 1e-5)
        for component in components:
            bounds = np.asarray(component.bounds, dtype=np.float64)
            area = float(component.area)
            if area < minimum_component_area:
                continue
            overlaps_object = not (
                bounds[1, 0] < object_bounds[0, 0] - object_padding
                or bounds[0, 0] > object_bounds[1, 0] + object_padding
                or bounds[1, 2] < object_bounds[0, 2] - object_padding
                or bounds[0, 2] > object_bounds[1, 2] + object_padding
            )
            overlaps_support = not (
                bounds[1, 0] < support_patch_bounds[0, 0] - support_padding
                or bounds[0, 0] > support_patch_bounds[1, 0] + support_padding
                or bounds[1, 2] < support_patch_bounds[0, 2] - support_padding
                or bounds[0, 2] > support_patch_bounds[1, 2] + support_padding
            )
            if overlaps_object or overlaps_support:
                filtered_components.append(component)
        if filtered_components:
            cleaned = (
                filtered_components[0]
                if len(filtered_components) == 1
                else trimesh.util.concatenate(filtered_components)
            )

    cleaned.remove_unreferenced_vertices()
    cleaned_glb = ctx.surface_dir / "scene_cleaned.glb"
    cleaned_glb.write_bytes(cleaned.export(file_type="glb"))

    cleanup_summary = {
        "backend": "open3d_trimesh",
        "kept_face_count": int(len(cleaned.faces)),
        "kept_vertex_count": int(len(cleaned.vertices)),
        "support_patch_bounds": support_summary["support_patch_bounds"],
        "object_bounds": support_summary["object_bounds"],
        "camera_preset": support_summary["camera_preset"],
        "cleaned_bounds": _bounds_dict(np.asarray(cleaned.bounds, dtype=np.float64)),
    }
    (ctx.surface_dir / "cleanup.json").write_text(
        json.dumps(cleanup_summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
