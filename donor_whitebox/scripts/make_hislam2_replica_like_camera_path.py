#!/usr/bin/env python3
import argparse
import math
import json
from pathlib import Path

import numpy as np


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--reference-traj", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--mode", choices=("baseline", "farther_cam", "denser_sampling", "farther_plus_denser", "global_room_closure", "global_room_closure_local_refine"),
                   default="baseline")
    p.add_argument("--duration", type=float, default=37.0)
    p.add_argument("--room-min-x", type=float, default=0.05)
    p.add_argument("--room-max-x", type=float, default=2.95)
    p.add_argument("--room-min-y", type=float, default=0.30)
    p.add_argument("--room-max-y", type=float, default=2.20)
    p.add_argument("--room-min-z", type=float, default=0.05)
    p.add_argument("--room-max-z", type=float, default=2.95)
    p.add_argument("--yaw-step-deg", type=float, default=1.0)
    p.add_argument("--farther-shrink", type=float, default=0.72,
                   help="Shrink factor toward room center for farther_cam modes.")
    p.add_argument("--denser-factor", type=float, default=1.5,
                   help="Frame multiplier for denser_sampling modes.")
    p.add_argument("--target-frames", type=int, default=0,
                   help="Optional explicit target frame count override.")
    p.add_argument("--stats-out", default=None,
                   help="Optional JSON sidecar path for fitted path statistics.")
    return p.parse_args()


def load_reference_traj(path: Path):
    mats = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            vals = [float(x) for x in line.strip().split()]
            if len(vals) != 16:
                continue
            mats.append(np.asarray(vals, dtype=np.float64).reshape(4, 4))
    if not mats:
        raise RuntimeError(f"empty or invalid reference traj: {path}")
    return mats


def yaw_rotation(deg: float) -> np.ndarray:
    a = math.radians(deg)
    c = math.cos(a)
    s = math.sin(a)
    return np.asarray(
        [
            [c, 0.0, s],
            [0.0, 1.0, 0.0],
            [-s, 0.0, c],
        ],
        dtype=np.float64,
    )


def normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    if n < 1e-12:
        return v.copy()
    return v / n


def look_at_rotation(eye: np.ndarray, target: np.ndarray) -> np.ndarray:
    forward = normalize(target - eye)
    if np.linalg.norm(forward) < 1e-12:
        forward = np.asarray([0.0, 0.0, 1.0], dtype=np.float64)

    world_up = np.asarray([0.0, 1.0, 0.0], dtype=np.float64)
    right = np.cross(world_up, forward)
    if np.linalg.norm(right) < 1e-12:
        world_up = np.asarray([0.0, 0.0, 1.0], dtype=np.float64)
        right = np.cross(world_up, forward)
    right = normalize(right)
    up = normalize(np.cross(forward, right))

    R = np.eye(3, dtype=np.float64)
    R[:, 0] = right
    R[:, 1] = up
    R[:, 2] = forward
    return R


def fit_transform(positions: np.ndarray, room_bounds, yaw_step_deg: float):
    center = positions.mean(axis=0)
    centered = positions - center
    target_center = np.asarray(
        [
            0.5 * (room_bounds[0] + room_bounds[1]),
            0.5 * (room_bounds[2] + room_bounds[3]),
            0.5 * (room_bounds[4] + room_bounds[5]),
        ],
        dtype=np.float64,
    )

    best = None
    yaw = 0.0
    while yaw < 360.0 - 1e-9:
        G = yaw_rotation(yaw)
        rotated = centered @ G.T
        mins = rotated.min(axis=0)
        maxs = rotated.max(axis=0)
        extents = np.maximum(maxs - mins, 1e-9)
        scale = min(
            (room_bounds[1] - room_bounds[0]) / extents[0],
            (room_bounds[3] - room_bounds[2]) / extents[1],
            (room_bounds[5] - room_bounds[4]) / extents[2],
        )
        scaled = rotated * scale
        translated = scaled + target_center

        # Prefer the largest feasible scale; tie-break by keeping the average
        # forward direction aimed more toward the room center.
        inward_score = 0.0
        dirs = target_center[None, :] - translated
        norms = np.linalg.norm(dirs, axis=1)
        inward_score = float(np.mean(norms))
        cand = (scale, -inward_score, yaw, G, translated)
        if best is None or cand > best:
            best = cand
        yaw += yaw_step_deg

    assert best is not None
    scale, _, yaw_deg, G, translated = best
    return scale, yaw_deg, G, translated, center, target_center


def summarize_positions(positions: np.ndarray):
    steps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
    center = positions.mean(axis=0)
    dstart = np.linalg.norm(positions - positions[0], axis=1)
    dcenter = np.linalg.norm(positions - center, axis=1)
    return {
        "frames": int(len(positions)),
        "step_mean_m": float(steps.mean()),
        "step_p95_m": float(np.percentile(steps, 95)),
        "step_max_m": float(steps.max()),
        "total_path_m": float(steps.sum()),
        "frames_per_meter": float(len(positions) / max(steps.sum(), 1e-9)),
        "max_from_start_m": float(dstart.max()),
        "max_from_center_m": float(dcenter.max()),
    }


def build_stage_intervals(mode: str, frames: int):
    if frames <= 0:
        return []

    if mode == "global_room_closure":
        orbit = int(round(frames * 0.30))
        diag = int(round(frames * 0.20))
        corner_pan = int(round(frames * 0.20))
        corner_ring = int(round(frames * 0.15))
        closure = max(frames - orbit - diag - corner_pan - corner_ring, 1)
        lengths = [
            ("global_orbit", orbit),
            ("global_diag", diag),
            ("global_corner_pan", corner_pan),
            ("global_corner_ring", corner_ring),
            ("closure", closure),
        ]
    elif mode == "global_room_closure_local_refine":
        global_frames = int(round(frames * 0.30))
        closure_frames = int(round(frames * 0.12))
        revisit_frames = int(round(frames * 0.12))
        local_frames = max(frames - global_frames - closure_frames - revisit_frames, 1)
        lengths = [
            ("global_anchor", global_frames),
            ("local_refine", local_frames),
            ("revisit", revisit_frames),
            ("closure", closure_frames),
        ]
    else:
        return []

    intervals = []
    start = 0
    for name, count in lengths:
        if count <= 0:
            continue
        end = min(start + count, frames)
        intervals.append(
            {
                "name": name,
                "start_frame": int(start),
                "end_frame_exclusive": int(end),
                "start_ratio": float(start / frames),
                "end_ratio": float(end / frames),
            }
        )
        start = end
    if intervals:
        intervals[-1]["end_frame_exclusive"] = int(frames)
        intervals[-1]["end_ratio"] = 1.0
    return intervals


def rotation_to_quaternion(R: np.ndarray) -> np.ndarray:
    q = np.empty(4, dtype=np.float64)
    trace = np.trace(R)
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        q[0] = 0.25 * s
        q[1] = (R[2, 1] - R[1, 2]) / s
        q[2] = (R[0, 2] - R[2, 0]) / s
        q[3] = (R[1, 0] - R[0, 1]) / s
    else:
        idx = int(np.argmax(np.diag(R)))
        if idx == 0:
            s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
            q[0] = (R[2, 1] - R[1, 2]) / s
            q[1] = 0.25 * s
            q[2] = (R[0, 1] + R[1, 0]) / s
            q[3] = (R[0, 2] + R[2, 0]) / s
        elif idx == 1:
            s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
            q[0] = (R[0, 2] - R[2, 0]) / s
            q[1] = (R[0, 1] + R[1, 0]) / s
            q[2] = 0.25 * s
            q[3] = (R[1, 2] + R[2, 1]) / s
        else:
            s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
            q[0] = (R[1, 0] - R[0, 1]) / s
            q[1] = (R[0, 2] + R[2, 0]) / s
            q[2] = (R[1, 2] + R[2, 1]) / s
            q[3] = 0.25 * s
    q /= np.linalg.norm(q)
    return q


def quaternion_to_rotation(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q
    return np.asarray(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def slerp(q0: np.ndarray, q1: np.ndarray, t: float) -> np.ndarray:
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        q = q0 + t * (q1 - q0)
        q /= np.linalg.norm(q)
        return q
    theta_0 = math.acos(dot)
    sin_theta_0 = math.sin(theta_0)
    theta = theta_0 * t
    sin_theta = math.sin(theta)
    s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
    s1 = sin_theta / sin_theta_0
    q = s0 * q0 + s1 * q1
    q /= np.linalg.norm(q)
    return q


def resample_path(positions: np.ndarray, rotations: np.ndarray, target_frames: int):
    if target_frames <= 0 or target_frames == len(positions):
        return positions, rotations
    src_t = np.linspace(0.0, 1.0, len(positions))
    dst_t = np.linspace(0.0, 1.0, target_frames)
    dst_pos = np.empty((target_frames, 3), dtype=np.float64)
    quats = np.stack([rotation_to_quaternion(R) for R in rotations], axis=0)
    dst_rot = np.empty((target_frames, 3, 3), dtype=np.float64)
    for i, t in enumerate(dst_t):
        hi = int(np.searchsorted(src_t, t, side="right"))
        if hi <= 0:
            dst_pos[i] = positions[0]
            dst_rot[i] = rotations[0]
            continue
        if hi >= len(src_t):
            dst_pos[i] = positions[-1]
            dst_rot[i] = rotations[-1]
            continue
        lo = hi - 1
        span = max(src_t[hi] - src_t[lo], 1e-12)
        u = float((t - src_t[lo]) / span)
        dst_pos[i] = (1.0 - u) * positions[lo] + u * positions[hi]
        q = slerp(quats[lo], quats[hi], u)
        dst_rot[i] = quaternion_to_rotation(q)
    return dst_pos, dst_rot


def append_segment(
    out_pos: list[np.ndarray],
    out_rot: list[np.ndarray],
    start_pos: np.ndarray,
    end_pos: np.ndarray,
    start_target: np.ndarray,
    end_target: np.ndarray,
    frames: int,
    include_first: bool,
):
    if frames <= 0:
        return
    for i in range(frames):
        if i == 0 and not include_first:
            continue
        denom = max(frames - 1, 1)
        u = float(i) / float(denom)
        pos = (1.0 - u) * start_pos + u * end_pos
        target = (1.0 - u) * start_target + u * end_target
        out_pos.append(pos)
        out_rot.append(look_at_rotation(pos, target))


def append_static_pan(
    out_pos: list[np.ndarray],
    out_rot: list[np.ndarray],
    pos: np.ndarray,
    targets: list[np.ndarray],
    frames_per_target: int,
    include_first: bool,
):
    if not targets or frames_per_target <= 0:
        return
    current = targets[0]
    first = True
    for nxt in targets[1:] + [targets[0]]:
        append_segment(
            out_pos,
            out_rot,
            pos,
            pos,
            current,
            nxt,
            frames_per_target,
            include_first if first else False,
        )
        current = nxt
        first = False


def connect_if_needed(
    out_pos: list[np.ndarray],
    out_rot: list[np.ndarray],
    next_pos: np.ndarray,
    next_target: np.ndarray,
    frames: int,
):
    if not out_pos or frames <= 0:
        return
    prev_pos = out_pos[-1]
    if np.linalg.norm(prev_pos - next_pos) < 1e-6:
        return
    append_segment(
        out_pos,
        out_rot,
        prev_pos,
        next_pos,
        next_target,
        next_target,
        frames,
        include_first=False,
    )


def clamp_camera_point(point: np.ndarray, room_bounds, margin_xy: float = 0.18, margin_y: float = 0.55) -> np.ndarray:
    min_x, max_x, min_y, max_y, min_z, max_z = room_bounds
    return np.asarray(
        [
            np.clip(point[0], min_x + margin_xy, max_x - margin_xy),
            np.clip(point[1], min_y + margin_y, max_y - margin_y),
            np.clip(point[2], min_z + margin_xy, max_z - margin_xy),
        ],
        dtype=np.float64,
    )


def make_arc_points(
    target: np.ndarray,
    room_bounds,
    radius_x: float,
    radius_z: float,
    y: float,
    start_deg: float,
    end_deg: float,
    count: int,
) -> list[np.ndarray]:
    count = max(int(count), 2)
    points: list[np.ndarray] = []
    for deg in np.linspace(start_deg, end_deg, count):
        rad = math.radians(float(deg))
        pos = np.asarray(
            [
                target[0] + radius_x * math.cos(rad),
                y,
                target[2] + radius_z * math.sin(rad),
            ],
            dtype=np.float64,
        )
        points.append(clamp_camera_point(pos, room_bounds))
    return points


def append_target_arc(
    out_pos: list[np.ndarray],
    out_rot: list[np.ndarray],
    target: np.ndarray,
    points: list[np.ndarray],
    frames_per_edge: int,
    connect_frames: int,
):
    if not points:
        return
    connect_if_needed(out_pos, out_rot, points[0], target, connect_frames)
    first = not out_pos
    for start, end in zip(points[:-1], points[1:]):
        append_segment(
            out_pos,
            out_rot,
            start,
            end,
            target,
            target,
            frames_per_edge,
            include_first=first,
        )
        first = False


def build_global_room_closure_path(
    room_bounds,
    target_frames: int,
):
    min_x, max_x, min_y, max_y, min_z, max_z = room_bounds
    cx = 0.5 * (min_x + max_x)
    cy = 0.5 * (min_y + max_y)
    cz = 0.5 * (min_z + max_z)
    center = np.asarray([cx, cy, cz], dtype=np.float64)

    safe_y = np.clip(cy + 0.05, min_y + 0.55, max_y - 0.55)
    floor_y = min_y + 0.35
    ceil_y = max_y - 0.35

    rx = 0.28 * (max_x - min_x)
    rz = 0.28 * (max_z - min_z)
    rx = min(rx, 0.65)
    rz = min(rz, 0.65)

    ring_points = [
        np.asarray([cx + rx, safe_y, cz], dtype=np.float64),
        np.asarray([cx + 0.55 * rx, safe_y, cz + 0.8 * rz], dtype=np.float64),
        np.asarray([cx - 0.55 * rx, safe_y, cz + 0.8 * rz], dtype=np.float64),
        np.asarray([cx - rx, safe_y, cz], dtype=np.float64),
        np.asarray([cx - 0.55 * rx, safe_y, cz - 0.8 * rz], dtype=np.float64),
        np.asarray([cx + 0.55 * rx, safe_y, cz - 0.8 * rz], dtype=np.float64),
        np.asarray([cx + rx, safe_y, cz], dtype=np.float64),
    ]

    diag_a0 = np.asarray([cx - 0.38 * (max_x - min_x), safe_y, cz - 0.38 * (max_z - min_z)], dtype=np.float64)
    diag_a1 = np.asarray([cx + 0.38 * (max_x - min_x), safe_y, cz + 0.38 * (max_z - min_z)], dtype=np.float64)
    diag_b0 = np.asarray([cx - 0.38 * (max_x - min_x), safe_y, cz + 0.38 * (max_z - min_z)], dtype=np.float64)
    diag_b1 = np.asarray([cx + 0.38 * (max_x - min_x), safe_y, cz - 0.38 * (max_z - min_z)], dtype=np.float64)

    corner_targets = [
        np.asarray([min_x + 0.18, floor_y, min_z + 0.18], dtype=np.float64),
        np.asarray([max_x - 0.18, floor_y, min_z + 0.18], dtype=np.float64),
        np.asarray([max_x - 0.18, ceil_y, max_z - 0.18], dtype=np.float64),
        np.asarray([min_x + 0.18, ceil_y, max_z - 0.18], dtype=np.float64),
    ]

    center_targets = [
        np.asarray([cx, floor_y, cz], dtype=np.float64),
        np.asarray([cx, ceil_y, cz], dtype=np.float64),
        center,
    ]

    frames = target_frames if target_frames > 0 else 4000
    seg = {
        "orbit": int(round(frames * 0.30)),
        "diag": int(round(frames * 0.20)),
        "corner_pan": int(round(frames * 0.20)),
        "corner_ring": int(round(frames * 0.15)),
    }
    seg["closure"] = max(frames - sum(seg.values()), 1)

    out_pos: list[np.ndarray] = []
    out_rot: list[np.ndarray] = []

    orbit_frames = max(seg["orbit"] // (len(ring_points) - 1), 16)
    first = True
    for a, b in zip(ring_points[:-1], ring_points[1:]):
        append_segment(out_pos, out_rot, a, b, center, center, orbit_frames, include_first=first)
        first = False

    diag_frames = max(seg["diag"] // 4, 16)
    connect_if_needed(out_pos, out_rot, diag_a0, diag_a1, max(diag_frames // 2, 12))
    append_segment(out_pos, out_rot, diag_a0, diag_a1, diag_a1, diag_a1, diag_frames, include_first=False)
    append_segment(out_pos, out_rot, diag_a1, diag_a0, center, center, diag_frames, include_first=False)
    connect_if_needed(out_pos, out_rot, diag_b0, diag_b1, max(diag_frames // 2, 12))
    append_segment(out_pos, out_rot, diag_b0, diag_b1, diag_b1, diag_b1, diag_frames, include_first=False)
    append_segment(out_pos, out_rot, diag_b1, diag_b0, center, center, diag_frames, include_first=False)

    pan_frames = max(seg["corner_pan"] // 4, 20)
    connect_if_needed(out_pos, out_rot, center, corner_targets[0], max(pan_frames // 2, 12))
    append_static_pan(out_pos, out_rot, center, corner_targets, pan_frames, include_first=False)
    append_static_pan(out_pos, out_rot, center, center_targets, max(seg["corner_pan"] // 8, 12), include_first=False)

    ring_targets = corner_targets + [corner_targets[0]]
    corner_ring_frames = max(seg["corner_ring"] // 4, 20)
    connect_if_needed(out_pos, out_rot, ring_points[0], ring_targets[0], max(corner_ring_frames // 2, 12))
    for i in range(4):
        start = ring_points[i]
        end = ring_points[i + 1]
        append_segment(
            out_pos,
            out_rot,
            start,
            end,
            ring_targets[i],
            ring_targets[i + 1],
            corner_ring_frames,
            include_first=False,
        )

    closure_points = [
        ring_points[4],
        ring_points[5],
        ring_points[6],
        ring_points[1],
        ring_points[2],
        ring_points[3],
        ring_points[4],
    ]
    closure_frames = max(seg["closure"] // (len(closure_points) - 1), 18)
    for a, b in zip(closure_points[:-1], closure_points[1:]):
        append_segment(out_pos, out_rot, a, b, center, center, closure_frames, include_first=False)

    positions = np.stack(out_pos, axis=0)
    rotations = np.stack(out_rot, axis=0)
    if len(positions) != frames:
        positions, rotations = resample_path(positions, rotations, frames)
    return positions, rotations


def build_global_room_closure_local_refine_path(
    room_bounds,
    target_frames: int,
):
    min_x, max_x, min_y, max_y, min_z, max_z = room_bounds
    cx = 0.5 * (min_x + max_x)
    cy = 0.5 * (min_y + max_y)
    cz = 0.5 * (min_z + max_z)
    center = np.asarray([cx, cy, cz], dtype=np.float64)

    frames = target_frames if target_frames > 0 else 8000
    global_frames = int(round(frames * 0.30))
    closure_frames = int(round(frames * 0.12))
    revisit_frames = int(round(frames * 0.12))
    local_frames = max(frames - global_frames - closure_frames - revisit_frames, 1)

    base_pos, base_rot = build_global_room_closure_path(room_bounds, global_frames)
    out_pos = [p.copy() for p in base_pos]
    out_rot = [R.copy() for R in base_rot]

    safe_y = np.clip(cy + 0.05, min_y + 0.60, max_y - 0.62)
    low_y = np.clip(safe_y - 0.10, min_y + 0.54, max_y - 0.72)
    mid_y = np.clip(safe_y + 0.12, min_y + 0.62, max_y - 0.60)
    high_y = np.clip(safe_y + 0.30, min_y + 0.72, max_y - 0.46)
    top_y = np.clip(safe_y + 0.44, min_y + 0.82, max_y - 0.34)

    bed_target = np.asarray([1.50, 0.62, 2.45], dtype=np.float64)
    table_target = np.asarray([2.50, 0.78, 1.00], dtype=np.float64)
    shelf_target = np.asarray([0.20, 0.95, 1.50], dtype=np.float64)
    window_target = np.asarray([1.50, 1.45, 2.92], dtype=np.float64)
    center_detail_target = np.asarray([1.30, 0.32, 1.00], dtype=np.float64)

    target_arc_specs = [
        (
            bed_target,
            [
                (1.18, 0.84, low_y, 205.0, 336.0, 12),
                (1.05, 0.78, mid_y, 336.0, 210.0, 12),
                (0.95, 0.68, high_y, 205.0, 332.0, 12),
            ],
        ),
        (
            table_target,
            [
                (0.98, 0.86, low_y, 118.0, 308.0, 12),
                (0.88, 0.80, mid_y, 308.0, 124.0, 12),
                (0.80, 0.74, high_y, 124.0, 300.0, 12),
            ],
        ),
        (
            shelf_target,
            [
                (0.92, 0.78, low_y, -70.0, 70.0, 12),
                (0.86, 0.72, mid_y, 70.0, -70.0, 12),
                (0.78, 0.66, high_y, -58.0, 58.0, 12),
            ],
        ),
        (
            window_target,
            [
                (1.02, 0.54, mid_y, 205.0, 335.0, 12),
                (0.96, 0.50, high_y, 335.0, 205.0, 12),
                (0.88, 0.44, top_y, 210.0, 330.0, 12),
            ],
        ),
        (
            center_detail_target,
            [
                (1.02, 0.92, low_y, 18.0, 342.0, 14),
                (0.92, 0.82, mid_y, 342.0, 18.0, 14),
                (0.84, 0.76, high_y, 18.0, 342.0, 14),
            ],
        ),
    ]

    local_specs = []
    for target, arcs in target_arc_specs:
        for radius_x, radius_z, y_level, start_deg, end_deg, num_points in arcs:
            local_specs.append(
                (
                    target,
                    make_arc_points(
                        target,
                        room_bounds,
                        radius_x,
                        radius_z,
                        y_level,
                        start_deg,
                        end_deg,
                        num_points,
                    ),
                )
            )

    local_edges = sum(max(len(points) - 1, 0) for _, points in local_specs)
    frames_per_edge = max(local_frames // max(local_edges, 1), 18)
    connect_frames = max(frames_per_edge // 2, 14)
    for target, points in local_specs:
        append_target_arc(out_pos, out_rot, target, points, frames_per_edge, connect_frames)

    revisit_points = [
        np.asarray([cx + 0.62, safe_y, cz + 0.62], dtype=np.float64),
        np.asarray([cx - 0.68, safe_y, cz + 0.58], dtype=np.float64),
        np.asarray([cx - 0.60, safe_y, cz - 0.64], dtype=np.float64),
        np.asarray([cx + 0.66, safe_y, cz - 0.58], dtype=np.float64),
        np.asarray([cx + 0.10, high_y, cz + 0.02], dtype=np.float64),
        np.asarray([cx - 0.08, mid_y, cz - 0.10], dtype=np.float64),
        np.asarray([cx + 0.62, safe_y, cz + 0.62], dtype=np.float64),
    ]
    revisit_targets = [
        bed_target,
        shelf_target,
        table_target,
        window_target,
        center_detail_target,
        np.asarray([cx, min_y + 0.38, cz], dtype=np.float64),
        center,
    ]
    revisit_frames_per_edge = max(revisit_frames // max(len(revisit_points) - 1, 1), 28)
    connect_if_needed(
        out_pos,
        out_rot,
        revisit_points[0],
        revisit_targets[0],
        max(revisit_frames_per_edge // 2, 18),
    )
    for i, (start, end) in enumerate(zip(revisit_points[:-1], revisit_points[1:])):
        append_segment(
            out_pos,
            out_rot,
            start,
            end,
            revisit_targets[i],
            revisit_targets[i + 1],
            revisit_frames_per_edge,
            include_first=False,
        )

    rx = min(0.30 * (max_x - min_x), 0.72)
    rz = min(0.30 * (max_z - min_z), 0.72)
    closure_points = [
        np.asarray([cx + rx, safe_y, cz], dtype=np.float64),
        np.asarray([cx + 0.65 * rx, safe_y, cz + 0.85 * rz], dtype=np.float64),
        np.asarray([cx - 0.65 * rx, safe_y, cz + 0.85 * rz], dtype=np.float64),
        np.asarray([cx - rx, safe_y, cz], dtype=np.float64),
        np.asarray([cx - 0.65 * rx, safe_y, cz - 0.85 * rz], dtype=np.float64),
        np.asarray([cx + 0.65 * rx, safe_y, cz - 0.85 * rz], dtype=np.float64),
        np.asarray([cx + rx, safe_y, cz], dtype=np.float64),
    ]
    closure_targets = [
        np.asarray([min_x + 0.16, min_y + 0.38, min_z + 0.16], dtype=np.float64),
        np.asarray([max_x - 0.16, min_y + 0.38, min_z + 0.16], dtype=np.float64),
        np.asarray([max_x - 0.16, max_y - 0.32, max_z - 0.16], dtype=np.float64),
        np.asarray([min_x + 0.16, max_y - 0.32, max_z - 0.16], dtype=np.float64),
        center,
        np.asarray([cx, min_y + 0.35, cz], dtype=np.float64),
        center,
    ]
    closure_frames_per_edge = max(closure_frames // max(len(closure_points) - 1, 1), 32)
    connect_if_needed(out_pos, out_rot, closure_points[0], closure_targets[0], max(closure_frames_per_edge // 2, 16))
    for i, (start, end) in enumerate(zip(closure_points[:-1], closure_points[1:])):
        append_segment(
            out_pos,
            out_rot,
            start,
            end,
            closure_targets[i],
            closure_targets[i + 1],
            closure_frames_per_edge,
            include_first=False,
        )

    positions = np.stack(out_pos, axis=0)
    rotations = np.stack(out_rot, axis=0)
    if len(positions) != frames:
        positions, rotations = resample_path(positions, rotations, frames)
    return positions, rotations


def apply_mode(mode: str, positions: np.ndarray, rotations: np.ndarray,
               target_center: np.ndarray, farther_shrink: float,
               denser_factor: float, target_frames: int):
    if mode == "global_room_closure":
        room_bounds = (
            0.05,
            2.95,
            0.30,
            2.20,
            0.05,
            2.95,
        )
        return build_global_room_closure_path(room_bounds, target_frames)
    if mode == "global_room_closure_local_refine":
        room_bounds = (
            0.05,
            2.95,
            0.30,
            2.20,
            0.05,
            2.95,
        )
        return build_global_room_closure_local_refine_path(room_bounds, target_frames)

    need_farther = mode in {"farther_cam", "farther_plus_denser"}
    need_denser = mode in {"denser_sampling", "farther_plus_denser"}

    out_pos = positions.copy()
    out_rot = rotations.copy()

    if need_farther:
        shrink = np.clip(farther_shrink, 0.05, 1.0)
        out_pos = target_center[None, :] + shrink * (out_pos - target_center[None, :])

    if need_denser:
        desired_frames = target_frames if target_frames > 0 else int(round(len(out_pos) * max(1.0, denser_factor)))
        desired_frames = max(desired_frames, len(out_pos) + 1)
        out_pos, out_rot = resample_path(out_pos, out_rot, desired_frames)

    return out_pos, out_rot


def main():
    args = parse_args()
    mats = load_reference_traj(Path(args.reference_traj))
    positions = np.stack([m[:3, 3] for m in mats], axis=0)
    rotations = np.stack([m[:3, :3] for m in mats], axis=0)

    room_bounds = (
        args.room_min_x,
        args.room_max_x,
        args.room_min_y,
        args.room_max_y,
        args.room_min_z,
        args.room_max_z,
    )
    scale, yaw_deg, G, translated, source_center, target_center = fit_transform(
        positions, room_bounds, args.yaw_step_deg
    )
    translated, rotations = apply_mode(
        args.mode,
        translated,
        np.stack([G @ R for R in rotations], axis=0),
        target_center,
        args.farther_shrink,
        args.denser_factor,
        args.target_frames,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        for i, (pos, R) in enumerate(zip(translated, rotations)):
            timestamp = args.duration * (i / max(1, len(translated) - 1))
            T = np.eye(4, dtype=np.float64)
            T[:3, :3] = R
            T[:3, 3] = pos
            flat = T.reshape(-1, order="F")
            handle.write(
                f"{timestamp:.6f} " + " ".join(f"{v:.6f}" for v in flat.tolist()) + "\n"
            )

    stats = summarize_positions(translated)
    mins = translated.min(axis=0)
    maxs = translated.max(axis=0)
    stats_payload = {
        "mode": args.mode,
        "duration_sec": float(args.duration),
        "scale": float(scale),
        "yaw_deg": float(yaw_deg),
        "source_center": source_center.tolist(),
        "target_center": target_center.tolist(),
        "bbox_min": mins.tolist(),
        "bbox_max": maxs.tolist(),
        "stats": stats,
        "frames_written": int(len(translated)),
        "farther_shrink": float(args.farther_shrink),
        "denser_factor": float(args.denser_factor),
        "target_frames": int(args.target_frames),
        "stage_intervals": build_stage_intervals(args.mode, int(len(translated))),
    }
    stats_out = Path(args.stats_out) if args.stats_out else out_path.with_suffix(".json")
    stats_out.write_text(json.dumps(stats_payload, indent=2, sort_keys=True), encoding="utf-8")
    print("[make_hislam2_replica_like_camera_path] done")
    print(f"output {out_path}")
    print(f"mode {args.mode}")
    print(f"stats_out {stats_out}")
    print(f"scale {scale:.6f}")
    print(f"yaw_deg {yaw_deg:.6f}")
    print("source_center", " ".join(f"{x:.6f}" for x in source_center.tolist()))
    print("target_center", " ".join(f"{x:.6f}" for x in target_center.tolist()))
    print("bbox_min", " ".join(f"{x:.6f}" for x in mins.tolist()))
    print("bbox_max", " ".join(f"{x:.6f}" for x in maxs.tolist()))
    for k, v in stats.items():
        if k == "frames":
            print(k, v)
        else:
            print(k, f"{v:.6f}")


if __name__ == "__main__":
    main()
