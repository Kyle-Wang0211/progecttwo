#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import shutil
from pathlib import Path

import numpy as np
import open3d as o3d
from plyfile import PlyData, PlyElement

try:
    from PIL import Image
except Exception:  # pragma: no cover - optional dependency on worker image stack
    Image = None

try:
    import cv2
except Exception:  # pragma: no cover - optional dependency on worker image stack
    cv2 = None


@dataclass(frozen=True)
class Camera:
    image_name: str
    width: int
    height: int
    R: np.ndarray
    T: np.ndarray
    K: np.ndarray


def sigmoid(x: np.ndarray) -> np.ndarray:
    clipped = np.clip(x, -60.0, 60.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def load_vertex(path: Path) -> tuple[PlyData, np.ndarray]:
    ply = PlyData.read(str(path))
    if "vertex" not in ply:
        raise RuntimeError(f"missing vertex element in {path}")
    return ply, ply["vertex"].data


def field_names(vertex: np.ndarray) -> tuple[str, ...]:
    names = vertex.dtype.names
    if not names:
        raise RuntimeError("vertex element has no named fields")
    return tuple(names)


def stack_fields(vertex: np.ndarray, names: list[str]) -> np.ndarray:
    return np.stack([np.asarray(vertex[name], dtype=np.float32) for name in names], axis=1)


def percentile_threshold(values: np.ndarray, *, scene_span: float) -> float:
    if values.size == 0:
        return max(scene_span * 0.02, 0.05)
    q99 = float(np.quantile(values, 0.99))
    median = float(np.median(values))
    return max(scene_span * 0.02, median * 20.0, q99 * 2.5)


def write_vertex_like(source_vertex: np.ndarray, mask: np.ndarray, out_path: Path) -> None:
    names = field_names(source_vertex)
    dtype = source_vertex.dtype.descr
    filtered = np.empty(int(np.count_nonzero(mask)), dtype=dtype)
    for name in names:
        filtered[name] = source_vertex[name][mask]
    PlyData([PlyElement.describe(filtered, "vertex")], text=False).write(str(out_path))


def load_rgb_image(path: Path) -> np.ndarray:
    if Image is None:
        raise RuntimeError("Pillow is required for semantic mask-guided cleanup")
    image = Image.open(path).convert("RGB")
    return np.asarray(image, dtype=np.float32) / 255.0


def find_matching_image(base_name: str, directory: Path | None) -> Path | None:
    if directory is None or not directory.is_dir():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".PNG", ".JPG", ".JPEG"):
        candidate = directory / f"{base_name}{ext}"
        if candidate.is_file():
            return candidate
    return None


def load_cameras_json(path: Path | None) -> list[Camera]:
    if path is None or not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("cameras") or payload.get("frames") or payload.get("views") or []
    cameras: list[Camera] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        image_name = str(entry.get("image_name") or entry.get("file_name") or entry.get("name") or "")
        width = int(entry.get("width") or entry.get("w") or 0)
        height = int(entry.get("height") or entry.get("h") or 0)
        r = np.asarray(entry.get("R"), dtype=np.float32)
        t = np.asarray(entry.get("T"), dtype=np.float32)
        k = np.asarray(entry.get("K"), dtype=np.float32)
        if not image_name or width <= 0 or height <= 0:
            continue
        if r.shape != (3, 3) or t.shape not in ((3,), (3, 1)) or k.shape != (3, 3):
            continue
        cameras.append(
            Camera(
                image_name=image_name,
                width=width,
                height=height,
                R=r.reshape(3, 3),
                T=t.reshape(3),
                K=k.reshape(3, 3),
            )
        )
    return cameras


def project_points(points: np.ndarray, camera: Camera) -> tuple[np.ndarray, np.ndarray]:
    if points.size == 0:
        return np.empty((0, 2), dtype=np.int32), np.empty((0,), dtype=np.float32)
    r_w2c = camera.R.T
    t_w2c = -r_w2c @ camera.T
    xyz_cam = (r_w2c @ points.T).T + t_w2c.reshape(1, 3)
    valid = xyz_cam[:, 2] > 1e-6
    if not np.any(valid):
        return np.empty((0, 2), dtype=np.int32), np.empty((0,), dtype=np.float32)
    xyz_cam = xyz_cam[valid]
    uv_h = (camera.K @ xyz_cam.T).T
    u = uv_h[:, 0] / np.maximum(uv_h[:, 2], 1e-6)
    v = uv_h[:, 1] / np.maximum(uv_h[:, 2], 1e-6)
    inside = (
        (u >= 0.0) &
        (v >= 0.0) &
        (u < float(camera.width)) &
        (v < float(camera.height))
    )
    if not np.any(inside):
        return np.empty((0, 2), dtype=np.int32), np.empty((0,), dtype=np.float32)
    pixels = np.stack(
        [
            np.asarray(np.round(u[inside]), dtype=np.int32),
            np.asarray(np.round(v[inside]), dtype=np.int32),
        ],
        axis=1,
    )
    depths = np.asarray(xyz_cam[inside, 2], dtype=np.float32)
    return pixels, depths


def keep_seeded_component(mask: np.ndarray, seed_mask: np.ndarray) -> np.ndarray:
    if cv2 is None:
        return mask
    if mask.size == 0 or int(np.count_nonzero(mask)) == 0:
        return mask
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if component_count <= 2:
        return mask
    seed_binary = seed_mask > 0
    best_label = 1
    best_score = -1
    for label in range(1, component_count):
        component = labels == label
        overlap = int(np.count_nonzero(component & seed_binary))
        area = int(stats[label, cv2.CC_STAT_AREA])
        score = overlap * 1_000_000 + area
        if score > best_score:
            best_score = score
            best_label = label
    kept = np.where(labels == best_label, 255, 0).astype(np.uint8)
    kept[seed_binary] = 255
    return kept


def auto_generate_masks_dir(
    cameras: list[Camera],
    images_dir: Path | None,
    surface_points: np.ndarray,
    out_dir: Path,
    *,
    scene_span: float,
) -> tuple[Path | None, dict[str, object]]:
    stats: dict[str, object] = {
        "generated": 0,
        "available_views": 0,
        "source": "none",
    }
    if (
        cv2 is None or
        Image is None or
        images_dir is None or
        not images_dir.is_dir() or
        surface_points.shape[0] == 0 or
        not cameras
    ):
        return None, stats

    out_dir.mkdir(parents=True, exist_ok=True)
    surface_samples = surface_points
    if surface_samples.shape[0] > 120_000:
        stride = int(math.ceil(surface_samples.shape[0] / 120_000))
        surface_samples = surface_samples[::stride]

    dilation_radius = max(6, min(24, int(round(max(scene_span * 6.0, 8.0)))))
    close_radius = max(3, dilation_radius // 2)

    for camera in cameras:
        image_path = find_matching_image(Path(camera.image_name).stem, images_dir)
        if image_path is None:
            continue
        image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            continue
        if image_bgr.shape[1] != camera.width or image_bgr.shape[0] != camera.height:
            image_bgr = cv2.resize(image_bgr, (camera.width, camera.height), interpolation=cv2.INTER_LINEAR)

        pixels, _ = project_points(surface_samples, camera)
        if pixels.shape[0] < 32:
            continue

        stats["available_views"] = int(stats["available_views"]) + 1
        seed_mask = np.zeros((camera.height, camera.width), dtype=np.uint8)
        seed_mask[pixels[:, 1], pixels[:, 0]] = 255
        seed_mask = cv2.dilate(
            seed_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilation_radius * 2 + 1, dilation_radius * 2 + 1)),
            iterations=1,
        )
        ys, xs = np.where(seed_mask > 0)
        if xs.size < 32:
            continue

        pad = max(20, dilation_radius * 3)
        x0 = max(0, int(xs.min()) - pad)
        x1 = min(camera.width - 1, int(xs.max()) + pad)
        y0 = max(0, int(ys.min()) - pad)
        y1 = min(camera.height - 1, int(ys.max()) + pad)

        gc_mask = np.full((camera.height, camera.width), cv2.GC_PR_BGD, dtype=np.uint8)
        gc_mask[y0:y1 + 1, x0:x1 + 1] = cv2.GC_PR_FGD
        gc_mask[seed_mask > 0] = cv2.GC_FGD
        if x0 > 0:
            gc_mask[:, :max(0, x0 - pad // 2)] = cv2.GC_BGD
        if x1 < camera.width - 1:
            gc_mask[:, min(camera.width, x1 + pad // 2):] = cv2.GC_BGD
        if y0 > 0:
            gc_mask[:max(0, y0 - pad // 2), :] = cv2.GC_BGD
        if y1 < camera.height - 1:
            gc_mask[min(camera.height, y1 + pad // 2):, :] = cv2.GC_BGD

        bg_model = np.zeros((1, 65), np.float64)
        fg_model = np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(image_bgr, gc_mask, None, bg_model, fg_model, 2, cv2.GC_INIT_WITH_MASK)
        except Exception:
            continue

        object_mask = np.where(
            (gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD),
            255,
            0,
        ).astype(np.uint8)
        object_mask = cv2.morphologyEx(
            object_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_radius * 2 + 1, close_radius * 2 + 1)),
            iterations=1,
        )
        object_mask = keep_seeded_component(object_mask, seed_mask)
        object_mask[seed_mask > 0] = 255
        if int(np.count_nonzero(object_mask)) < 64:
            continue

        out_path = out_dir / f"{Path(camera.image_name).stem}.png"
        cv2.imwrite(str(out_path), object_mask)
        stats["generated"] = int(stats["generated"]) + 1

    if int(stats["generated"]) == 0:
        return None, stats
    stats["source"] = "auto_generated"
    return out_dir, stats


def project_point_with_depth(xyz: np.ndarray, camera: Camera) -> tuple[int, int, float] | None:
    r_w2c = camera.R.T
    t_w2c = -r_w2c @ camera.T
    xyz_cam = r_w2c @ xyz + t_w2c
    depth = float(xyz_cam[2])
    if depth <= 1e-6:
        return None
    uv_h = camera.K @ xyz_cam
    u = float(uv_h[0] / uv_h[2])
    v = float(uv_h[1] / uv_h[2])
    if u < 0.0 or v < 0.0 or u >= float(camera.width) or v >= float(camera.height):
        return None
    return int(u), int(v), depth


def semantic_whitelist_mask(
    xyz: np.ndarray,
    cameras: list[Camera],
    masks_dir: Path | None,
    *,
    min_views: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    if xyz.shape[0] == 0 or not cameras or masks_dir is None or not masks_dir.is_dir():
        return np.ones(xyz.shape[0], dtype=bool), np.zeros(xyz.shape[0], dtype=np.int32), 0

    view_counts = np.zeros(xyz.shape[0], dtype=np.int32)
    available_views = 0
    for camera in cameras:
        base_name = Path(camera.image_name).stem
        mask_path = find_matching_image(base_name, masks_dir)
        if mask_path is None:
            continue
        mask_rgb = load_rgb_image(mask_path)
        if mask_rgb.shape[0] != camera.height or mask_rgb.shape[1] != camera.width:
            if Image is None:
                continue
            resized = Image.fromarray((mask_rgb * 255.0).astype(np.uint8)).resize((camera.width, camera.height), Image.BILINEAR)
            mask_rgb = np.asarray(resized, dtype=np.float32) / 255.0
        object_mask = np.any(mask_rgb > 0.05, axis=2)
        available_views += 1
        for index, point in enumerate(xyz):
            projection = project_point_with_depth(point, camera)
            if projection is None:
                continue
            u, v, _ = projection
            if object_mask[v, u]:
                view_counts[index] += 1

    if available_views == 0:
        return np.ones(xyz.shape[0], dtype=bool), view_counts, 0
    whitelist = view_counts >= max(min_views, 1)
    return whitelist, view_counts, available_views


def semantic_color_validation_mask(
    xyz: np.ndarray,
    rgb_colors: np.ndarray,
    cameras: list[Camera],
    masks_dir: Path | None,
    images_dir: Path | None,
    whitelist_mask: np.ndarray,
    *,
    color_threshold: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    if (
        xyz.shape[0] == 0 or
        not cameras or
        masks_dir is None or
        not masks_dir.is_dir() or
        images_dir is None or
        not images_dir.is_dir()
    ):
        return np.ones(xyz.shape[0], dtype=bool), np.zeros(xyz.shape[0], dtype=np.int32), np.zeros(xyz.shape[0], dtype=np.int32), 0

    whitelisted_indices = np.flatnonzero(whitelist_mask)
    if whitelisted_indices.size == 0:
        return whitelist_mask.copy(), np.zeros(xyz.shape[0], dtype=np.int32), np.zeros(xyz.shape[0], dtype=np.int32), 0

    rendered_count = np.zeros(xyz.shape[0], dtype=np.int32)
    match_count = np.zeros(xyz.shape[0], dtype=np.int32)
    available_views = 0

    for camera in cameras:
        base_name = Path(camera.image_name).stem
        mask_path = find_matching_image(base_name, masks_dir)
        image_path = find_matching_image(base_name, images_dir)
        if mask_path is None or image_path is None:
            continue

        mask_rgb = load_rgb_image(mask_path)
        image_rgb = load_rgb_image(image_path)
        if mask_rgb.shape[:2] != (camera.height, camera.width):
            if Image is None:
                continue
            resized = Image.fromarray((mask_rgb * 255.0).astype(np.uint8)).resize((camera.width, camera.height), Image.BILINEAR)
            mask_rgb = np.asarray(resized, dtype=np.float32) / 255.0
        if image_rgb.shape[:2] != (camera.height, camera.width):
            if Image is None:
                continue
            resized = Image.fromarray((image_rgb * 255.0).astype(np.uint8)).resize((camera.width, camera.height), Image.BILINEAR)
            image_rgb = np.asarray(resized, dtype=np.float32) / 255.0

        object_mask = np.any(mask_rgb > 0.05, axis=2)
        depth_buffer = np.full((camera.height, camera.width), np.inf, dtype=np.float32)
        pixel_to_gaussian: dict[tuple[int, int], int] = {}
        for index in whitelisted_indices:
            projection = project_point_with_depth(xyz[index], camera)
            if projection is None:
                continue
            u, v, depth = projection
            if not object_mask[v, u]:
                continue
            if depth < depth_buffer[v, u]:
                depth_buffer[v, u] = depth
                pixel_to_gaussian[(u, v)] = int(index)

        if not pixel_to_gaussian:
            continue
        available_views += 1
        for (u, v), index in pixel_to_gaussian.items():
            rendered_count[index] += 1
            expected_color = image_rgb[v, u]
            color_distance = float(np.linalg.norm(rgb_colors[index] - expected_color))
            if color_distance <= color_threshold:
                match_count[index] += 1

    if available_views == 0:
        return whitelist_mask.copy(), rendered_count, match_count, 0

    has_renders = rendered_count > 0
    has_matches = match_count > 0
    keep_mask = whitelist_mask & ((~has_renders) | has_matches)
    return keep_mask, rendered_count, match_count, available_views


def statistical_outlier_mask(xyz: np.ndarray, *, nb_neighbors: int, std_ratio: float) -> np.ndarray:
    if xyz.shape[0] < max(nb_neighbors * 2, 64):
        return np.ones(xyz.shape[0], dtype=bool)
    point_cloud = o3d.geometry.PointCloud()
    point_cloud.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
    _, keep_indices = point_cloud.remove_statistical_outlier(
        nb_neighbors=nb_neighbors,
        std_ratio=std_ratio,
    )
    keep_mask = np.zeros(xyz.shape[0], dtype=bool)
    keep_mask[np.asarray(keep_indices, dtype=np.int64)] = True
    return keep_mask


def radius_outlier_mask(xyz: np.ndarray, *, nb_points: int, radius: float) -> np.ndarray:
    if xyz.shape[0] < max(nb_points * 2, 64):
        return np.ones(xyz.shape[0], dtype=bool)
    point_cloud = o3d.geometry.PointCloud()
    point_cloud.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
    _, keep_indices = point_cloud.remove_radius_outlier(
        nb_points=nb_points,
        radius=radius,
    )
    keep_mask = np.zeros(xyz.shape[0], dtype=bool)
    keep_mask[np.asarray(keep_indices, dtype=np.int64)] = True
    return keep_mask


def cluster_keep_mask(
    xyz: np.ndarray,
    *,
    eps: float,
    min_points: int,
    min_cluster_fraction: float,
) -> np.ndarray:
    if xyz.shape[0] < max(min_points * 2, 64):
        return np.ones(xyz.shape[0], dtype=bool)
    point_cloud = o3d.geometry.PointCloud()
    point_cloud.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
    labels = np.asarray(
        point_cloud.cluster_dbscan(eps=eps, min_points=min_points, print_progress=False),
        dtype=np.int32,
    )
    if labels.size == 0:
        return np.ones(xyz.shape[0], dtype=bool)
    clustered = labels[labels >= 0]
    if clustered.size == 0:
        return np.zeros(xyz.shape[0], dtype=bool)

    counts = np.bincount(clustered)
    min_cluster_size = max(int(math.ceil(xyz.shape[0] * min_cluster_fraction)), min_points * 2, 96)
    keep_labels = np.flatnonzero(counts >= min_cluster_size)
    if keep_labels.size == 0:
        keep_labels = np.array([int(np.argmax(counts))], dtype=np.int32)
    return np.isin(labels, keep_labels)


def dominant_cluster_mask(
    xyz: np.ndarray,
    *,
    eps: float,
    min_points: int,
    scene_span: float,
) -> tuple[np.ndarray, dict[str, int | float]]:
    if xyz.shape[0] < max(min_points * 2, 64):
        return np.ones(xyz.shape[0], dtype=bool), {
            "components": 1,
            "kept": int(xyz.shape[0]),
            "second": 0,
            "kept_components": 1,
            "largest": int(xyz.shape[0]),
            "min_significant": 0,
            "min_grounded": 0,
            "eps": float(eps),
        }

    point_cloud = o3d.geometry.PointCloud()
    point_cloud.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
    labels = np.asarray(
        point_cloud.cluster_dbscan(eps=eps, min_points=min_points, print_progress=False),
        dtype=np.int32,
    )
    clustered = labels[labels >= 0]
    if clustered.size == 0:
        return np.ones(xyz.shape[0], dtype=bool), {
            "components": 0,
            "kept": int(xyz.shape[0]),
            "second": 0,
            "kept_components": 1,
            "largest": int(xyz.shape[0]),
            "min_significant": 0,
            "min_grounded": 0,
            "eps": float(eps),
        }
    counts = np.bincount(clustered)
    dominant = int(np.argmax(counts))
    dominant_count = int(counts[dominant])
    second = int(np.partition(counts, -2)[-2]) if counts.size >= 2 else 0
    total = int(xyz.shape[0])
    dominant_xyz = xyz[labels == dominant]
    dominant_center = np.median(dominant_xyz, axis=0)
    dominant_xz = dominant_xyz[:, [0, 2]]
    dominant_radial = np.linalg.norm(
        dominant_xz - np.median(dominant_xz, axis=0, keepdims=True),
        axis=1,
    )
    dominant_radius = float(max(np.quantile(dominant_radial, 0.94) * 1.45, scene_span * 0.12, 0.18))
    overall_ground_y = float(np.quantile(xyz[:, 1], 0.16))
    ground_band = float(max(scene_span * 0.10, 0.12))
    min_significant = max(
        int(math.ceil(dominant_count * 0.08)),
        int(math.ceil(total * 0.025)),
        min_points * 4,
        128,
    )
    min_grounded = max(
        int(math.ceil(dominant_count * 0.035)),
        int(math.ceil(total * 0.010)),
        min_points * 3,
        96,
    )

    force_significant = max(
        int(math.ceil(dominant_count * 0.22)),
        min_significant * 2,
        192,
    )
    keep_labels: list[int] = []
    for label, count in enumerate(counts):
        component = labels == label
        component_xyz = xyz[component]
        component_center = np.median(component_xyz, axis=0)
        component_y15 = float(np.quantile(component_xyz[:, 1], 0.15))
        component_y35 = float(np.quantile(component_xyz[:, 1], 0.35))
        component_xz_dist = float(np.linalg.norm(component_center[[0, 2]] - dominant_center[[0, 2]]))
        component_is_significant = (
            int(count) >= min_significant and
            (
                component_xz_dist <= dominant_radius * 2.10 or
                int(count) >= force_significant
            )
        )
        component_is_grounded = (
            int(count) >= min_grounded and
            component_y15 <= overall_ground_y + ground_band
        )
        component_is_related = component_xz_dist <= dominant_radius * 1.85
        if label == dominant or component_is_significant or (component_is_grounded and component_is_related):
            keep_labels.append(int(label))

    keep = np.isin(labels, np.asarray(keep_labels, dtype=np.int32))
    if not np.any(keep):
        keep = np.ones(xyz.shape[0], dtype=bool)
    return keep, {
        "components": int(counts.size),
        "kept": int(np.count_nonzero(keep)),
        "second": second,
        "kept_components": int(len(keep_labels) if keep_labels else 1),
        "largest": dominant_count,
        "min_significant": int(min_significant),
        "force_significant": int(force_significant),
        "min_grounded": int(min_grounded),
        "eps": float(eps),
    }


def splat_quality_keep_mask(
    xyz: np.ndarray,
    scales: np.ndarray,
    opacities: np.ndarray,
    surface_supported: np.ndarray,
    surface_distances: np.ndarray,
    *,
    scene_span: float,
    median_scale: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    if xyz.shape[0] == 0:
        return np.zeros((0,), dtype=bool), {
            "kept": 0,
            "removed": 0,
            "preserved_bottom": 0,
            "preserved_surface": 0,
            "scale50": 0.0,
            "scale70": 0.0,
            "scale85": 0.0,
            "scale95": 0.0,
            "opacity10": 0.0,
            "opacity25": 0.0,
            "slab_low": 0.0,
            "slab_high": 0.0,
            "radius": 0.0,
            "voxel_size": 0.0,
            "support75": 0.0,
            "unsupported_removed": 0,
        }
    if xyz.shape[0] < 96:
        return np.ones(xyz.shape[0], dtype=bool), {
            "kept": int(xyz.shape[0]),
            "removed": 0,
            "preserved_bottom": 0,
            "preserved_surface": 0,
            "scale50": float(np.quantile(scales.mean(axis=1), 0.50)),
            "scale70": float(np.quantile(scales.mean(axis=1), 0.70)),
            "scale85": float(np.quantile(scales.mean(axis=1), 0.85)),
            "scale95": float(np.quantile(scales.mean(axis=1), 0.95)),
            "opacity10": float(np.quantile(opacities, 0.10)),
            "opacity25": float(np.quantile(opacities, 0.25)),
            "slab_low": float(np.quantile(xyz[:, 1], 0.10)),
            "slab_high": float(np.quantile(xyz[:, 1], 0.35)),
            "radius": float(max(scene_span * 0.08, 0.08)),
            "voxel_size": float(max(scene_span * 0.025, median_scale * 10.0, 0.04)),
            "support75": 0.0,
            "unsupported_removed": 0,
        }

    mean_scales = scales.mean(axis=1)
    max_scales = scales.max(axis=1)
    min_scales = np.maximum(scales.min(axis=1), 1e-6)
    anisotropy = max_scales / min_scales

    scale50 = float(np.quantile(mean_scales, 0.50))
    scale70 = float(np.quantile(mean_scales, 0.70))
    scale85 = float(np.quantile(mean_scales, 0.85))
    scale95 = float(np.quantile(mean_scales, 0.95))
    scale98 = float(np.quantile(mean_scales, 0.98))
    opacity10 = float(np.quantile(opacities, 0.10))
    opacity25 = float(np.quantile(opacities, 0.25))
    opacity05 = float(np.quantile(opacities, 0.05))

    center = np.median(xyz, axis=0)
    radial = np.linalg.norm(xyz[:, [0, 2]] - center[[0, 2]].reshape(1, 2), axis=1)
    footprint_radius = float(max(np.quantile(radial, 0.92) * 1.35, scene_span * 0.08, median_scale * 12.0, 0.08))
    y10 = float(np.quantile(xyz[:, 1], 0.10))
    y20 = float(np.quantile(xyz[:, 1], 0.20))
    y35 = float(np.quantile(xyz[:, 1], 0.35))
    slab_low = float(y10 - max(scene_span * 0.05, median_scale * 10.0, 0.05))
    slab_high = float(y20 + max(scene_span * 0.025, median_scale * 6.0, 0.03))

    voxel_size = float(max(scene_span * 0.025, median_scale * 10.0, 0.04))
    voxel_coords = np.floor(xyz / voxel_size).astype(np.int32)
    unique_voxels, inverse, counts = np.unique(voxel_coords, axis=0, return_inverse=True, return_counts=True)
    voxel_index = {tuple(coord.tolist()): idx for idx, coord in enumerate(unique_voxels)}
    voxel_support = np.zeros(unique_voxels.shape[0], dtype=np.int32)
    for idx, coord in enumerate(unique_voxels):
        support = 0
        cx, cy, cz = int(coord[0]), int(coord[1]), int(coord[2])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    neighbor = voxel_index.get((cx + dx, cy + dy, cz + dz))
                    if neighbor is not None:
                        support += int(counts[neighbor])
        voxel_support[idx] = support
    local_support = voxel_support[inverse]
    support75 = float(np.quantile(voxel_support.astype(np.float32), 0.75)) if voxel_support.size else 0.0
    support_floor = max(8.0, support75 * 0.30)
    surface_far_floor = float(max(scene_span * 0.030, median_scale * 12.0, 0.07))

    dx_fp = xyz[:, 0] - center[0]
    dz_fp = xyz[:, 2] - center[2]
    in_footprint = np.sqrt(dx_fp * dx_fp + dz_fp * dz_fp) <= footprint_radius
    in_surface_fill_footprint = np.sqrt(dx_fp * dx_fp + dz_fp * dz_fp) <= footprint_radius * 1.75
    in_bottom_slab = (xyz[:, 1] >= slab_low) & (xyz[:, 1] <= slab_high)
    bottom_support_candidate = (
        in_footprint &
        in_bottom_slab &
        (local_support >= support_floor) &
        (mean_scales <= scale95 * 2.2) &
        (anisotropy <= 4.8) &
        (opacities >= max(0.008, opacity10 * 0.55))
    )
    surface_fill_candidate = (
        surface_supported &
        in_surface_fill_footprint &
        (local_support >= max(5.0, support75 * 0.18)) &
        (surface_distances <= surface_far_floor * 1.45) &
        (mean_scales <= scale98 * 2.10) &
        (anisotropy <= 7.0) &
        (opacities >= max(0.004, opacity05 * 0.18))
    )
    surface_anchor_candidate = (
        surface_supported &
        in_surface_fill_footprint &
        (surface_distances <= surface_far_floor * 0.70) &
        (mean_scales <= scale98 * 2.55) &
        (anisotropy <= 8.0) &
        (opacities >= max(0.006, opacity10 * 0.22))
    )

    huge_and_weak = (mean_scales > scale95 * 1.00) & (opacities < 0.90)
    big_and_soft = (mean_scales > scale85 * 1.08) & (opacities < 0.76)
    medium_big_and_soft = (mean_scales > scale70 * 1.12) & (opacities < 0.62)
    elongated_and_weak = (anisotropy > 3.2) & (mean_scales > scale50 * 0.98) & (opacities < 0.92)
    very_large_sheet = (mean_scales > scale50 * 1.35) & (anisotropy > 1.9) & (opacities < 0.98)
    giant_cap = (max_scales > scale95 * 1.20) & (opacities < 0.99)
    low_alpha_blob = (mean_scales > scale50 * 1.05) & (opacities < 0.48)
    low_alpha_noise = opacities < max(0.015, opacity10 * 0.65)
    low_alpha_medium = (opacities < max(0.045, opacity25 * 0.80)) & (mean_scales > scale50 * 0.95)
    anisotropic_medium = (anisotropy > 2.6) & (mean_scales > scale70 * 0.95) & (opacities < 0.96)
    low_support_noise = (local_support < max(8.0, support75 * 0.35)) & (opacities < max(0.06, opacity25 * 0.85))
    surface_far_and_weak = (
        (~surface_supported | (surface_distances > surface_far_floor)) &
        (local_support < max(14.0, support75 * 0.55)) &
        (opacities < max(0.14, opacity25 * 1.10))
    )

    should_cull = (
        huge_and_weak | big_and_soft | medium_big_and_soft |
        elongated_and_weak | very_large_sheet | giant_cap |
        low_alpha_blob | low_alpha_noise | low_alpha_medium |
        anisotropic_medium | low_support_noise | surface_far_and_weak
    )
    preserve_bottom = (
        bottom_support_candidate &
        ~(giant_cap & (opacities < 0.15)) &
        ~(very_large_sheet & (opacities < 0.12))
    )
    preserve_surface = (
        (surface_fill_candidate | surface_anchor_candidate) &
        ~(giant_cap & (opacities < 0.05)) &
        ~(very_large_sheet & (opacities < 0.04))
    )
    keep = ~should_cull | preserve_bottom | preserve_surface
    if np.count_nonzero(keep) < max(64, xyz.shape[0] // 5):
        keep = ~(
            huge_and_weak | big_and_soft | low_alpha_blob |
            low_alpha_noise | low_support_noise | surface_far_and_weak
        ) | preserve_bottom | preserve_surface
    if not np.any(keep):
        keep = np.ones(xyz.shape[0], dtype=bool)

    return keep, {
        "kept": int(np.count_nonzero(keep)),
        "removed": int(xyz.shape[0] - np.count_nonzero(keep)),
        "preserved_bottom": int(np.count_nonzero(preserve_bottom)),
        "preserved_surface": int(np.count_nonzero(preserve_surface)),
        "scale50": scale50,
        "scale70": scale70,
        "scale85": scale85,
        "scale95": scale95,
        "opacity10": opacity10,
        "opacity25": opacity25,
        "slab_low": slab_low,
        "slab_high": slab_high,
        "radius": footprint_radius,
        "voxel_size": voxel_size,
        "support75": support75,
        "unsupported_removed": int(np.count_nonzero(surface_far_and_weak & ~preserve_bottom & ~preserve_surface)),
    }


def recover_surface_fill_mask(
    xyz: np.ndarray,
    dominant_mask: np.ndarray,
    surface_keep: np.ndarray,
    max_scales: np.ndarray,
    opacities: np.ndarray,
    *,
    scene_span: float,
    median_scale: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    if xyz.shape[0] == 0 or not np.any(dominant_mask):
        return np.zeros(xyz.shape[0], dtype=bool), {
            "recovered": 0,
            "band_low": 0.0,
            "band_high": 0.0,
            "radius": 0.0,
            "opacity_floor": 0.0,
            "scale_limit": 0.0,
        }

    dominant_seed = dominant_mask & surface_keep
    if np.count_nonzero(dominant_seed) >= max(64, int(np.count_nonzero(dominant_mask) * 0.18)):
        dominant_xyz = xyz[dominant_seed]
        dominant_scales = max_scales[dominant_seed]
        dominant_opacity = opacities[dominant_seed]
    else:
        dominant_xyz = xyz[dominant_mask]
        dominant_scales = max_scales[dominant_mask]
        dominant_opacity = opacities[dominant_mask]

    dominant_center = np.median(dominant_xyz, axis=0)
    dominant_xz = dominant_xyz[:, [0, 2]]
    radial = np.linalg.norm(dominant_xz - np.median(dominant_xz, axis=0, keepdims=True), axis=1)
    footprint_radius = float(max(np.quantile(radial, 0.97) * 1.70, scene_span * 0.14, median_scale * 16.0, 0.14))
    opacity_floor = float(max(np.quantile(dominant_opacity, 0.03) * 0.12, 0.004))
    scale_limit = float(max(np.quantile(dominant_scales, 0.97) * 2.35, median_scale * 6.0, 0.024))
    proximity_limit = float(max(np.quantile(dominant_scales, 0.96) * 14.0, scene_span * 0.09, median_scale * 16.0, 0.12))

    dx = xyz[:, 0] - dominant_center[0]
    dz = xyz[:, 2] - dominant_center[2]
    in_footprint = np.sqrt(dx * dx + dz * dz) <= footprint_radius
    dominant_proximity = np.full(xyz.shape[0], np.inf, dtype=np.float32)
    if dominant_xyz.shape[0] > 0:
        source = o3d.geometry.PointCloud()
        target = o3d.geometry.PointCloud()
        source.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
        target.points = o3d.utility.Vector3dVector(dominant_xyz.astype(np.float64))
        dominant_proximity = np.asarray(source.compute_point_cloud_distance(target), dtype=np.float32)
    recovered = (
        surface_keep &
        in_footprint &
        (dominant_proximity <= proximity_limit) &
        (max_scales <= scale_limit) &
        (opacities >= opacity_floor)
    )
    recovered &= ~dominant_mask
    return recovered, {
        "recovered": int(np.count_nonzero(recovered)),
        "band_low": 0.0,
        "band_high": 0.0,
        "radius": footprint_radius,
        "opacity_floor": opacity_floor,
        "scale_limit": scale_limit,
        "proximity_limit": proximity_limit,
    }


def recover_body_fill_mask(
    xyz: np.ndarray,
    dominant_mask: np.ndarray,
    max_scales: np.ndarray,
    opacities: np.ndarray,
    *,
    scene_span: float,
    median_scale: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    if xyz.shape[0] == 0 or not np.any(dominant_mask):
        return np.zeros(xyz.shape[0], dtype=bool), {
            "recovered": 0,
            "band_low": 0.0,
            "band_high": 0.0,
            "radius": 0.0,
            "opacity_floor": 0.0,
            "scale_limit": 0.0,
            "support75": 0.0,
        }

    dominant_xyz = xyz[dominant_mask]
    dominant_scales = max_scales[dominant_mask]
    dominant_opacity = opacities[dominant_mask]

    dominant_center = np.median(dominant_xyz, axis=0)
    dominant_xz = dominant_xyz[:, [0, 2]]
    radial = np.linalg.norm(dominant_xz - np.median(dominant_xz, axis=0, keepdims=True), axis=1)
    footprint_radius = float(max(np.quantile(radial, 0.98) * 1.90, scene_span * 0.16, median_scale * 18.0, 0.16))
    opacity_floor = float(max(np.quantile(dominant_opacity, 0.02) * 0.08, 0.003))
    scale_limit = float(max(np.quantile(dominant_scales, 0.98) * 2.70, median_scale * 7.5, 0.03))
    proximity_limit = float(max(np.quantile(dominant_scales, 0.98) * 18.0, scene_span * 0.14, median_scale * 22.0, 0.16))

    voxel_size = float(max(scene_span * 0.030, median_scale * 12.0, 0.05))
    voxel_coords = np.floor(xyz / voxel_size).astype(np.int32)
    unique_voxels, inverse, counts = np.unique(voxel_coords, axis=0, return_inverse=True, return_counts=True)
    voxel_index = {tuple(coord.tolist()): idx for idx, coord in enumerate(unique_voxels)}
    voxel_support = np.zeros(unique_voxels.shape[0], dtype=np.int32)
    for idx, coord in enumerate(unique_voxels):
        support = 0
        cx, cy, cz = int(coord[0]), int(coord[1]), int(coord[2])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    neighbor = voxel_index.get((cx + dx, cy + dy, cz + dz))
                    if neighbor is not None:
                        support += int(counts[neighbor])
        voxel_support[idx] = support
    local_support = voxel_support[inverse]
    support75 = float(np.quantile(voxel_support.astype(np.float32), 0.75)) if voxel_support.size else 0.0

    dx = xyz[:, 0] - dominant_center[0]
    dz = xyz[:, 2] - dominant_center[2]
    in_footprint = np.sqrt(dx * dx + dz * dz) <= footprint_radius
    dominant_proximity = np.full(xyz.shape[0], np.inf, dtype=np.float32)
    if dominant_xyz.shape[0] > 0:
        source = o3d.geometry.PointCloud()
        target = o3d.geometry.PointCloud()
        source.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
        target.points = o3d.utility.Vector3dVector(dominant_xyz.astype(np.float64))
        dominant_proximity = np.asarray(source.compute_point_cloud_distance(target), dtype=np.float32)
    recovered = (
        in_footprint &
        (dominant_proximity <= proximity_limit) &
        (local_support >= max(4.0, support75 * 0.12)) &
        (max_scales <= scale_limit) &
        (opacities >= opacity_floor)
    )
    recovered &= ~dominant_mask
    return recovered, {
        "recovered": int(np.count_nonzero(recovered)),
        "band_low": 0.0,
        "band_high": 0.0,
        "radius": footprint_radius,
        "opacity_floor": opacity_floor,
        "scale_limit": scale_limit,
        "support75": support75,
        "proximity_limit": proximity_limit,
    }


def recover_ground_support_mask(
    xyz: np.ndarray,
    dominant_mask: np.ndarray,
    surface_keep: np.ndarray,
    max_scales: np.ndarray,
    opacities: np.ndarray,
    *,
    scene_span: float,
    median_scale: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    if xyz.shape[0] == 0 or not np.any(dominant_mask):
        return np.zeros(xyz.shape[0], dtype=bool), {
            "recovered": 0,
            "y10": 0.0,
            "y20": 0.0,
            "y35": 0.0,
            "slab_low": 0.0,
            "slab_high": 0.0,
            "radius": 0.0,
            "columns": 0,
        }

    dominant_seed = dominant_mask & surface_keep
    if np.count_nonzero(dominant_seed) >= max(48, int(np.count_nonzero(dominant_mask) * 0.15)):
        dominant_xyz = xyz[dominant_seed]
        dominant_scales = max_scales[dominant_seed]
    else:
        dominant_xyz = xyz[dominant_mask]
        dominant_scales = max_scales[dominant_mask]
    dominant_center = np.median(dominant_xyz, axis=0)
    dominant_xz = dominant_xyz[:, [0, 2]]
    radial = np.linalg.norm(dominant_xz - np.median(dominant_xz, axis=0, keepdims=True), axis=1)
    footprint_radius = float(max(np.quantile(radial, 0.92) * 1.35, scene_span * 0.08, median_scale * 12.0, 0.08))

    y10 = float(np.quantile(dominant_xyz[:, 1], 0.10))
    y20 = float(np.quantile(dominant_xyz[:, 1], 0.20))
    y35 = float(np.quantile(dominant_xyz[:, 1], 0.35))
    slab_low = float(y10 - max(scene_span * 0.04, median_scale * 10.0, 0.05))
    slab_high = float(y20 + max(scene_span * 0.025, median_scale * 6.0, 0.03))

    footprint_delta = xyz[:, [0, 2]] - dominant_center[[0, 2]]
    in_footprint = np.linalg.norm(footprint_delta, axis=1) <= footprint_radius
    in_slab = (xyz[:, 1] >= slab_low) & (xyz[:, 1] <= slab_high)
    compact_scale_limit = float(max(np.quantile(dominant_scales, 0.80) * 1.65, median_scale * 2.8, 0.012))
    grounded = (
        surface_keep &
        in_footprint &
        in_slab &
        (max_scales <= compact_scale_limit) &
        (opacities >= max(float(np.quantile(opacities[dominant_mask], 0.10)), 0.02))
    )

    columns = int(np.count_nonzero(grounded))
    recovered = grounded & ~dominant_mask
    return recovered, {
        "recovered": int(np.count_nonzero(recovered)),
        "y10": y10,
        "y20": y20,
        "y35": y35,
        "slab_low": slab_low,
        "slab_high": slab_high,
        "radius": footprint_radius,
        "columns": columns,
    }


def load_surface_points(path: Path) -> np.ndarray:
    if not path.is_file():
        return np.empty((0, 3), dtype=np.float32)

    if path.suffix.lower() == ".ply":
        mesh = o3d.io.read_triangle_mesh(str(path))
        if mesh.has_vertices():
            points = np.asarray(mesh.vertices, dtype=np.float32)
        else:
            point_cloud = o3d.io.read_point_cloud(str(path))
            points = np.asarray(point_cloud.points, dtype=np.float32)
    else:
        point_cloud = o3d.io.read_point_cloud(str(path))
        points = np.asarray(point_cloud.points, dtype=np.float32)

    if points.ndim != 2 or points.shape[1] != 3:
        return np.empty((0, 3), dtype=np.float32)
    if points.shape[0] > 250_000:
        stride = int(math.ceil(points.shape[0] / 250_000))
        points = points[::stride]
    return points


def surface_support_mask(
    xyz: np.ndarray,
    surface_points: np.ndarray,
    *,
    max_distance: float,
) -> tuple[np.ndarray, np.ndarray]:
    if xyz.shape[0] == 0 or surface_points.shape[0] == 0:
        return np.ones(xyz.shape[0], dtype=bool), np.zeros(xyz.shape[0], dtype=np.float32)
    source = o3d.geometry.PointCloud()
    target = o3d.geometry.PointCloud()
    source.points = o3d.utility.Vector3dVector(xyz.astype(np.float64))
    target.points = o3d.utility.Vector3dVector(surface_points.astype(np.float64))
    distances = np.asarray(source.compute_point_cloud_distance(target), dtype=np.float32)
    return distances <= max_distance, distances


def main() -> None:
    parser = argparse.ArgumentParser(description="Conservative cleanup for 3DGS PLY output.")
    parser.add_argument("--ply", required=True, help="Path to 3dgs_final.ply")
    parser.add_argument("--summary-json", required=True, help="Where to write cleanup summary JSON")
    parser.add_argument("--surface-ply", default="", help="Optional TSDF mesh / support surface for whitelist-like cleanup")
    parser.add_argument("--backup-suffix", default="_uncleaned", help="Suffix for the preserved raw backup")
    parser.add_argument("--images-dir", default="", help="Directory with original RGB images for Clean-GS color validation")
    parser.add_argument("--masks-dir", default="", help="Directory with sparse semantic masks for Clean-GS whitelist filtering")
    parser.add_argument("--cameras-json", default="", help="Camera metadata JSON with image_name/width/height/R/T/K")
    parser.add_argument("--semantic-min-views", type=int, default=2)
    parser.add_argument("--semantic-color-threshold", type=float, default=0.35)
    parser.add_argument("--min-opacity", type=float, default=0.02)
    parser.add_argument("--outlier-neighbors", type=int, default=24)
    parser.add_argument("--outlier-std-ratio", type=float, default=3.0)
    parser.add_argument("--radius-outlier-points", type=int, default=12)
    parser.add_argument("--min-cluster-fraction", type=float, default=0.006)
    args = parser.parse_args()

    ply_path = Path(args.ply)
    summary_path = Path(args.summary_json)
    surface_path = Path(args.surface_ply) if args.surface_ply else None
    images_dir = Path(args.images_dir) if args.images_dir else None
    masks_dir = Path(args.masks_dir) if args.masks_dir else None
    cameras_path = Path(args.cameras_json) if args.cameras_json else None
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, object] = {
        "ply_path": str(ply_path),
        "applied": False,
        "backup_path": None,
        "reason": None,
        "thresholds": {
            "min_opacity": float(args.min_opacity),
            "outlier_neighbors": int(args.outlier_neighbors),
            "outlier_std_ratio": float(args.outlier_std_ratio),
            "radius_outlier_points": int(args.radius_outlier_points),
            "min_cluster_fraction": float(args.min_cluster_fraction),
            "semantic_min_views": int(args.semantic_min_views),
            "semantic_color_threshold": float(args.semantic_color_threshold),
        },
    }

    if not ply_path.is_file():
        payload["reason"] = "missing_ply"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    ply, vertex = load_vertex(ply_path)
    names = field_names(vertex)
    required = {"x", "y", "z", "opacity"}
    scale_names = sorted(name for name in names if name.startswith("scale_"))
    if not required.issubset(names) or len(scale_names) < 3:
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
    rgb_colors = None
    if {"f_dc_0", "f_dc_1", "f_dc_2"}.issubset(names):
        dc0 = np.asarray(vertex["f_dc_0"], dtype=np.float32)[valid_mask]
        dc1 = np.asarray(vertex["f_dc_1"], dtype=np.float32)[valid_mask]
        dc2 = np.asarray(vertex["f_dc_2"], dtype=np.float32)[valid_mask]
        c0 = 0.28209479177387814
        rgb_colors = np.clip(np.stack([dc0, dc1, dc2], axis=1) * c0 + 0.5, 0.0, 1.0)
    median_scale = float(np.median(max_scales)) if max_scales.size else 0.0

    scene_span = float(np.linalg.norm(filtered_xyz.max(axis=0) - filtered_xyz.min(axis=0))) if filtered_xyz.size else 0.0
    giant_threshold = percentile_threshold(max_scales, scene_span=scene_span)
    surface_points = load_surface_points(surface_path) if surface_path else np.empty((0, 3), dtype=np.float32)

    keep_valid_mask = filtered_opacity >= float(args.min_opacity)
    keep_valid_mask &= max_scales <= giant_threshold

    semantic_stats: dict[str, object] = {
        "enabled": False,
        "available_views": 0,
        "whitelist_removed": 0,
        "color_removed": 0,
        "source": "none",
        "generated_masks": 0,
    }
    cameras = load_cameras_json(cameras_path)
    auto_mask_stats: dict[str, object] = {"generated": 0, "available_views": 0, "source": "none"}
    if (masks_dir is None or not masks_dir.is_dir()) and cameras:
        generated_masks_dir, auto_mask_stats = auto_generate_masks_dir(
            cameras,
            images_dir,
            surface_points,
            summary_path.parent / f"{ply_path.stem}_semantic_masks",
            scene_span=scene_span,
        )
        if generated_masks_dir is not None:
            masks_dir = generated_masks_dir

    if rgb_colors is not None and cameras and masks_dir is not None and masks_dir.is_dir():
        semantic_stats["enabled"] = True
        semantic_stats["source"] = (
            str(auto_mask_stats.get("source"))
            if str(auto_mask_stats.get("source") or "") != "none"
            else "provided"
        )
        semantic_stats["generated_masks"] = int(auto_mask_stats.get("generated") or 0)
        semantic_whitelist, view_counts, whitelist_views = semantic_whitelist_mask(
            filtered_xyz[keep_valid_mask],
            cameras,
            masks_dir,
            min_views=int(args.semantic_min_views),
        )
        semantic_stats["available_views"] = int(whitelist_views)
        semantic_stats["whitelist_removed"] = int(np.count_nonzero(keep_valid_mask) - np.count_nonzero(semantic_whitelist))
        if whitelist_views > 0:
            semantic_color_keep, rendered_count, match_count, color_views = semantic_color_validation_mask(
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
            keep_valid_indices = np.flatnonzero(keep_valid_mask)
            updated_keep = np.zeros_like(keep_valid_mask, dtype=bool)
            updated_keep[keep_valid_indices[semantic_color_keep]] = True
            keep_valid_mask = updated_keep

    kept_xyz_pre_outlier = filtered_xyz[keep_valid_mask]
    kept_scales_pre_outlier = max_scales[keep_valid_mask]
    kept_opacity_pre_outlier = filtered_opacity[keep_valid_mask]
    surface_distance_threshold = max(scene_span * 0.032, median_scale * 13.0, 0.08)
    surface_keep, surface_distances = surface_support_mask(
        kept_xyz_pre_outlier,
        surface_points,
        max_distance=surface_distance_threshold,
    )

    surface_kept_xyz = kept_xyz_pre_outlier[surface_keep]
    surface_kept_scales = kept_scales_pre_outlier[surface_keep]
    surface_kept_opacity = kept_opacity_pre_outlier[surface_keep]
    radius_threshold = max(scene_span * 0.012, float(np.median(surface_kept_scales)) * 6.0 if surface_kept_scales.size else median_scale * 6.0, 0.04)
    radius_keep = radius_outlier_mask(
        surface_kept_xyz,
        nb_points=int(args.radius_outlier_points),
        radius=radius_threshold,
    )

    radius_kept_xyz = surface_kept_xyz[radius_keep]
    cluster_eps = max(scene_span * 0.018, float(np.median(surface_kept_scales[radius_keep])) * 10.0 if np.count_nonzero(radius_keep) else median_scale * 10.0, 0.06)
    cluster_keep = cluster_keep_mask(
        radius_kept_xyz,
        eps=cluster_eps,
        min_points=max(int(args.radius_outlier_points), 10),
        min_cluster_fraction=float(args.min_cluster_fraction),
    )

    dominant_keep, dominant_stats = dominant_cluster_mask(
        radius_kept_xyz[cluster_keep],
        eps=max(cluster_eps * 0.90, 0.05),
        min_points=max(int(args.radius_outlier_points), 10),
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
        nb_neighbors=int(args.outlier_neighbors),
        std_ratio=float(args.outlier_std_ratio),
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
            kept_scales_pre_outlier,
            kept_opacity_pre_outlier,
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
            kept_scales_pre_outlier,
            kept_opacity_pre_outlier,
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
            kept_scales_pre_outlier,
            kept_opacity_pre_outlier,
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

    removed_invalid = int(np.count_nonzero(~valid_mask))
    removed_low_opacity = int(np.count_nonzero(valid_mask) - np.count_nonzero(keep_valid_mask))
    removed_surface_far = int(np.count_nonzero(keep_valid_mask) - np.count_nonzero(surface_keep))
    removed_radius = int(np.count_nonzero(surface_keep) - np.count_nonzero(radius_keep))
    removed_cluster = int(np.count_nonzero(radius_keep) - np.count_nonzero(cluster_keep))
    removed_outlier = int(np.count_nonzero(cluster_keep) - np.count_nonzero(dominant_keep) + np.count_nonzero(dominant_keep) - np.count_nonzero(outlier_keep))
    original_count = int(vertex.shape[0])
    kept_count = int(np.count_nonzero(final_mask))

    backup_path = ply_path.with_name(f"{ply_path.stem}{args.backup_suffix}{ply_path.suffix}")
    if not backup_path.exists():
        shutil.copy2(ply_path, backup_path)

    if kept_count > 0 and kept_count < original_count:
        write_vertex_like(vertex, final_mask, ply_path)
        payload["applied"] = True
    else:
        payload["reason"] = "no_safe_reduction" if kept_count >= original_count else "all_points_removed_guarded"

    payload.update(
        {
            "backup_path": str(backup_path),
            "counts": {
                "original": original_count,
                "kept": kept_count,
                "removed_invalid": removed_invalid,
                "removed_low_opacity_or_giant": removed_low_opacity,
                "removed_surface_far": removed_surface_far,
                "removed_radius_outlier": removed_radius,
                "removed_small_cluster": removed_cluster,
                "removed_outlier": removed_outlier,
            },
            "thresholds": {
                **payload["thresholds"],
                "giant_scale_threshold": giant_threshold,
                "scene_span": scene_span,
                "surface_distance_threshold": surface_distance_threshold,
                "radius_outlier_radius": radius_threshold,
                "cluster_eps": cluster_eps,
                "median_scale": median_scale,
            },
            "surface_support": {
                "surface_path": str(surface_path) if surface_path else None,
                "surface_points": int(surface_points.shape[0]),
            },
            "semantic_cleanup": semantic_stats,
            "auto_masks": auto_mask_stats,
            "dominant_cluster": dominant_stats,
            "surface_fill_recovery": fill_stats,
            "body_fill_recovery": body_stats,
            "ground_recovery": ground_stats,
            "splat_quality": splat_stats,
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
