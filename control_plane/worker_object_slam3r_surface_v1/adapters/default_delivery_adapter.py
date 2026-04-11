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


@dataclass(frozen=True)
class _ProjectionView:
    pose: _ImagePose
    camera: _Camera
    image: np.ndarray
    rotation: np.ndarray
    translation: np.ndarray
    center: np.ndarray


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
    mesh, topology_summary = _repair_mesh_topology(mesh)
    mesh = _smooth_mesh(mesh)

    try:
        mesh.fix_normals(multibody=True)
    except Exception:
        mesh.fix_normals()

    simplification_applied = False
    simplify_reason = "preserve_geometry"
    target_faces = max(1024, config.delivery_target_face_count)
    if _should_simplify_mesh(mesh, target_faces=target_faces):
        mesh = _simplify_mesh(
            mesh,
            target_faces=_simplify_target_faces(initial_faces, target_faces=target_faces),
        )
        simplification_applied = len(mesh.faces) < initial_faces
        simplify_reason = "hard_face_cap" if initial_faces > config.delivery_hard_max_face_count else "target_budget"

    mesh, final_topology_summary = _repair_mesh_topology(mesh)
    topology_summary = {
        **topology_summary,
        "watertight_after": bool(final_topology_summary["watertight_after"]),
        "hole_fill_iterations": int(topology_summary["hole_fill_iterations"]) + int(final_topology_summary["hole_fill_iterations"]),
        "holes_repaired": bool(topology_summary["holes_repaired"] or final_topology_summary["holes_repaired"]),
        "voxel_fallback_used": bool(topology_summary.get("voxel_fallback_used", False) or final_topology_summary.get("voxel_fallback_used", False)),
        "aggressive_repair_allowed": bool(
            topology_summary.get("aggressive_repair_allowed", True)
            and final_topology_summary.get("aggressive_repair_allowed", True)
        ),
        "closure_strategy": str(final_topology_summary.get("closure_strategy", topology_summary.get("closure_strategy", "native_repair"))),
    }

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
        "watertight_before": bool(topology_summary["watertight_before"]),
        "watertight_after": bool(topology_summary["watertight_after"]),
        "hole_fill_iterations": int(topology_summary["hole_fill_iterations"]),
        "holes_repaired": bool(topology_summary["holes_repaired"]),
        "voxel_fallback_used": bool(topology_summary.get("voxel_fallback_used", False)),
        "aggressive_repair_allowed": bool(topology_summary.get("aggressive_repair_allowed", True)),
        "watertight_gate_passed": bool(topology_summary["watertight_after"]),
        "closure_strategy": str(topology_summary.get("closure_strategy", "native_repair")),
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

    projected_views = _sample_projected_views(
        poses,
        max_views=max(1, int(config.delivery_texture_max_views)),
    )
    loaded_views = _load_delivery_projection_views(
        ctx=ctx,
        images_dir=ctx.sparse2dgs_scene_dir / "images",
        cameras=cameras,
        poses=projected_views,
    )
    vertex_colors, observed_vertex_count = _project_vertex_colors(
        mesh=mesh,
        projection_views=loaded_views,
    )
    textured_mesh, atlas_summary = _bake_uv_textured_mesh(
        mesh=mesh,
        vertex_colors=vertex_colors,
        projection_views=loaded_views,
        atlas_size=max(256, int(config.delivery_texture_atlas_size)),
    )
    glb_path = ctx.delivery_dir / "default_mesh.glb"
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    textured_mesh.export(glb_path)
    glb_size_bytes = glb_path.stat().st_size if glb_path.exists() else 0

    summary = {
        "optimized_mesh": str(optimized_mesh),
        "default_asset": str(glb_path),
        "projection_mode": "per_texel_best_view_photo_projection",
        "texture_bake_mode": "uv_atlas_from_visible_photo_projection",
        "projection_source": "curated_center_crop_highres",
        "projected_view_count": len(loaded_views),
        "observed_vertex_count": int(observed_vertex_count),
        "face_count": int(len(textured_mesh.faces)),
        "vertex_count": int(len(textured_mesh.vertices)),
        "atlas_size": int(atlas_summary["atlas_size"]),
        "atlas_observed_pixel_count": int(atlas_summary["observed_pixel_count"]),
        "atlas_coverage_ratio": float(atlas_summary["coverage_ratio"]),
        "atlas_photo_projected_pixel_count": int(atlas_summary["photo_projected_pixel_count"]),
        "atlas_photo_projected_face_count": int(atlas_summary["photo_projected_face_count"]),
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


def _repair_mesh_topology(mesh):
    aggressive_repair_allowed = len(mesh.faces) <= int(config.delivery_aggressive_repair_face_cap)
    summary = {
        "watertight_before": bool(getattr(mesh, "is_watertight", False)),
        "watertight_after": bool(getattr(mesh, "is_watertight", False)),
        "hole_fill_iterations": 0,
        "holes_repaired": False,
        "voxel_fallback_used": False,
        "closure_strategy": "boundary_preserving_native_repair" if aggressive_repair_allowed else "boundary_preserving_open_mesh",
        "aggressive_repair_allowed": bool(aggressive_repair_allowed),
    }
    try:
        import trimesh
    except Exception:
        return mesh, summary

    repair = getattr(trimesh, "repair", None)
    if repair is None:
        return mesh, summary

    if not aggressive_repair_allowed:
        try:
            if hasattr(repair, "fix_winding"):
                repair.fix_winding(mesh)
        except Exception:
            pass
        try:
            if hasattr(repair, "fix_inversion"):
                try:
                    repair.fix_inversion(mesh, multibody=True)
                except TypeError:
                    repair.fix_inversion(mesh)
        except Exception:
            pass
        try:
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
        summary["watertight_after"] = bool(getattr(mesh, "is_watertight", False))
        if not summary["watertight_after"]:
            summary["closure_strategy"] = "boundary_preserving_open_mesh"
        return mesh, summary

    for _ in range(max(1, int(config.delivery_hole_fill_iterations))):
        summary["hole_fill_iterations"] += 1
        try:
            if hasattr(mesh, "process"):
                mesh.process(validate=True)
        except Exception:
            pass
        try:
            if hasattr(repair, "fix_winding"):
                repair.fix_winding(mesh)
        except Exception:
            pass
        try:
            if hasattr(repair, "fix_inversion"):
                try:
                    repair.fix_inversion(mesh, multibody=True)
                except TypeError:
                    repair.fix_inversion(mesh)
        except Exception:
            pass
        try:
            if hasattr(repair, "fill_holes"):
                repaired = repair.fill_holes(mesh)
                summary["holes_repaired"] = bool(summary["holes_repaired"] or repaired)
        except Exception:
            pass
        try:
            mesh.remove_unreferenced_vertices()
        except Exception:
            pass
        if bool(getattr(mesh, "is_watertight", False)):
            break

    if config.delivery_enforce_watertight and config.delivery_enable_voxel_watertight_fallback and not bool(getattr(mesh, "is_watertight", False)):
        voxel_mesh = _voxelize_watertight_mesh(mesh)
        if voxel_mesh is not None and _is_reasonable_watertight_fallback(mesh, voxel_mesh):
            mesh = voxel_mesh
            summary["voxel_fallback_used"] = True
            summary["closure_strategy"] = "voxel_fill"

    summary["watertight_after"] = bool(getattr(mesh, "is_watertight", False))
    if not summary["watertight_after"]:
        summary["closure_strategy"] = "boundary_preserving_open_mesh"
    return mesh, summary


def _voxelize_watertight_mesh(mesh):
    try:
        bounds = np.asarray(mesh.bounds, dtype=np.float64)
        extent = bounds[1] - bounds[0]
        max_extent = float(np.max(extent))
        if not math.isfinite(max_extent) or max_extent <= 1e-6:
            return None
        start_resolution = max(48, int(config.delivery_watertight_voxel_resolution))
        min_resolution = max(24, int(config.delivery_watertight_voxel_min_resolution))
        tried: set[int] = set()
        resolution = start_resolution
        while resolution >= min_resolution:
            if resolution in tried:
                break
            tried.add(resolution)
            pitch = max(max_extent / float(resolution), 1e-4)
            voxel_grid = mesh.voxelized(pitch)
            voxel_grid = voxel_grid.fill()
            watertight_mesh = voxel_grid.marching_cubes
            if watertight_mesh is not None and len(watertight_mesh.faces) > 0:
                watertight_mesh.remove_unreferenced_vertices()
                if bool(getattr(watertight_mesh, "is_watertight", False)):
                    return watertight_mesh
            resolution = int(resolution * 0.75)
    except Exception:
        return None
    return None


def _is_reasonable_watertight_fallback(source_mesh, candidate_mesh) -> bool:
    try:
        source_faces = int(len(source_mesh.faces))
        candidate_faces = int(len(candidate_mesh.faces))
    except Exception:
        return False
    if source_faces <= 0 or candidate_faces <= 0:
        return False
    if not bool(getattr(candidate_mesh, "is_watertight", False)):
        return False

    # Reject "successful" closures that collapse a detailed mesh into a tiny hull-like shell.
    min_faces = 2048 if source_faces >= 50_000 else 256
    min_ratio = 0.02 if source_faces >= 50_000 else 0.005
    return candidate_faces >= max(min_faces, int(source_faces * min_ratio))


def _smooth_mesh(mesh):
    iterations = max(0, int(config.delivery_taubin_iterations))
    if iterations <= 0:
        return mesh
    faces = np.asarray(mesh.faces, dtype=np.int64)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    if faces.ndim != 2 or faces.shape[1] != 3 or len(vertices) == 0:
        return mesh
    boundary_mask = _boundary_vertex_mask(faces, vertex_count=len(vertices))
    movable_indices = np.flatnonzero(~boundary_mask)
    if movable_indices.size == 0:
        return mesh
    neighbors = _vertex_neighbors(faces, vertex_count=len(vertices))
    current = vertices.copy()
    lamb = float(config.delivery_taubin_lambda)
    nu = float(config.delivery_taubin_nu)
    try:
        for _ in range(iterations):
            current = _boundary_preserving_laplacian_pass(
                current,
                neighbors=neighbors,
                movable_indices=movable_indices,
                factor=lamb,
            )
            current = _boundary_preserving_laplacian_pass(
                current,
                neighbors=neighbors,
                movable_indices=movable_indices,
                factor=nu,
            )
    except Exception:
        return mesh
    mesh.vertices = current
    return mesh


def _boundary_vertex_mask(faces: np.ndarray, *, vertex_count: int) -> np.ndarray:
    edges = np.sort(
        np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    unique_edges, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = unique_edges[counts == 1]
    mask = np.zeros(vertex_count, dtype=bool)
    if boundary_edges.size > 0:
        mask[np.unique(boundary_edges.reshape(-1))] = True
    return mask


def _vertex_neighbors(faces: np.ndarray, *, vertex_count: int) -> list[np.ndarray]:
    adjacency: list[set[int]] = [set() for _ in range(vertex_count)]
    for face in faces.tolist():
        a, b, c = int(face[0]), int(face[1]), int(face[2])
        adjacency[a].update((b, c))
        adjacency[b].update((a, c))
        adjacency[c].update((a, b))
    return [np.asarray(sorted(neighbors), dtype=np.int64) for neighbors in adjacency]


def _boundary_preserving_laplacian_pass(
    vertices: np.ndarray,
    *,
    neighbors: list[np.ndarray],
    movable_indices: np.ndarray,
    factor: float,
) -> np.ndarray:
    updated = vertices.copy()
    for index in movable_indices.tolist():
        neighbor_indices = neighbors[index]
        if neighbor_indices.size < 2:
            continue
        centroid = vertices[neighbor_indices].mean(axis=0)
        updated[index] = vertices[index] + factor * (centroid - vertices[index])
    return updated


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


def _simplify_target_faces(face_count: int, *, target_faces: int) -> int:
    if not config.delivery_preserve_geometry_default:
        return target_faces
    if face_count > config.delivery_hard_max_face_count:
        return max(target_faces, int(config.delivery_hard_max_face_count))
    return face_count


def _project_vertex_colors(
    *,
    mesh,
    projection_views: list[_ProjectionView],
) -> tuple[np.ndarray, int]:
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

    for view in projection_views:
        camera = view.camera
        image = view.image
        height, width = image.shape[:2]
        rotation = view.rotation
        translation = view.translation
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
            view_dirs = view.center[None, :] - vertices
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


def _sample_projected_views(poses: list[_ImagePose], *, max_views: int) -> list[_ImagePose]:
    if not poses:
        return []
    if len(poses) <= max_views:
        return poses
    if max_views <= 1:
        return [poses[len(poses) // 2]]

    indices = np.linspace(0, len(poses) - 1, num=max_views, dtype=np.int64)
    deduped: list[_ImagePose] = []
    seen: set[int] = set()
    for index in indices.tolist():
        if index in seen:
            continue
        seen.add(index)
        deduped.append(poses[index])
    if len(deduped) < max_views:
        for index, pose in enumerate(poses):
            if index in seen:
                continue
            deduped.append(pose)
            seen.add(index)
            if len(deduped) >= max_views:
                break
    return deduped


def _load_delivery_projection_views(
    *,
    ctx: JobContext,
    images_dir: Path,
    cameras: dict[int, _Camera],
    poses: list[_ImagePose],
) -> list[_ProjectionView]:
    projection_views: list[_ProjectionView] = []
    loaded_pose_names: set[str] = set()
    curated_paths = _sorted_curated_frame_paths(ctx)

    for pose in poses:
        camera = cameras.get(pose.camera_id)
        if camera is None:
            continue
        curated_path = _resolve_curated_projection_path(curated_paths, pose.name)
        if curated_path is None:
            continue
        projection_view = _load_curated_projection_view(
            curated_path=curated_path,
            camera=camera,
            pose=pose,
        )
        if projection_view is None:
            continue
        projection_views.append(projection_view)
        loaded_pose_names.add(pose.name)

    missing_poses = [pose for pose in poses if pose.name not in loaded_pose_names]
    if missing_poses:
        projection_views.extend(
            _load_projection_views(
                images_dir=images_dir,
                cameras=cameras,
                poses=missing_poses,
            )
        )
    return projection_views


def _load_projection_views(
    *,
    images_dir: Path,
    cameras: dict[int, _Camera],
    poses: list[_ImagePose],
) -> list[_ProjectionView]:
    from PIL import Image

    projection_views: list[_ProjectionView] = []
    for pose in poses:
        camera = cameras.get(pose.camera_id)
        if camera is None:
            continue
        image_path = images_dir / pose.name
        if not image_path.exists():
            continue
        image = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float64)
        rotation = _qvec_to_rotmat(pose.qvec)
        translation = np.asarray(pose.tvec, dtype=np.float64)
        center = -(rotation.T @ translation)
        projection_views.append(
            _ProjectionView(
                pose=pose,
                camera=camera,
                image=image,
                rotation=rotation,
                translation=translation,
                center=center,
            )
        )
    return projection_views


def _sorted_curated_frame_paths(ctx: JobContext) -> list[Path]:
    if ctx.curated_dir is None or not ctx.curated_dir.exists():
        return []
    return sorted(
        path
        for path in ctx.curated_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )


def _resolve_curated_projection_path(curated_paths: list[Path], pose_name: str) -> Path | None:
    stem = Path(pose_name).stem
    try:
        frame_index = int(stem)
    except ValueError:
        return None
    if frame_index < 0 or frame_index >= len(curated_paths):
        return None
    return curated_paths[frame_index]


def _load_curated_projection_view(
    *,
    curated_path: Path,
    camera: _Camera,
    pose: _ImagePose,
) -> _ProjectionView | None:
    from PIL import Image

    if not curated_path.exists():
        return None

    image = np.asarray(Image.open(curated_path).convert("RGB"), dtype=np.float64)
    square = _center_crop_square(image)
    target_size = max(256, int(config.delivery_projection_image_size))
    output_size = min(square.shape[0], target_size)
    if output_size <= 0:
        return None
    if square.shape[0] != output_size:
        square = np.asarray(
            Image.fromarray(square.astype(np.uint8), mode="RGB").resize((output_size, output_size), Image.BILINEAR),
            dtype=np.float64,
        )

    scale = float(output_size) / max(float(camera.width), 1.0)
    scaled_camera = _Camera(
        width=int(output_size),
        height=int(output_size),
        fx=float(camera.fx * scale),
        fy=float(camera.fy * scale),
        cx=float(camera.cx * scale),
        cy=float(camera.cy * scale),
    )
    rotation = _qvec_to_rotmat(pose.qvec)
    translation = np.asarray(pose.tvec, dtype=np.float64)
    center = -(rotation.T @ translation)
    return _ProjectionView(
        pose=pose,
        camera=scaled_camera,
        image=square,
        rotation=rotation,
        translation=translation,
        center=center,
    )


def _center_crop_square(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    side = min(height, width)
    top = max(0, (height - side) // 2)
    left = max(0, (width - side) // 2)
    return np.ascontiguousarray(image[top : top + side, left : left + side])


def _bake_uv_textured_mesh(*, mesh, vertex_colors: np.ndarray, projection_views: list[_ProjectionView], atlas_size: int):
    try:
        import trimesh
        import xatlas
        from PIL import Image
        from trimesh.visual.material import SimpleMaterial
        from trimesh.visual.texture import TextureVisuals
    except Exception as exc:
        raise RuntimeError(f"delivery_texture_runtime_missing:{exc}") from exc

    vertices = np.asarray(mesh.vertices, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.uint32)
    if len(vertices) == 0 or len(faces) == 0:
        raise RuntimeError("delivery_texture_empty_mesh")

    vmapping, remapped_faces, uvs = xatlas.parametrize(vertices, faces)
    remapped_faces = np.asarray(remapped_faces, dtype=np.int64)
    uvs = np.asarray(uvs, dtype=np.float32)
    vmapping = np.asarray(vmapping, dtype=np.int64)

    baked_vertices = vertices[vmapping]
    baked_normals = np.asarray(mesh.vertex_normals, dtype=np.float32)
    if baked_normals.shape == vertices.shape:
        baked_normals = baked_normals[vmapping]
    else:
        baked_normals = None
    baked_colors = np.asarray(vertex_colors[:, :3], dtype=np.float32)[vmapping]

    fallback_rgb, fallback_coverage = _rasterize_vertex_color_atlas(
        uvs=uvs,
        faces=remapped_faces,
        colors=baked_colors,
        atlas_size=atlas_size,
    )
    texture_rgb, observed_pixel_count, projected_pixel_count, projected_face_count = _rasterize_photo_projection_atlas(
        vertices=baked_vertices.astype(np.float64),
        uvs=uvs,
        faces=remapped_faces,
        projection_views=projection_views,
        atlas_size=atlas_size,
        fallback_rgb=fallback_rgb,
        fallback_coverage=fallback_coverage,
    )

    pil_image = Image.fromarray(texture_rgb, mode="RGB")
    material = SimpleMaterial(image=pil_image)
    visual = TextureVisuals(uv=uvs.astype(np.float64), image=pil_image, material=material)
    textured_mesh = trimesh.Trimesh(
        vertices=baked_vertices,
        faces=remapped_faces,
        vertex_normals=baked_normals,
        visual=visual,
        process=False,
    )
    atlas_summary = {
        "atlas_size": atlas_size,
        "observed_pixel_count": int(observed_pixel_count),
        "coverage_ratio": float(observed_pixel_count / max(1, atlas_size * atlas_size)),
        "photo_projected_pixel_count": int(projected_pixel_count),
        "photo_projected_face_count": int(projected_face_count),
    }
    return textured_mesh, atlas_summary


def _rasterize_vertex_color_atlas(
    *,
    uvs: np.ndarray,
    faces: np.ndarray,
    colors: np.ndarray,
    atlas_size: int,
) -> tuple[np.ndarray, int]:
    import cv2

    atlas = np.zeros((atlas_size, atlas_size, 3), dtype=np.uint8)
    coverage = np.zeros((atlas_size, atlas_size), dtype=np.uint8)

    uv_pixels = np.empty_like(uvs, dtype=np.float32)
    uv_pixels[:, 0] = np.clip(uvs[:, 0], 0.0, 1.0) * float(atlas_size - 1)
    uv_pixels[:, 1] = (1.0 - np.clip(uvs[:, 1], 0.0, 1.0)) * float(atlas_size - 1)

    for face in faces:
        tri_uv = uv_pixels[face]
        tri_colors = colors[face]
        min_x = max(0, int(np.floor(np.min(tri_uv[:, 0]))))
        max_x = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 0]))))
        min_y = max(0, int(np.floor(np.min(tri_uv[:, 1]))))
        max_y = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 1]))))
        if min_x >= max_x or min_y >= max_y:
            continue
        triangle_area = _edge_function(tri_uv[0], tri_uv[1], tri_uv[2])
        if abs(triangle_area) < 1e-5:
            continue

        xs = np.arange(min_x, max_x + 1, dtype=np.float32) + 0.5
        ys = np.arange(min_y, max_y + 1, dtype=np.float32) + 0.5
        grid_x, grid_y = np.meshgrid(xs, ys)
        points = np.stack([grid_x, grid_y], axis=-1)

        w0 = _edge_function(tri_uv[1], tri_uv[2], points)
        w1 = _edge_function(tri_uv[2], tri_uv[0], points)
        w2 = _edge_function(tri_uv[0], tri_uv[1], points)
        if triangle_area < 0:
            inside = (w0 <= 0) & (w1 <= 0) & (w2 <= 0)
        else:
            inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not np.any(inside):
            continue

        w0 = w0 / triangle_area
        w1 = w1 / triangle_area
        w2 = w2 / triangle_area
        interpolated = (
            w0[..., None] * tri_colors[0][None, None, :]
            + w1[..., None] * tri_colors[1][None, None, :]
            + w2[..., None] * tri_colors[2][None, None, :]
        )
        patch = atlas[min_y : max_y + 1, min_x : max_x + 1]
        patch_coverage = coverage[min_y : max_y + 1, min_x : max_x + 1]
        patch[inside] = np.clip(np.rint(interpolated[inside]), 0, 255).astype(np.uint8)
        patch_coverage[inside] = 255

    if np.any(coverage):
        kernel_size = max(1, int(config.delivery_texture_fill_kernel))
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(atlas, kernel, iterations=1)
        mask = coverage == 0
        atlas[mask] = dilated[mask]

    return atlas, coverage


def _rasterize_photo_projection_atlas(
    *,
    vertices: np.ndarray,
    uvs: np.ndarray,
    faces: np.ndarray,
    projection_views: list[_ProjectionView],
    atlas_size: int,
    fallback_rgb: np.ndarray,
    fallback_coverage: np.ndarray,
) -> tuple[np.ndarray, int, int, int]:
    import cv2

    atlas = fallback_rgb.copy()
    coverage = fallback_coverage.copy()
    uv_pixels = np.empty_like(uvs, dtype=np.float64)
    uv_pixels[:, 0] = np.clip(uvs[:, 0], 0.0, 1.0) * float(atlas_size - 1)
    uv_pixels[:, 1] = (1.0 - np.clip(uvs[:, 1], 0.0, 1.0)) * float(atlas_size - 1)

    photo_projected_pixel_count = 0
    projected_face_count = 0

    for face in faces:
        tri_vertices = vertices[face]
        tri_uv = uv_pixels[face]
        best_view = _select_best_projection_view(tri_vertices, projection_views)
        if best_view is None:
            continue
        projected = _project_face_to_photo(
            tri_vertices=tri_vertices,
            tri_uv=tri_uv,
            view=best_view,
            atlas=atlas,
            coverage=coverage,
        )
        if projected <= 0:
            projected = _fill_face_from_centroid_sample(
                tri_vertices=tri_vertices,
                tri_uv=tri_uv,
                view=best_view,
                atlas=atlas,
                coverage=coverage,
            )
        if projected > 0:
            projected_face_count += 1
            photo_projected_pixel_count += projected

    if np.any(coverage):
        kernel_size = max(1, int(config.delivery_texture_fill_kernel))
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        dilated = cv2.dilate(atlas, kernel, iterations=1)
        mask = coverage == 0
        atlas[mask] = dilated[mask]

    observed_pixel_count = int(np.count_nonzero(coverage))
    return atlas, observed_pixel_count, int(photo_projected_pixel_count), int(projected_face_count)


def _select_best_projection_view(
    tri_vertices: np.ndarray,
    projection_views: list[_ProjectionView],
) -> _ProjectionView | None:
    if not projection_views:
        return None
    normal = np.cross(tri_vertices[1] - tri_vertices[0], tri_vertices[2] - tri_vertices[0])
    normal_norm = np.linalg.norm(normal)
    if normal_norm < 1e-8:
        return None
    normal = normal / normal_norm
    centroid = np.mean(tri_vertices, axis=0)

    best_view = _select_best_projection_view_with_min_cosine(
        tri_vertices=tri_vertices,
        projection_views=projection_views,
        centroid=centroid,
        normal=normal,
        min_cosine=float(config.delivery_texture_min_view_cosine),
    )
    if best_view is not None:
        return best_view
    return _select_best_projection_view_with_min_cosine(
        tri_vertices=tri_vertices,
        projection_views=projection_views,
        centroid=centroid,
        normal=normal,
        min_cosine=-1.0,
    )


def _select_best_projection_view_with_min_cosine(
    *,
    tri_vertices: np.ndarray,
    projection_views: list[_ProjectionView],
    centroid: np.ndarray,
    normal: np.ndarray,
    min_cosine: float,
) -> _ProjectionView | None:
    best_score = -math.inf
    best_view: _ProjectionView | None = None
    for view in projection_views:
        camera_points = (view.rotation @ tri_vertices.T).T + view.translation
        depth = camera_points[:, 2]
        if np.any(depth <= 1e-5):
            continue
        u = view.camera.fx * (camera_points[:, 0] / depth) + view.camera.cx
        v = view.camera.fy * (camera_points[:, 1] / depth) + view.camera.cy
        width = view.camera.width
        height = view.camera.height
        if np.any(~np.isfinite(u)) or np.any(~np.isfinite(v)):
            continue
        if np.any(u < 0.0) or np.any(u > (width - 1)) or np.any(v < 0.0) or np.any(v > (height - 1)):
            continue

        view_dir = view.center - centroid
        view_norm = np.linalg.norm(view_dir)
        if view_norm <= 1e-8:
            continue
        view_dir = view_dir / view_norm
        cosine = float(np.dot(normal, view_dir))
        if cosine <= min_cosine:
            continue

        area = abs(
            (u[1] - u[0]) * (v[2] - v[0])
            - (v[1] - v[0]) * (u[2] - u[0])
        ) * 0.5
        if area <= 1e-4:
            continue

        score = max(cosine, 0.05) * math.sqrt(area) / max(float(np.mean(depth)), 1e-3)
        if score > best_score:
            best_score = score
            best_view = view
    return best_view


def _project_face_to_photo(
    *,
    tri_vertices: np.ndarray,
    tri_uv: np.ndarray,
    view: _ProjectionView,
    atlas: np.ndarray,
    coverage: np.ndarray,
) -> int:
    triangle_area = _edge_function(tri_uv[0], tri_uv[1], tri_uv[2])
    if abs(triangle_area) < 1e-6:
        return 0

    atlas_size = atlas.shape[0]
    min_x = max(0, int(np.floor(np.min(tri_uv[:, 0]))))
    max_x = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 0]))))
    min_y = max(0, int(np.floor(np.min(tri_uv[:, 1]))))
    max_y = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 1]))))
    if min_x >= max_x or min_y >= max_y:
        return 0

    xs = np.arange(min_x, max_x + 1, dtype=np.float64) + 0.5
    ys = np.arange(min_y, max_y + 1, dtype=np.float64) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.stack([grid_x, grid_y], axis=-1)

    w0 = _edge_function(tri_uv[1], tri_uv[2], points)
    w1 = _edge_function(tri_uv[2], tri_uv[0], points)
    w2 = _edge_function(tri_uv[0], tri_uv[1], points)
    if triangle_area < 0:
        inside = (w0 <= 0) & (w1 <= 0) & (w2 <= 0)
    else:
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not np.any(inside):
        return 0

    w0 = w0 / triangle_area
    w1 = w1 / triangle_area
    w2 = w2 / triangle_area
    positions = (
        w0[..., None] * tri_vertices[0][None, None, :]
        + w1[..., None] * tri_vertices[1][None, None, :]
        + w2[..., None] * tri_vertices[2][None, None, :]
    )
    flat_positions = positions[inside]
    camera_points = (view.rotation @ flat_positions.T).T + view.translation
    depth = camera_points[:, 2]
    valid = depth > 1e-6
    if not np.any(valid):
        return 0

    u = view.camera.fx * (camera_points[:, 0] / depth) + view.camera.cx
    v = view.camera.fy * (camera_points[:, 1] / depth) + view.camera.cy
    valid &= np.isfinite(u) & np.isfinite(v)
    valid &= (u >= 0.0) & (u <= (view.camera.width - 1)) & (v >= 0.0) & (v <= (view.camera.height - 1))
    if not np.any(valid):
        return 0

    sampled = _sample_bilinear_rgb(view.image, u[valid], v[valid])
    patch = atlas[min_y : max_y + 1, min_x : max_x + 1]
    patch_coverage = coverage[min_y : max_y + 1, min_x : max_x + 1]
    inside_indices = np.argwhere(inside)
    valid_inside_indices = inside_indices[valid]
    patch[valid_inside_indices[:, 0], valid_inside_indices[:, 1]] = sampled
    patch_coverage[valid_inside_indices[:, 0], valid_inside_indices[:, 1]] = 255
    return int(len(valid_inside_indices))


def _fill_face_from_centroid_sample(
    *,
    tri_vertices: np.ndarray,
    tri_uv: np.ndarray,
    view: _ProjectionView,
    atlas: np.ndarray,
    coverage: np.ndarray,
) -> int:
    centroid = np.mean(tri_vertices, axis=0, keepdims=True)
    camera_points = (view.rotation @ centroid.T).T + view.translation
    depth = camera_points[:, 2]
    if float(depth[0]) <= 1e-6:
        return 0

    u = view.camera.fx * (camera_points[:, 0] / depth) + view.camera.cx
    v = view.camera.fy * (camera_points[:, 1] / depth) + view.camera.cy
    if not (np.isfinite(u[0]) and np.isfinite(v[0])):
        return 0
    if u[0] < 0.0 or u[0] > (view.camera.width - 1) or v[0] < 0.0 or v[0] > (view.camera.height - 1):
        return 0

    sampled = _sample_bilinear_rgb(view.image, u, v)[0]
    triangle_area = _edge_function(tri_uv[0], tri_uv[1], tri_uv[2])
    if abs(triangle_area) < 1e-6:
        return 0

    atlas_size = atlas.shape[0]
    min_x = max(0, int(np.floor(np.min(tri_uv[:, 0]))))
    max_x = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 0]))))
    min_y = max(0, int(np.floor(np.min(tri_uv[:, 1]))))
    max_y = min(atlas_size - 1, int(np.ceil(np.max(tri_uv[:, 1]))))
    if min_x >= max_x or min_y >= max_y:
        return 0

    xs = np.arange(min_x, max_x + 1, dtype=np.float64) + 0.5
    ys = np.arange(min_y, max_y + 1, dtype=np.float64) + 0.5
    grid_x, grid_y = np.meshgrid(xs, ys)
    points = np.stack([grid_x, grid_y], axis=-1)

    w0 = _edge_function(tri_uv[1], tri_uv[2], points)
    w1 = _edge_function(tri_uv[2], tri_uv[0], points)
    w2 = _edge_function(tri_uv[0], tri_uv[1], points)
    if triangle_area < 0:
        inside = (w0 <= 0) & (w1 <= 0) & (w2 <= 0)
    else:
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
    if not np.any(inside):
        return 0

    patch = atlas[min_y : max_y + 1, min_x : max_x + 1]
    patch_coverage = coverage[min_y : max_y + 1, min_x : max_x + 1]
    patch[inside] = sampled[None, :]
    patch_coverage[inside] = 255
    return int(np.count_nonzero(inside))


def _sample_bilinear_rgb(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    u0 = np.floor(u).astype(np.int64)
    v0 = np.floor(v).astype(np.int64)
    u1 = np.clip(u0 + 1, 0, width - 1)
    v1 = np.clip(v0 + 1, 0, height - 1)
    u0 = np.clip(u0, 0, width - 1)
    v0 = np.clip(v0, 0, height - 1)

    du = (u - u0).astype(np.float64)
    dv = (v - v0).astype(np.float64)

    top_left = image[v0, u0]
    top_right = image[v0, u1]
    bottom_left = image[v1, u0]
    bottom_right = image[v1, u1]

    top = top_left * (1.0 - du[:, None]) + top_right * du[:, None]
    bottom = bottom_left * (1.0 - du[:, None]) + bottom_right * du[:, None]
    interpolated = top * (1.0 - dv[:, None]) + bottom * dv[:, None]
    return np.clip(np.rint(interpolated), 0, 255).astype(np.uint8)


def _edge_function(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    return (c[..., 0] - a[0]) * (b[1] - a[1]) - (c[..., 1] - a[1]) * (b[0] - a[0])


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
