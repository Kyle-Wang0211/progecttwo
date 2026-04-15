#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

try:
    from scipy.spatial import cKDTree
except Exception as exc:  # pragma: no cover
    raise RuntimeError("scipy_required_for_sheetness_hotspot_analysis") from exc


@dataclass(frozen=True)
class PairSample:
    face_i: int
    face_j: int
    midpoint: np.ndarray
    normal_cosine: float
    plane_gap_ratio: float
    centroid_distance_ratio: float
    area_ratio_i: float
    area_ratio_j: float
    boundary_i: bool
    boundary_j: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cluster self-intersection hotspot samples to localize sheetness failures.",
    )
    parser.add_argument(
        "--mesh",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="Mesh label and path. Repeat for multiple meshes.",
    )
    parser.add_argument("--sample-cap", type=int, default=4096, help="Face sample cap per mesh.")
    parser.add_argument(
        "--cluster-radius-ratio",
        type=float,
        default=0.035,
        help="Cluster midpoint radius as a fraction of mesh diagonal.",
    )
    parser.add_argument(
        "--match-radius",
        type=float,
        default=0.10,
        help="Normalized centroid distance threshold when matching hotspots across meshes.",
    )
    parser.add_argument("--top-k", type=int, default=8, help="Number of top clusters to keep per mesh.")
    parser.add_argument("--output-json", type=Path, default=None, help="Optional JSON output path.")
    return parser.parse_args()


def parse_mesh_specs(specs: list[str]) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"invalid_mesh_spec:{spec}")
        label, raw_path = spec.split("=", 1)
        label = label.strip()
        path = Path(raw_path.strip())
        if not label:
            raise ValueError(f"invalid_mesh_label:{spec}")
        parsed.append((label, path))
    return parsed


def mesh_extents(mesh: trimesh.Trimesh) -> np.ndarray:
    bounds = np.asarray(mesh.bounds, dtype=np.float64)
    return np.maximum(bounds[1] - bounds[0], 1e-6)


def sample_indices(total: int, cap: int) -> np.ndarray:
    if total <= cap:
        return np.arange(total, dtype=np.int64)
    return np.linspace(0, total - 1, num=cap, dtype=np.int64)


def aabb_overlap(
    a_min: np.ndarray,
    a_max: np.ndarray,
    b_min: np.ndarray,
    b_max: np.ndarray,
    *,
    pad: float,
) -> bool:
    return bool(np.all((a_min - pad) <= (b_max + pad)) and np.all((b_min - pad) <= (a_max + pad)))


def point_near_triangle(point: np.ndarray, triangle: np.ndarray, plane_tolerance: float) -> bool:
    a, b, c = triangle
    normal = np.cross(b - a, c - a)
    normal_norm = float(np.linalg.norm(normal))
    if normal_norm <= 1e-10:
        return False
    unit = normal / normal_norm
    signed_distance = float(np.dot(point - a, unit))
    if abs(signed_distance) > plane_tolerance:
        return False
    projected = point - signed_distance * unit
    edge_ab = np.dot(np.cross(b - a, projected - a), unit)
    edge_bc = np.dot(np.cross(c - b, projected - b), unit)
    edge_ca = np.dot(np.cross(a - c, projected - c), unit)
    tolerance = max(plane_tolerance, 1e-8)
    return bool(edge_ab >= -tolerance and edge_bc >= -tolerance and edge_ca >= -tolerance)


def boundary_face_mask(faces: np.ndarray, *, vertex_count: int) -> np.ndarray:
    if len(faces) == 0:
        return np.zeros(0, dtype=bool)
    edges = np.vstack(
        [
            faces[:, [0, 1]],
            faces[:, [1, 2]],
            faces[:, [2, 0]],
        ]
    )
    edges = np.sort(edges, axis=1)
    key_dtype = np.dtype([("u", "<i8"), ("v", "<i8")])
    keys = np.empty(len(edges), dtype=key_dtype)
    keys["u"] = edges[:, 0]
    keys["v"] = edges[:, 1]
    unique_keys, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    del unique_keys
    boundary_edges = counts[inverse] == 1
    boundary_vertices = np.zeros(vertex_count, dtype=bool)
    boundary_vertices[edges[boundary_edges, 0]] = True
    boundary_vertices[edges[boundary_edges, 1]] = True
    return np.any(boundary_vertices[faces], axis=1)


def area_ratios(mesh: trimesh.Trimesh, diagonal: float) -> np.ndarray:
    raw_areas = np.asarray(getattr(mesh, "area_faces", np.zeros(len(mesh.faces))), dtype=np.float64)
    normalizer = max(diagonal * diagonal, 1e-8)
    return raw_areas / normalizer


def collect_pair_samples(
    mesh: trimesh.Trimesh,
    *,
    sample_cap: int,
) -> dict[str, object]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if len(faces) < 2:
        return {
            "diagonal": 0.0,
            "pairs": [],
            "sample_face_count": int(len(faces)),
            "sample_indices": [],
        }

    extents = mesh_extents(mesh)
    diagonal = max(float(np.linalg.norm(extents)), 1e-6)
    sample_face_indices = sample_indices(len(faces), sample_cap)
    sampled_faces = faces[sample_face_indices]
    triangles = vertices[sampled_faces]
    centroids = triangles.mean(axis=1)
    aabb_min = triangles.min(axis=1)
    aabb_max = triangles.max(axis=1)
    normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    normal_norms = np.linalg.norm(normals, axis=1)
    valid_normals = normal_norms > 1e-8
    normals[valid_normals] /= normal_norms[valid_normals][:, None]
    plane_tolerance = max(diagonal * 0.0015, 1e-5)
    radii = np.max(np.linalg.norm(triangles - centroids[:, None, :], axis=2), axis=1)
    query_radius = max(float(np.median(radii) * 2.5), diagonal * 0.03)
    tree = cKDTree(centroids)
    neighborhoods = tree.query_ball_point(centroids, query_radius, workers=1)
    full_boundary = boundary_face_mask(faces, vertex_count=len(vertices))
    sampled_boundary = full_boundary[sample_face_indices]
    sampled_area_ratios = area_ratios(mesh, diagonal)[sample_face_indices]

    pair_samples: list[PairSample] = []
    for local_i, candidates in enumerate(neighborhoods):
        if not valid_normals[local_i]:
            continue
        face_i = sampled_faces[local_i]
        tri_i = triangles[local_i]
        centroid_i = centroids[local_i]
        normal_i = normals[local_i]
        for local_j in candidates:
            if local_j <= local_i or not valid_normals[local_j]:
                continue
            face_j = sampled_faces[local_j]
            if np.intersect1d(face_i, face_j).size > 0:
                continue
            if not aabb_overlap(
                aabb_min[local_i],
                aabb_max[local_i],
                aabb_min[local_j],
                aabb_max[local_j],
                pad=plane_tolerance,
            ):
                continue
            tri_j = triangles[local_j]
            centroid_j = centroids[local_j]
            if not (
                point_near_triangle(centroid_i, tri_j, plane_tolerance)
                or point_near_triangle(centroid_j, tri_i, plane_tolerance)
            ):
                continue
            normal_j = normals[local_j]
            mean_normal = normal_i + normal_j
            mean_normal_norm = float(np.linalg.norm(mean_normal))
            if mean_normal_norm <= 1e-8:
                mean_normal = normal_i
                mean_normal_norm = max(float(np.linalg.norm(mean_normal)), 1e-8)
            mean_normal = mean_normal / mean_normal_norm
            centroid_delta = centroid_j - centroid_i
            pair_samples.append(
                PairSample(
                    face_i=int(sample_face_indices[local_i]),
                    face_j=int(sample_face_indices[local_j]),
                    midpoint=((centroid_i + centroid_j) * 0.5).astype(np.float64),
                    normal_cosine=float(np.dot(normal_i, normal_j)),
                    plane_gap_ratio=float(abs(np.dot(centroid_delta, mean_normal)) / diagonal),
                    centroid_distance_ratio=float(np.linalg.norm(centroid_delta) / diagonal),
                    area_ratio_i=float(sampled_area_ratios[local_i]),
                    area_ratio_j=float(sampled_area_ratios[local_j]),
                    boundary_i=bool(sampled_boundary[local_i]),
                    boundary_j=bool(sampled_boundary[local_j]),
                )
            )
    return {
        "diagonal": diagonal,
        "pairs": pair_samples,
        "sample_face_count": int(len(sample_face_indices)),
        "sample_indices": sample_face_indices.tolist(),
    }


def cluster_pair_samples(
    pairs: list[PairSample],
    *,
    diagonal: float,
    radius_ratio: float,
    top_k: int,
) -> list[dict[str, object]]:
    if not pairs:
        return []
    points = np.vstack([pair.midpoint for pair in pairs])
    cluster_radius = max(diagonal * max(radius_ratio, 1e-4), 1e-5)
    tree = cKDTree(points)
    neighborhoods = tree.query_ball_point(points, cluster_radius, workers=1)
    visited = np.zeros(len(points), dtype=bool)
    clusters: list[list[int]] = []

    for start in range(len(points)):
        if visited[start]:
            continue
        queue: deque[int] = deque([start])
        visited[start] = True
        members: list[int] = []
        while queue:
            index = queue.popleft()
            members.append(index)
            for neighbor in neighborhoods[index]:
                if not visited[neighbor]:
                    visited[neighbor] = True
                    queue.append(neighbor)
        clusters.append(members)

    summaries: list[dict[str, object]] = []
    for cluster_id, members in enumerate(sorted(clusters, key=len, reverse=True)[:top_k]):
        cluster_pairs = [pairs[index] for index in members]
        cluster_points = np.vstack([pair.midpoint for pair in cluster_pairs])
        unique_faces = sorted(
            {
                int(face_index)
                for pair in cluster_pairs
                for face_index in (pair.face_i, pair.face_j)
            }
        )
        normal_cosines = np.asarray([pair.normal_cosine for pair in cluster_pairs], dtype=np.float64)
        plane_gaps = np.asarray([pair.plane_gap_ratio for pair in cluster_pairs], dtype=np.float64)
        centroid_distances = np.asarray([pair.centroid_distance_ratio for pair in cluster_pairs], dtype=np.float64)
        boundary_flags = np.asarray(
            [
                boundary
                for pair in cluster_pairs
                for boundary in (pair.boundary_i, pair.boundary_j)
            ],
            dtype=bool,
        )
        area_ratios_local = np.asarray(
            [
                area_ratio
                for pair in cluster_pairs
                for area_ratio in (pair.area_ratio_i, pair.area_ratio_j)
            ],
            dtype=np.float64,
        )
        centroid = cluster_points.mean(axis=0)
        bounds_min = cluster_points.min(axis=0)
        bounds_max = cluster_points.max(axis=0)
        extents = bounds_max - bounds_min
        parallel_ratio = float(np.mean(np.abs(normal_cosines) >= 0.85)) if normal_cosines.size else 0.0
        opposed_ratio = float(np.mean(normal_cosines <= -0.85)) if normal_cosines.size else 0.0
        boundary_ratio = float(np.mean(boundary_flags)) if boundary_flags.size else 0.0
        area_ratio_p95 = float(np.percentile(area_ratios_local, 95)) if area_ratios_local.size else 0.0
        plane_gap_p95 = float(np.percentile(plane_gaps, 95)) if plane_gaps.size else 0.0
        centroid_distance_p95 = float(np.percentile(centroid_distances, 95)) if centroid_distances.size else 0.0
        recommendation = "review"
        if boundary_ratio <= 0.20 and parallel_ratio >= 0.60 and plane_gap_p95 <= 0.01:
            recommendation = "local_split_retriangulate_refine"
        elif boundary_ratio > 0.35:
            recommendation = "boundary_overlap_review"
        summaries.append(
            {
                "cluster_id": int(cluster_id),
                "pair_count": int(len(cluster_pairs)),
                "unique_face_count": int(len(unique_faces)),
                "centroid_world": [round(float(value), 6) for value in centroid],
                "bounds_min_world": [round(float(value), 6) for value in bounds_min],
                "bounds_max_world": [round(float(value), 6) for value in bounds_max],
                "extent_ratio_x": round(float(extents[0] / diagonal), 5),
                "extent_ratio_y": round(float(extents[1] / diagonal), 5),
                "extent_ratio_z": round(float(extents[2] / diagonal), 5),
                "parallel_pair_ratio": round(parallel_ratio, 5),
                "opposed_pair_ratio": round(opposed_ratio, 5),
                "boundary_face_ratio": round(boundary_ratio, 5),
                "normal_cosine_median": round(float(np.median(normal_cosines)), 5) if normal_cosines.size else 0.0,
                "plane_gap_ratio_p50": round(float(np.median(plane_gaps)), 5) if plane_gaps.size else 0.0,
                "plane_gap_ratio_p95": round(plane_gap_p95, 5),
                "centroid_distance_ratio_p50": round(float(np.median(centroid_distances)), 5) if centroid_distances.size else 0.0,
                "centroid_distance_ratio_p95": round(centroid_distance_p95, 5),
                "face_area_ratio_p95": round(area_ratio_p95, 8),
                "recommendation": recommendation,
            }
        )
    return summaries


def normalized_centroid(mesh: trimesh.Trimesh, centroid_world: np.ndarray) -> np.ndarray:
    bounds = np.asarray(mesh.bounds, dtype=np.float64)
    return np.clip(
        (centroid_world - bounds[0]) / np.maximum(bounds[1] - bounds[0], 1e-6),
        0.0,
        1.0,
    )


def analyze_mesh(
    label: str,
    path: Path,
    *,
    sample_cap: int,
    cluster_radius_ratio: float,
    top_k: int,
) -> tuple[trimesh.Trimesh, dict[str, object]]:
    mesh = trimesh.load_mesh(path, process=False)
    mesh = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices, dtype=np.float64),
        faces=np.asarray(mesh.faces, dtype=np.int64),
        process=False,
    )
    pair_data = collect_pair_samples(mesh, sample_cap=sample_cap)
    diagonal = float(pair_data["diagonal"])
    hotspots = cluster_pair_samples(
        pair_data["pairs"],
        diagonal=diagonal,
        radius_ratio=cluster_radius_ratio,
        top_k=top_k,
    )
    for hotspot in hotspots:
        hotspot["centroid_normalized"] = [
            round(float(value), 5)
            for value in normalized_centroid(mesh, np.asarray(hotspot["centroid_world"], dtype=np.float64))
        ]
    summary = {
        "label": label,
        "mesh_path": str(path),
        "face_count": int(len(mesh.faces)),
        "vertex_count": int(len(mesh.vertices)),
        "diagonal": round(diagonal, 6),
        "sample_face_count": int(pair_data["sample_face_count"]),
        "pair_sample_count": int(len(pair_data["pairs"])),
        "hotspots": hotspots,
    }
    return mesh, summary


def match_hotspots(
    analyses: list[tuple[trimesh.Trimesh, dict[str, object]]],
    *,
    match_radius: float,
) -> list[dict[str, object]]:
    if len(analyses) < 2:
        return []
    anchor_mesh, anchor_summary = analyses[0]
    anchor_hotspots = anchor_summary["hotspots"]
    matched_groups: list[dict[str, object]] = []
    for hotspot in anchor_hotspots:
        anchor_centroid = np.asarray(hotspot["centroid_normalized"], dtype=np.float64)
        group = {
            "anchor_label": anchor_summary["label"],
            "anchor_cluster_id": hotspot["cluster_id"],
            "anchor_centroid_normalized": hotspot["centroid_normalized"],
            "anchor_recommendation": hotspot["recommendation"],
            "matches": [],
        }
        for mesh, summary in analyses[1:]:
            best_match: dict[str, object] | None = None
            best_distance = math.inf
            for candidate in summary["hotspots"]:
                candidate_centroid = np.asarray(candidate["centroid_normalized"], dtype=np.float64)
                distance = float(np.linalg.norm(candidate_centroid - anchor_centroid))
                if distance < best_distance:
                    best_distance = distance
                    best_match = candidate
            if best_match is not None and best_distance <= match_radius:
                group["matches"].append(
                    {
                        "label": summary["label"],
                        "cluster_id": best_match["cluster_id"],
                        "distance": round(best_distance, 5),
                        "recommendation": best_match["recommendation"],
                        "pair_count": best_match["pair_count"],
                        "centroid_normalized": best_match["centroid_normalized"],
                    }
                )
        matched_groups.append(group)
    del anchor_mesh
    return matched_groups


def main() -> None:
    args = parse_args()
    mesh_specs = parse_mesh_specs(args.mesh)
    analyses: list[tuple[trimesh.Trimesh, dict[str, object]]] = []
    for label, path in mesh_specs:
        mesh, summary = analyze_mesh(
            label,
            path,
            sample_cap=max(256, int(args.sample_cap)),
            cluster_radius_ratio=float(args.cluster_radius_ratio),
            top_k=max(1, int(args.top_k)),
        )
        analyses.append((mesh, summary))

    payload = {
        "methodology": {
            "sample_cap": int(args.sample_cap),
            "cluster_radius_ratio": float(args.cluster_radius_ratio),
            "match_radius": float(args.match_radius),
            "top_k": int(args.top_k),
            "notes": [
                "Hotspots are clustered from sampled intersecting face pairs.",
                "Recommendation local_split_retriangulate_refine means low-boundary, near-parallel, thin-gap overlap dominated the cluster.",
                "This script is for localization and method selection, not for final HQ gate numbers.",
            ],
        },
        "meshes": [summary for _, summary in analyses],
        "matched_hotspots": match_hotspots(analyses, match_radius=float(args.match_radius)),
    }
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
