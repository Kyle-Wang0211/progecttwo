#!/usr/bin/env python3
import argparse
import math

import numpy as np

try:
    import open3d as o3d
except ImportError:  # pragma: no cover - exercised on remote host
    o3d = None


ROOM_MAX_X = 3.0
ROOM_MAX_Y = 2.5
ROOM_MAX_Z = 3.0

PLY_NUMPY_DTYPES = {
    "char": "i1",
    "uchar": "u1",
    "short": "<i2",
    "ushort": "<u2",
    "int": "<i4",
    "uint": "<u4",
    "float": "<f4",
    "double": "<f8",
}


def load_point_cloud(path):
    if o3d is not None:
        pcd = o3d.io.read_point_cloud(path)
        points = np.asarray(pcd.points)
        normals = np.asarray(pcd.normals) if pcd.has_normals() else None
        return points, normals
    return load_binary_ply(path)


def load_binary_ply(path):
    fmt = None
    vertex_count = None
    vertex_props = []
    in_vertex = False
    data_offset = None
    with open(path, "rb") as handle:
        first = handle.readline()
        if first.strip() != b"ply":
            raise ValueError(f"{path} is not a PLY file")
        while True:
            line = handle.readline()
            if not line:
                raise ValueError(f"{path} has no end_header")
            text = line.decode("ascii", "strict").strip()
            if text.startswith("format "):
                fmt = text.split()[1]
            elif text.startswith("element "):
                fields = text.split()
                in_vertex = len(fields) >= 3 and fields[1] == "vertex"
                if in_vertex:
                    vertex_count = int(fields[2])
            elif in_vertex and text.startswith("property "):
                fields = text.split()
                if len(fields) != 3:
                    raise ValueError(f"unsupported PLY property line: {text}")
                _, prop_type, prop_name = fields
                if prop_type not in PLY_NUMPY_DTYPES:
                    raise ValueError(f"unsupported PLY property type: {prop_type}")
                vertex_props.append((prop_name, PLY_NUMPY_DTYPES[prop_type]))
            elif text == "end_header":
                data_offset = handle.tell()
                break

    if fmt != "binary_little_endian":
        raise ValueError(f"unsupported PLY format: {fmt}")
    if vertex_count is None or not vertex_props or data_offset is None:
        raise ValueError(f"incomplete PLY header: {path}")

    dtype = np.dtype(vertex_props)
    raw = np.memmap(path, mode="r", dtype=dtype, offset=data_offset, shape=(vertex_count,))
    points = np.column_stack([raw["x"], raw["y"], raw["z"]]).astype(np.float64, copy=False)
    normals = None
    if {"nx", "ny", "nz"}.issubset(raw.dtype.names):
        normals = np.column_stack([raw["nx"], raw["ny"], raw["nz"]]).astype(np.float64, copy=False)
    return points, normals


def sdf_box(px, py, pz, cx, cy, cz, hx, hy, hz):
    dx = abs(px - cx) - hx
    dy = abs(py - cy) - hy
    dz = abs(pz - cz) - hz
    outside = math.sqrt(max(dx, 0.0) ** 2 + max(dy, 0.0) ** 2 + max(dz, 0.0) ** 2)
    inside = min(max(dx, max(dy, dz)), 0.0)
    return outside + inside


def sdf_cylinder(px, py, pz, bx, by, bz, radius, height):
    dx = px - bx
    dz = pz - bz
    dist_xz = math.sqrt(dx * dx + dz * dz) - radius
    dist_y = max(by - py, py - (by + height))
    outside = math.sqrt(max(dist_xz, 0.0) ** 2 + max(dist_y, 0.0) ** 2)
    inside = min(max(dist_xz, dist_y), 0.0)
    return outside + inside


def sdf_sphere(px, py, pz, cx, cy, cz, r):
    dx = px - cx
    dy = py - cy
    dz = pz - cz
    return math.sqrt(dx * dx + dy * dy + dz * dz) - r


def sdf_ellipsoid(px, py, pz, cx, cy, cz, rx, ry, rz):
    dx = (px - cx) / rx
    dy = (py - cy) / ry
    dz = (pz - cz) / rz
    return (math.sqrt(dx * dx + dy * dy + dz * dz) - 1.0) * min(rx, ry, rz)


def scene_sdf(px, py, pz):
    if px < -0.3 or px > 3.3 or py < -0.3 or py > 2.8 or pz < -0.3 or pz > 3.3:
        return 1.0

    d_floor = py
    d_ceiling = ROOM_MAX_Y - py
    d_left = px
    d_right = ROOM_MAX_X - px
    d_front = pz
    d_door = sdf_box(px, py, pz, 1.20, 1.00, 0.0, 0.40, 1.00, 0.10)
    d_front = max(d_front, -d_door)
    d_back = ROOM_MAX_Z - pz
    d_window = sdf_box(px, py, pz, 1.50, 1.60, ROOM_MAX_Z, 0.50, 0.40, 0.10)
    d_back = max(d_back, -d_window)

    d = min(d_floor, d_ceiling, d_left, d_right, d_front, d_back)
    d = min(d, sdf_box(px, py, pz, 1.5, 0.175, 2.50, 0.90, 0.175, 0.45))
    d = min(d, sdf_box(px, py, pz, 1.5, 0.45, 2.50, 0.85, 0.10, 0.42))
    d = min(d, sdf_box(px, py, pz, 1.5, 0.60, 2.96, 0.90, 0.35, 0.03))
    wrinkle = math.sin(15.0 * px) * math.sin(12.0 * pz) * 0.015
    d = min(d, sdf_box(px, py - wrinkle, pz, 1.5, 0.57, 2.40, 0.80, 0.025, 0.35))
    d = min(d, sdf_ellipsoid(px, py, pz, 1.0, 0.62, 2.70, 0.18, 0.08, 0.12))
    d = min(d, sdf_ellipsoid(px, py, pz, 2.0, 0.62, 2.70, 0.18, 0.08, 0.12))
    d = min(d, sdf_box(px, py, pz, 2.50, 0.75, 1.00, 0.40, 0.02, 0.30))
    for x, z in [(2.15, 0.75), (2.85, 0.75), (2.15, 1.25), (2.85, 1.25)]:
        d = min(d, sdf_cylinder(px, py, pz, x, 0.0, z, 0.02, 0.73))
    d = min(d, sdf_box(px, py, pz, 2.30, 0.45, 0.65, 0.20, 0.02, 0.20))
    d = min(d, sdf_box(px, py, pz, 2.30, 0.70, 0.83, 0.18, 0.20, 0.015))
    for x, z in [(2.13, 0.48), (2.47, 0.48), (2.13, 0.82), (2.47, 0.82)]:
        d = min(d, sdf_cylinder(px, py, pz, x, 0.0, z, 0.015, 0.43))
    outer = sdf_box(px, py, pz, 0.20, 0.90, 1.50, 0.18, 0.90, 0.35)
    inner = sdf_box(px, py, pz, 0.26, 0.90, 1.50, 0.14, 0.86, 0.31)
    d = min(d, max(outer, -inner))
    for y in [0.45, 0.90, 1.35]:
        d = min(d, sdf_box(px, py, pz, 0.20, y, 1.50, 0.17, 0.015, 0.34))
    for cx, cy, cz, hy, hz in [
        (0.18, 0.24, 1.25, 0.18, 0.015),
        (0.18, 0.22, 1.38, 0.16, 0.015),
        (0.18, 0.68, 1.30, 0.19, 0.015),
        (0.18, 0.66, 1.43, 0.17, 0.015),
        (0.18, 0.68, 1.58, 0.19, 0.015),
        (0.18, 1.13, 1.35, 0.19, 0.015),
        (0.18, 1.11, 1.50, 0.17, 0.015),
        (0.18, 1.13, 1.65, 0.20, 0.015),
    ]:
        d = min(d, sdf_box(px, py, pz, cx, cy, cz, 0.07, hy, hz))
    d = min(d, sdf_cylinder(px, py, pz, 2.65, 0.77, 1.20, 0.025, 0.30))
    d = min(d, sdf_sphere(px, py, pz, 2.65, 1.12, 1.20, 0.08))
    d = min(d, sdf_cylinder(px, py, pz, 1.30, 0.0, 1.00, 0.08, 0.18))
    for cx, cy, cz, r in [
        (1.30, 0.30, 1.00, 0.10),
        (1.20, 0.28, 0.92, 0.07),
        (1.40, 0.28, 0.92, 0.07),
        (1.22, 0.28, 1.08, 0.07),
        (1.38, 0.28, 1.08, 0.07),
    ]:
        d = min(d, sdf_sphere(px, py, pz, cx, cy, cz, r))
    bump = math.sin(8.0 * px) * math.sin(8.0 * pz) * 0.005
    d = min(d, sdf_box(px, py - bump, pz, 1.50, 0.008, 1.30, 0.60, 0.008, 0.45))
    d = min(d, sdf_box(px, py, pz, 0.015, 1.50, 1.00, 0.015, 0.20, 0.25))
    fold = math.sin(20.0 * px) * 0.015
    d = min(d, sdf_box(px, py, pz - fold, 1.50, 1.40, 2.97, 0.70, 0.60, 0.02))
    d = min(d, sdf_box(px, py, pz, 0.40, 0.25, 2.50, 0.20, 0.25, 0.20))
    return d


def scene_normal(px, py, pz):
    eps = 0.0005
    nx = scene_sdf(px + eps, py, pz) - scene_sdf(px - eps, py, pz)
    ny = scene_sdf(px, py + eps, pz) - scene_sdf(px, py - eps, pz)
    nz = scene_sdf(px, py, pz + eps) - scene_sdf(px, py, pz - eps)
    norm = math.sqrt(nx * nx + ny * ny + nz * nz)
    if norm <= 1e-8:
        return np.array([0.0, 1.0, 0.0], dtype=np.float64)
    return np.array([nx / norm, ny / norm, nz / norm], dtype=np.float64)


def quat_to_rot(qx, qy, qz, qw):
    q = np.array([qx, qy, qz, qw], dtype=np.float64)
    q /= np.linalg.norm(q) + 1e-12
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=np.float64)


def parse_pose_file(path, has_frame_index):
    poses = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.strip().split()
            if not fields:
                continue
            if not has_frame_index and len(fields) == 17:
                idx = len(poses)
                matrix = np.array([float(v) for v in fields[1:]], dtype=np.float64).reshape(4, 4, order="F")
                poses.append((idx, matrix[:3, 3], matrix[:3, :3]))
                continue
            vals = [float(v) for v in fields]
            if has_frame_index:
                idx = int(vals[0])
                tx, ty, tz, qx, qy, qz, qw = vals[1:8]
            else:
                idx = len(poses)
                tx, ty, tz, qx, qy, qz, qw = vals[1:8]
            poses.append((idx, np.array([tx, ty, tz], dtype=np.float64), quat_to_rot(qx, qy, qz, qw)))
    return poses


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ply", required=True)
    parser.add_argument("--traj", default=None)
    parser.add_argument("--gt-traj", default=None)
    parser.add_argument("--static-window", type=int, default=30)
    parser.add_argument("--static-thresh-mm", type=float, default=3.0)
    parser.add_argument("--sample-max", type=int, default=0)
    parser.add_argument("--seed", type=int, default=1234)
    return parser.parse_args()


def umeyama_similarity(src, dst):
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError("expected Nx3 source/destination arrays")
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_centered = src - src_mean
    dst_centered = dst - dst_mean
    cov = (dst_centered.T @ src_centered) / src.shape[0]
    u, d, vt = np.linalg.svd(cov)
    s = np.eye(3, dtype=np.float64)
    if np.linalg.det(u) * np.linalg.det(vt) < 0.0:
        s[-1, -1] = -1.0
    rot = u @ s @ vt
    src_var = np.mean(np.sum(src_centered * src_centered, axis=1))
    scale = np.trace(np.diag(d) @ s) / max(src_var, 1e-12)
    trans = dst_mean - scale * (rot @ src_mean)
    return scale, rot, trans


def main():
    args = parse_args()
    points, normals = load_point_cloud(args.ply)
    eval_points = points
    eval_normals = normals

    est = None
    gt = None
    if args.traj and args.gt_traj:
        est = parse_pose_file(args.traj, has_frame_index=True)
        gt = parse_pose_file(args.gt_traj, has_frame_index=False)
        count = min(len(est), len(gt))
        est_pos = np.stack([row[1] for row in est[:count]], axis=0)
        gt_pos = np.stack([row[1] for row in gt[:count]], axis=0)
        sim_scale, sim_rot, sim_trans = umeyama_similarity(est_pos, gt_pos)
        print("ALIGN_SCALE", round(float(sim_scale), 6))
        print("ALIGN_ROT_DET", round(float(np.linalg.det(sim_rot)), 6))
        print("ALIGN_TRANS_M", " ".join(f"{v:.4f}" for v in sim_trans))
        points = (sim_scale * (sim_rot @ points.T)).T + sim_trans
        if normals is not None:
            normals = (sim_rot @ normals.T).T
        aligned_est = []
        for idx, trans, rot in est[:count]:
            aligned_trans = sim_scale * (sim_rot @ trans) + sim_trans
            aligned_rot = sim_rot @ rot
            aligned_est.append((idx, aligned_trans, aligned_rot))
        est = aligned_est
        gt = gt[:count]

    if args.sample_max > 0 and points.shape[0] > args.sample_max:
        rng = np.random.default_rng(args.seed)
        sample_idx = rng.choice(points.shape[0], size=args.sample_max, replace=False)
        eval_points = points[sample_idx]
        if normals is not None:
            eval_normals = normals[sample_idx]

    dists = np.empty(eval_points.shape[0], dtype=np.float64)
    dots = np.empty(eval_points.shape[0], dtype=np.float64) if eval_normals is not None else None
    absdots = np.empty(eval_points.shape[0], dtype=np.float64) if eval_normals is not None else None

    for i, point in enumerate(eval_points):
        gt_normal = scene_normal(point[0], point[1], point[2])
        dists[i] = abs(scene_sdf(point[0], point[1], point[2]))
        if eval_normals is not None:
            n = eval_normals[i]
            n_norm = np.linalg.norm(n)
            if n_norm > 1e-12:
                n = n / n_norm
            dots[i] = float(np.dot(n, gt_normal))
            absdots[i] = abs(dots[i])

    print("POINTS", points.shape[0])
    if eval_points.shape[0] != points.shape[0]:
        print("POINTS_EVAL", eval_points.shape[0])
    bbox_min = points.min(axis=0)
    bbox_max = points.max(axis=0)
    bbox_extent = bbox_max - bbox_min
    centroid = points.mean(axis=0)
    room_center = np.array([ROOM_MAX_X * 0.5, ROOM_MAX_Y * 0.5, ROOM_MAX_Z * 0.5], dtype=np.float64)
    in_room = np.logical_and.reduce([
        points[:, 0] >= 0.0,
        points[:, 0] <= ROOM_MAX_X,
        points[:, 1] >= 0.0,
        points[:, 1] <= ROOM_MAX_Y,
        points[:, 2] >= 0.0,
        points[:, 2] <= ROOM_MAX_Z,
    ])
    print("L_BBOX_MIN_M", " ".join(f"{v:.4f}" for v in bbox_min))
    print("L_BBOX_MAX_M", " ".join(f"{v:.4f}" for v in bbox_max))
    print("L_BBOX_EXTENT_M", " ".join(f"{v:.4f}" for v in bbox_extent))
    print("L_CENTROID_M", " ".join(f"{v:.4f}" for v in centroid))
    print("L_CENTROID_OFFSET_MM", round(float(np.linalg.norm(centroid - room_center) * 1000.0), 3))
    print("L_INROOM_RATE", round(float(in_room.mean()), 6))
    print("K_P50_MM", round(float(np.percentile(dists, 50) * 1000.0), 3))
    print("K_P95_MM", round(float(np.percentile(dists, 95) * 1000.0), 3))
    print("K_MEAN_MM", round(float(dists.mean() * 1000.0), 3))
    if normals is not None:
        strict = np.logical_and(dists < 0.02, dots > 0.70)
        absdot = np.logical_and(dists < 0.02, absdots > 0.70)
        print("Q1_STRICT_RATE", round(float(strict.mean()), 6))
        print("Q1_ABSDOT_RATE", round(float(absdot.mean()), 6))
        print("NORMAL_DOT_MEDIAN", round(float(np.median(dots)), 6))
        print("NORMAL_ABSDOT_MEDIAN", round(float(np.median(absdots)), 6))

    if est is not None and gt is not None:
        count = min(len(est), len(gt))
        window = args.static_window
        static_thresh = args.static_thresh_mm / 1000.0
        best_gt = float("inf")
        p2_rows = []
        for start in range(0, count - window):
            gt_disp = np.linalg.norm(gt[start + window][1] - gt[start][1])
            best_gt = min(best_gt, gt_disp)
            if gt_disp <= static_thresh:
                est_disp = np.linalg.norm(est[start + window][1] - est[start][1])
                rel = est[start][2].T @ est[start + window][2]
                rot_deg = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(rel) - 1.0) * 0.5))))
                p2_rows.append((start, est_disp * 1000.0, rot_deg))
        print("P2_STATIC_WINDOWS", len(p2_rows))
        print("P2_MIN_GT_WINDOW_MM", round(best_gt * 1000.0, 3))
        if p2_rows:
            trans = np.array([row[1] for row in p2_rows])
            rot = np.array([row[2] for row in p2_rows])
            print("P2_TRANS_P50_MM", round(float(np.percentile(trans, 50)), 3))
            print("P2_TRANS_P95_MM", round(float(np.percentile(trans, 95)), 3))
            print("P2_ROT_P50_DEG", round(float(np.percentile(rot, 50)), 3))
            print("P2_ROT_P95_DEG", round(float(np.percentile(rot, 95)), 3))


if __name__ == "__main__":
    main()
