from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..config import config
from ..context import JobContext


@dataclass(frozen=True)
class _Camera:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float


@dataclass(frozen=True)
class _ImagePose:
    camera_id: int
    name: str
    qvec: tuple[float, float, float, float]
    tvec: tuple[float, float, float]


def optimize_default_mesh_delivery(ctx: JobContext) -> Path:
    assert ctx.matcha_dir is not None
    assert ctx.delivery_dir is not None

    raw_mesh = _resolve_matcha_mesh_asset(ctx.matcha_dir)
    mesh = _load_mesh(raw_mesh)
    initial_faces = int(len(mesh.faces))
    initial_vertices = int(len(mesh.vertices))

    mesh.remove_unreferenced_vertices()
    try:
        mesh.merge_vertices()
    except Exception:
        pass

    mesh = _drop_small_components(mesh)
    mesh = _drop_degenerate_faces(mesh)

    try:
        mesh.fix_normals(multibody=True)
    except Exception:
        mesh.fix_normals()

    simplification_applied = False
    simplify_reason = "preserve_geometry"
    target_faces = max(1024, config.delivery_target_face_count)
    if _should_simplify_mesh(mesh, target_faces=target_faces):
        mesh = _simplify_mesh(mesh, target_faces=target_faces)
        simplification_applied = len(mesh.faces) < initial_faces
        simplify_reason = "hard_face_cap" if initial_faces > config.delivery_hard_max_face_count else "target_budget"

    destination = ctx.delivery_dir / "optimized_mesh.ply"
    destination.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(destination)

    summary = {
        "source_mesh": str(raw_mesh),
        "optimized_mesh": str(destination),
        "initial_faces": initial_faces,
        "initial_vertices": initial_vertices,
        "optimized_faces": int(len(mesh.faces)),
        "optimized_vertices": int(len(mesh.vertices)),
        "preserve_geometry": bool(config.delivery_preserve_geometry_default),
        "simplification_applied": simplification_applied,
        "simplify_reason": simplify_reason,
        "target_face_count": int(config.delivery_target_face_count),
        "hard_max_face_count": int(config.delivery_hard_max_face_count),
        "component_min_faces": int(config.delivery_component_min_faces),
        "component_ratio_floor": float(config.delivery_component_ratio_floor),
    }
    (ctx.delivery_dir / config.delivery_mesh_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return destination


def bake_default_texture_delivery(ctx: JobContext) -> Path:
    assert ctx.delivery_dir is not None
    assert ctx.sparse2dgs_scene_dir is not None

    optimized_mesh = _resolve_optimized_mesh_asset(ctx.delivery_dir)
    mesh = _load_mesh(optimized_mesh)

    cameras = _read_cameras(ctx.sparse2dgs_scene_dir / "sparse" / "0" / "cameras.txt")
    poses = _read_images(ctx.sparse2dgs_scene_dir / "sparse" / "0" / "images.txt")
    if not cameras or not poses:
        raise RuntimeError("delivery_texture_contract_missing")

    projected_views = poses[: max(1, min(len(poses), config.delivery_texture_max_views))]
    vertex_colors, observed_vertex_count = _project_vertex_colors(
        mesh=mesh,
        images_dir=ctx.sparse2dgs_scene_dir / "images",
        cameras=cameras,
        poses=projected_views,
    )

    textured_mesh = mesh.copy()
    textured_mesh.visual.vertex_colors = vertex_colors
    glb_path = ctx.delivery_dir / "default_mesh.glb"
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    textured_mesh.export(glb_path)
    glb_size_bytes = glb_path.stat().st_size if glb_path.exists() else 0

    summary = {
        "optimized_mesh": str(optimized_mesh),
        "default_asset": str(glb_path),
        "projection_mode": "multi_view_vertex_color_projection",
        "projected_view_count": len(projected_views),
        "observed_vertex_count": int(observed_vertex_count),
        "face_count": int(len(textured_mesh.faces)),
        "vertex_count": int(len(textured_mesh.vertices)),
        "glb_size_bytes": int(glb_size_bytes),
        "glb_size_mb": round(glb_size_bytes / (1024 * 1024), 3) if glb_size_bytes > 0 else 0.0,
    }
    (ctx.delivery_dir / config.delivery_texture_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return glb_path


def _resolve_matcha_mesh_asset(matcha_dir: Path) -> Path:
    summary = _read_json(matcha_dir / config.matcha_summary_filename) or {}
    candidate = summary.get("mesh_asset")
    if isinstance(candidate, str) and candidate.strip():
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    matches = sorted(matcha_dir.glob("tetra_mesh_binary_search_*.ply"))
    if matches:
        return matches[-1]
    matches = sorted(matcha_dir.glob("*.ply"))
    if matches:
        return matches[-1]
    raise RuntimeError(f"matcha_mesh_asset_missing:{matcha_dir}")


def _resolve_optimized_mesh_asset(delivery_dir: Path) -> Path:
    summary = _read_json(delivery_dir / config.delivery_mesh_summary_filename) or {}
    candidate = summary.get("optimized_mesh")
    if isinstance(candidate, str) and candidate.strip():
        path = Path(candidate).expanduser()
        if path.exists():
            return path
    fallback = delivery_dir / "optimized_mesh.ply"
    if fallback.exists():
        return fallback
    raise RuntimeError(f"optimized_mesh_missing:{delivery_dir}")


def _load_mesh(source: Path):
    try:
        import trimesh
    except Exception as exc:
        raise RuntimeError(f"delivery_mesh_runtime_missing:{exc}") from exc

    mesh_or_scene = trimesh.load(source, force="scene")
    if isinstance(mesh_or_scene, trimesh.Scene):
        geometries = [geometry for geometry in mesh_or_scene.geometry.values() if len(geometry.faces) > 0]
        if not geometries:
            raise RuntimeError(f"delivery_mesh_empty_scene:{source}")
        mesh = trimesh.util.concatenate(geometries)
    else:
        mesh = mesh_or_scene
    if not hasattr(mesh, "faces") or len(mesh.faces) == 0:
        raise RuntimeError(f"delivery_mesh_missing_faces:{source}")
    return mesh


def _drop_small_components(mesh):
    try:
        import trimesh
    except Exception:
        return mesh

    components = list(mesh.split(only_watertight=False))
    if len(components) <= 1:
        return mesh
    largest_faces = max(len(component.faces) for component in components)
    min_faces = max(config.delivery_component_min_faces, int(largest_faces * config.delivery_component_ratio_floor))
    kept = [component for component in components if len(component.faces) >= min_faces]
    if not kept:
        kept = [max(components, key=lambda component: len(component.faces))]
    if len(kept) == 1:
        return kept[0]
    return trimesh.util.concatenate(kept)


def _drop_degenerate_faces(mesh):
    if hasattr(mesh, "nondegenerate_faces"):
        try:
            mask = mesh.nondegenerate_faces()
            mesh.update_faces(mask)
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
    if hasattr(mesh, "unique_faces"):
        try:
            mask = mesh.unique_faces()
            mesh.update_faces(mask)
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
    return mesh


def _simplify_mesh(mesh, *, target_faces: int):
    if len(mesh.faces) <= target_faces:
        return mesh
    simplify = getattr(mesh, "simplify_quadric_decimation", None)
    if callable(simplify):
        for kwargs in (
            {"face_count": target_faces},
            {"faces": target_faces},
            {"percent": max(0.01, min(0.99, target_faces / max(1, len(mesh.faces))))},
        ):
            try:
                simplified = simplify(**kwargs)
                if simplified is not None and len(simplified.faces) > 0:
                    return simplified
            except Exception:
                continue
    return mesh


def _should_simplify_mesh(mesh, *, target_faces: int) -> bool:
    face_count = len(mesh.faces)
    if face_count <= target_faces:
        return False
    if not config.delivery_preserve_geometry_default:
        return True
    return face_count > config.delivery_hard_max_face_count


def _project_vertex_colors(
    *,
    mesh,
    images_dir: Path,
    cameras: dict[int, _Camera],
    poses: list[_ImagePose],
) -> tuple[np.ndarray, int]:
    from PIL import Image

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    vertex_count = len(vertices)
    if vertex_count == 0:
        raise RuntimeError("delivery_texture_empty_mesh")

    normals = np.asarray(mesh.vertex_normals, dtype=np.float64)
    if normals.shape != vertices.shape:
        normals = np.zeros_like(vertices)

    fallback_colors = np.full((vertex_count, 3), 180.0, dtype=np.float64)
    existing_colors = _existing_vertex_colors(mesh)
    if existing_colors is not None:
        fallback_colors = existing_colors[:, :3].astype(np.float64)

    color_accum = np.zeros((vertex_count, 3), dtype=np.float64)
    weight_accum = np.zeros(vertex_count, dtype=np.float64)

    for pose in poses:
        camera = cameras.get(pose.camera_id)
        if camera is None:
            continue
        image_path = images_dir / pose.name
        if not image_path.exists():
            continue
        image = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float64)
        height, width = image.shape[:2]
        rotation = _qvec_to_rotmat(pose.qvec)
        translation = np.asarray(pose.tvec, dtype=np.float64)
        camera_points = (rotation @ vertices.T).T + translation
        depth = camera_points[:, 2]
        valid = depth > 1e-6
        if not np.any(valid):
            continue

        u = camera.fx * (camera_points[:, 0] / depth) + camera.cx
        v = camera.fy * (camera_points[:, 1] / depth) + camera.cy
        valid &= np.isfinite(u) & np.isfinite(v)
        valid &= (u >= 0.0) & (u <= (width - 1)) & (v >= 0.0) & (v <= (height - 1))
        if np.any(valid):
            camera_center = -(rotation.T @ translation)
            view_dirs = camera_center[None, :] - vertices
            view_norm = np.linalg.norm(view_dirs, axis=1, keepdims=True)
            view_norm[view_norm == 0] = 1.0
            view_dirs /= view_norm
            cosine = np.sum(normals * view_dirs, axis=1)
            valid &= cosine > 0.05
        valid_indices = np.where(valid)[0]
        if valid_indices.size == 0:
            continue

        pixel_u = np.clip(np.rint(u[valid_indices]).astype(np.int32), 0, width - 1)
        pixel_v = np.clip(np.rint(v[valid_indices]).astype(np.int32), 0, height - 1)
        sampled = image[pixel_v, pixel_u]
        cosine = np.maximum(0.05, np.sum(normals[valid_indices] * view_dirs[valid_indices], axis=1))
        weights = cosine / np.maximum(depth[valid_indices], 1e-3)
        color_accum[valid_indices] += sampled * weights[:, None]
        weight_accum[valid_indices] += weights

    resolved = fallback_colors.copy()
    observed = weight_accum > 0
    resolved[observed] = color_accum[observed] / weight_accum[observed, None]
    rgba = np.concatenate(
        [
            np.clip(np.rint(resolved), 0, 255).astype(np.uint8),
            np.full((vertex_count, 1), 255, dtype=np.uint8),
        ],
        axis=1,
    )
    return rgba, int(np.count_nonzero(observed))


def _existing_vertex_colors(mesh) -> np.ndarray | None:
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None
    colors = getattr(visual, "vertex_colors", None)
    if colors is None:
        return None
    colors = np.asarray(colors)
    if colors.ndim != 2 or colors.shape[0] != len(mesh.vertices):
        return None
    if colors.shape[1] == 3:
        colors = np.concatenate([colors, np.full((colors.shape[0], 1), 255, dtype=colors.dtype)], axis=1)
    return colors


def _read_cameras(path: Path) -> dict[int, _Camera]:
    cameras: dict[int, _Camera] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 8 or parts[1] != "PINHOLE":
            continue
        camera_id = int(parts[0])
        cameras[camera_id] = _Camera(
            width=int(parts[2]),
            height=int(parts[3]),
            fx=float(parts[4]),
            fy=float(parts[5]),
            cx=float(parts[6]),
            cy=float(parts[7]),
        )
    return cameras


def _read_images(path: Path) -> list[_ImagePose]:
    poses: list[_ImagePose] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 10:
            continue
        poses.append(
            _ImagePose(
                camera_id=int(parts[8]),
                name=parts[9],
                qvec=(float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])),
                tvec=(float(parts[5]), float(parts[6]), float(parts[7])),
            )
        )
        if index < len(lines):
            index += 1
    return poses


def _qvec_to_rotmat(qvec: tuple[float, float, float, float]) -> np.ndarray:
    qw, qx, qy, qz = qvec
    return np.array(
        [
            [
                1 - 2 * (qy * qy + qz * qz),
                2 * (qx * qy - qw * qz),
                2 * (qx * qz + qw * qy),
            ],
            [
                2 * (qx * qy + qw * qz),
                1 - 2 * (qx * qx + qz * qz),
                2 * (qy * qz - qw * qx),
            ],
            [
                2 * (qx * qz - qw * qy),
                2 * (qy * qz + qw * qx),
                1 - 2 * (qx * qx + qy * qy),
            ],
        ],
        dtype=np.float64,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
