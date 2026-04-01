#!/usr/bin/env python3

import argparse
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


STATE_ORDER = {
    "empty": 0,
    "provisional": 1,
    "confirmed": 2,
    "locked": 3,
}


@dataclass
class MetricResult:
    name: str
    passed: bool
    details: Dict[str, object]


def vec_sub(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [float(a[i]) - float(b[i]) for i in range(3)]


def vec_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def vec_cross(a: Sequence[float], b: Sequence[float]) -> List[float]:
    return [
        float(a[1]) * float(b[2]) - float(a[2]) * float(b[1]),
        float(a[2]) * float(b[0]) - float(a[0]) * float(b[2]),
        float(a[0]) * float(b[1]) - float(a[1]) * float(b[0]),
    ]


def vec_norm(a: Sequence[float]) -> float:
    return math.sqrt(vec_dot(a, a))


def vec_normalize(a: Sequence[float]) -> List[float]:
    n = vec_norm(a)
    if n <= 1e-12:
        return [0.0, 0.0, 0.0]
    return [float(v) / n for v in a]


def build_plane_basis(normal: Sequence[float]) -> Tuple[List[float], List[float]]:
    n = vec_normalize(normal)
    ref = [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0]
    u = vec_normalize(vec_cross(ref, n))
    if vec_norm(u) <= 1e-12:
        ref = [0.0, 0.0, 1.0]
        u = vec_normalize(vec_cross(ref, n))
    v = vec_normalize(vec_cross(n, u))
    return u, v


def project_polygon(points: Sequence[Sequence[float]], normal: Sequence[float]) -> List[Tuple[float, float]]:
    origin = points[0]
    u, v = build_plane_basis(normal)
    projected = []
    for p in points:
        d = vec_sub(p, origin)
        projected.append((vec_dot(d, u), vec_dot(d, v)))
    return projected


def polygon_area(poly: Sequence[Tuple[float, float]]) -> float:
    if len(poly) < 3:
        return 0.0
    area = 0.0
    for i, p0 in enumerate(poly):
        p1 = poly[(i + 1) % len(poly)]
        area += p0[0] * p1[1] - p1[0] * p0[1]
    return abs(area) * 0.5


def inside(p: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -1e-9


def line_intersection(
    s: Tuple[float, float],
    e: Tuple[float, float],
    a: Tuple[float, float],
    b: Tuple[float, float],
) -> Tuple[float, float]:
    x1, y1 = s
    x2, y2 = e
    x3, y3 = a
    x4, y4 = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-12:
        return e
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return (px, py)


def convex_clip(
    subject: Sequence[Tuple[float, float]],
    clipper: Sequence[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    output = list(subject)
    for i, cp1 in enumerate(clipper):
        cp2 = clipper[(i + 1) % len(clipper)]
        input_list = output
        output = []
        if not input_list:
            break
        s = input_list[-1]
        for e in input_list:
            if inside(e, cp1, cp2):
                if not inside(s, cp1, cp2):
                    output.append(line_intersection(s, e, cp1, cp2))
                output.append(e)
            elif inside(s, cp1, cp2):
                output.append(line_intersection(s, e, cp1, cp2))
            s = e
    return output


def tile_overlap_ratio(tile_a: Dict[str, object], tile_b: Dict[str, object]) -> Optional[float]:
    corners_a = tile_a.get("corners")
    corners_b = tile_b.get("corners")
    normal_a = tile_a.get("normal")
    normal_b = tile_b.get("normal")
    if not corners_a or not corners_b or not normal_a or not normal_b:
        return None
    avg_normal = vec_normalize([normal_a[i] + normal_b[i] for i in range(3)])
    poly_a = project_polygon(corners_a, avg_normal)
    poly_b = project_polygon(corners_b, avg_normal)
    area_a = polygon_area(poly_a)
    area_b = polygon_area(poly_b)
    if area_a <= 1e-9 or area_b <= 1e-9:
        return 0.0
    inter = convex_clip(poly_a, poly_b)
    inter_area = polygon_area(inter)
    return inter_area / min(area_a, area_b)


def iter_active_tiles(frame: Dict[str, object]) -> Iterable[Dict[str, object]]:
    for tile in frame.get("tiles", []):
        if tile.get("state") == "empty":
            continue
        yield tile


def evaluate_q2(frames: Sequence[Dict[str, object]], pass_rate_threshold: float = 0.8) -> MetricResult:
    total = 0
    passed = 0
    for frame in frames:
        for tile in iter_active_tiles(frame):
            support = tile.get("surface_corner_support")
            if support is None:
                continue
            total += 1
            if sum(1 for x in support if x) >= 3:
                passed += 1
    rate = float(passed) / total if total else 0.0
    return MetricResult(
        "Q2",
        bool(total) and rate >= pass_rate_threshold,
        {"tiles_with_support": total, "passing_tiles": passed, "pass_rate": rate},
    )


def evaluate_q3(frames: Sequence[Dict[str, object]]) -> MetricResult:
    duplicates = 0
    duplicate_frames = 0
    for frame in frames:
        counts = defaultdict(int)
        for tile in iter_active_tiles(frame):
            cell_id = tile.get("cell_id")
            if cell_id is not None:
                counts[str(cell_id)] += 1
        frame_dups = sum(v - 1 for v in counts.values() if v > 1)
        if frame_dups:
            duplicate_frames += 1
            duplicates += frame_dups
    return MetricResult(
        "Q3",
        duplicates == 0,
        {"duplicate_cells": duplicates, "frames_with_duplicates": duplicate_frames},
    )


def evaluate_q4(frames: Sequence[Dict[str, object]], overlap_threshold: float = 0.01) -> MetricResult:
    checked = 0
    violations = 0
    max_overlap = 0.0
    for frame in frames:
        tiles = list(iter_active_tiles(frame))
        by_id = {str(tile.get("tile_id")): tile for tile in tiles if tile.get("tile_id") is not None}
        seen_pairs = set()
        for tile in tiles:
            tid = str(tile.get("tile_id"))
            neighbors = tile.get("neighbors", [])
            if neighbors:
                for nbr in neighbors:
                    other_id = str(nbr.get("tile_id"))
                    if other_id not in by_id:
                        continue
                    key = tuple(sorted((tid, other_id)))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    ratio = tile_overlap_ratio(tile, by_id[other_id])
                    if ratio is None:
                        continue
                    checked += 1
                    max_overlap = max(max_overlap, ratio)
                    if ratio > overlap_threshold:
                        violations += 1
            else:
                # Fallback: brute-force within frame when no neighbor graph is present.
                for other in tiles:
                    oid = str(other.get("tile_id"))
                    if oid <= tid:
                        continue
                    key = (tid, oid)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    ratio = tile_overlap_ratio(tile, other)
                    if ratio is None:
                        continue
                    checked += 1
                    max_overlap = max(max_overlap, ratio)
                    if ratio > overlap_threshold:
                        violations += 1
    return MetricResult(
        "Q4",
        checked > 0 and violations == 0,
        {"checked_pairs": checked, "violating_pairs": violations, "max_overlap_ratio": max_overlap},
    )


def evaluate_q5(frames: Sequence[Dict[str, object]], regular_pair_threshold: float = 0.85) -> MetricResult:
    pair_regular = 0
    pair_total = 0
    duplicate_uv = 0
    spacing_samples = []
    for frame in frames:
        tiles = [t for t in iter_active_tiles(frame) if t.get("u") is not None and t.get("v") is not None]
        uv_seen = set()
        tile_map = {}
        for tile in tiles:
            key = (int(tile["u"]), int(tile["v"]))
            if key in uv_seen:
                duplicate_uv += 1
            uv_seen.add(key)
            tile_map[key] = tile
        horiz = []
        vert = []
        for (u, v), tile in tile_map.items():
            for du, dv, bucket in ((1, 0, horiz), (0, 1, vert)):
                other = tile_map.get((u + du, v + dv))
                if not other:
                    continue
                d = vec_norm(vec_sub(tile["center"], other["center"]))
                bucket.append(d)
        for bucket in (horiz, vert):
            if bucket:
                spacing_samples.extend(bucket)
                median_spacing = statistics.median(bucket)
                for d in bucket:
                    pair_total += 1
                    if median_spacing > 1e-9 and abs(d - median_spacing) / median_spacing <= 0.2:
                        pair_regular += 1
    regular_rate = float(pair_regular) / pair_total if pair_total else 0.0
    spacing_stddev = statistics.pstdev(spacing_samples) if len(spacing_samples) > 1 else 0.0
    return MetricResult(
        "Q5",
        duplicate_uv == 0 and pair_total > 0 and regular_rate >= regular_pair_threshold,
        {
            "duplicate_uv": duplicate_uv,
            "regular_pair_rate": regular_rate,
            "regular_pairs": pair_regular,
            "pair_total": pair_total,
            "spacing_stddev": spacing_stddev,
        },
    )


def evaluate_p1(frames: Sequence[Dict[str, object]]) -> MetricResult:
    regressions = 0
    tile_last_state = {}
    for frame in frames:
        for tile in frame.get("tiles", []):
            tid = str(tile.get("tile_id"))
            state = str(tile.get("state"))
            last = tile_last_state.get(tid)
            if last is not None and STATE_ORDER.get(state, -1) < STATE_ORDER.get(last, -1):
                if STATE_ORDER.get(last, -1) >= STATE_ORDER["confirmed"]:
                    regressions += 1
            tile_last_state[tid] = state
    return MetricResult("P1", regressions == 0, {"state_regressions": regressions})


def count_toggles(seq: Sequence[bool]) -> int:
    toggles = 0
    for i in range(1, len(seq)):
        if seq[i] != seq[i - 1]:
            toggles += 1
    return toggles


def evaluate_p3(frames: Sequence[Dict[str, object]], window: int = 30) -> MetricResult:
    visibility_history = defaultdict(list)
    state_history = defaultdict(list)
    for frame in frames:
        tiles_by_id = {str(tile.get("tile_id")): tile for tile in frame.get("tiles", []) if tile.get("tile_id") is not None}
        all_ids = set(visibility_history.keys()) | set(tiles_by_id.keys())
        for tid in all_ids:
            tile = tiles_by_id.get(tid)
            if tile is None:
                visibility_history[tid].append(False)
                state_history[tid].append("empty")
                continue
            visibility_history[tid].append(bool(tile.get("visible", False)))
            state_history[tid].append(str(tile.get("state")))
    failures = 0
    candidates = 0
    for tid, seq in visibility_history.items():
        states = state_history[tid]
        if not any(s in ("confirmed", "locked") for s in states):
            continue
        candidates += 1
        for start in range(0, max(1, len(seq) - window + 1)):
            end = min(len(seq), start + window)
            if count_toggles(seq[start:end]) > 1:
                failures += 1
                break
    return MetricResult(
        "P3",
        candidates > 0 and failures == 0,
        {"candidate_tiles": candidates, "flicker_fail_tiles": failures},
    )


def evaluate_v1(frames: Sequence[Dict[str, object]], epsilon: float = 1e-6) -> MetricResult:
    regressions = 0
    last = None
    for frame in frames:
        current = float(frame.get("coverage", 0.0))
        if last is not None and current + epsilon < last:
            regressions += 1
        last = current
    return MetricResult("V1", regressions == 0, {"coverage_regressions": regressions})


def evaluate_v2(frames: Sequence[Dict[str, object]], gap_stddev_threshold_mm: float = 2.0) -> MetricResult:
    gaps = []
    for frame in frames:
        for tile in iter_active_tiles(frame):
            for nbr in tile.get("neighbors", []):
                if "gap_mm" in nbr:
                    gaps.append(float(nbr["gap_mm"]))
    stddev = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
    return MetricResult(
        "V2",
        len(gaps) > 0 and stddev <= gap_stddev_threshold_mm,
        {"gap_count": len(gaps), "gap_stddev_mm": stddev},
    )


def evaluate_optional_q1(frames: Sequence[Dict[str, object]], distance_mm: float = 20.0, normal_dot: float = 0.70) -> Optional[MetricResult]:
    total = 0
    passed = 0
    for frame in frames:
        for tile in iter_active_tiles(frame):
            if "surface_center_distance_mm" not in tile or "surface_normal_dot" not in tile:
                continue
            total += 1
            if float(tile["surface_center_distance_mm"]) < distance_mm and float(tile["surface_normal_dot"]) > normal_dot:
                passed += 1
    if total == 0:
        return None
    rate = float(passed) / total
    return MetricResult("Q1", rate >= 0.8, {"tiles_checked": total, "pass_rate": rate})


def evaluate(frames: Sequence[Dict[str, object]]) -> List[MetricResult]:
    results = []
    q1 = evaluate_optional_q1(frames)
    if q1 is not None:
        results.append(q1)
    results.extend(
        [
            evaluate_q2(frames),
            evaluate_q3(frames),
            evaluate_q4(frames),
            evaluate_q5(frames),
            evaluate_p1(frames),
            evaluate_p3(frames),
            evaluate_v1(frames),
            evaluate_v2(frames),
        ]
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate product-aligned tile world-state white-box checkpoints.")
    parser.add_argument("input", type=Path, help="Path to world-state JSON")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    data = json.loads(args.input.read_text())
    frames = data.get("frames", [])
    results = evaluate(frames)

    if args.json:
        payload = {
            "version": data.get("version", "unknown"),
            "frame_count": len(frames),
            "results": [
                {"name": r.name, "passed": r.passed, "details": r.details}
                for r in results
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"FRAME_COUNT {len(frames)}")
    for result in results:
        print(f"{result.name} {'PASS' if result.passed else 'FAIL'}")
        for key, value in sorted(result.details.items()):
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
