#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--prep-root", required=True)
    p.add_argument("--extract-if-missing", action="store_true")
    p.add_argument("--hfov-deg", type=float, default=62.0)
    p.add_argument("--run-colmap", action="store_true")
    p.add_argument("--colmap-binary", default="colmap")
    p.add_argument("--colmap-frame-stride", type=int, default=4)
    p.add_argument("--colmap-max-frames", type=int, default=600)
    p.add_argument(
        "--colmap-matcher",
        choices=("sequential", "exhaustive"),
        default="sequential",
    )
    p.add_argument("--colmap-use-gpu", action="store_true")
    p.add_argument("--status-json")
    p.add_argument("--reuse-live-sfm-if-ready", action="store_true")
    return p.parse_args()


def parse_frame_index(path: Path) -> int | None:
    stem = path.stem
    prefix = stem.split("_", 1)[0]
    if not prefix.isdigit():
        return None
    try:
        return int(prefix)
    except ValueError:
        return None


def list_final_jpgs(images_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in images_dir.glob("*.jpg")
        if not path.name.endswith(".tmp.jpg")
    )


def summarize_frame_inventory(image_paths: list[Path]) -> dict[str, int | bool]:
    indices: list[int] = []
    for path in image_paths:
        frame_index = parse_frame_index(path)
        if frame_index is None:
            continue
        indices.append(frame_index)

    if not indices:
        return {
            "count": 0,
            "indexed_count": 0,
            "first_index": -1,
            "last_index": -1,
            "contiguous_from_zero": False,
            "missing_slots": 0,
        }

    indices.sort()
    first_index = indices[0]
    last_index = indices[-1]
    missing_slots = 0
    previous = indices[0]
    for frame_index in indices[1:]:
        if frame_index > previous + 1:
            missing_slots += frame_index - previous - 1
        previous = frame_index

    return {
        "count": len(image_paths),
        "indexed_count": len(indices),
        "first_index": first_index,
        "last_index": last_index,
        "contiguous_from_zero": first_index == 0 and len(indices) == last_index + 1,
        "missing_slots": missing_slots,
    }


def next_missing_frame_index(images_dir: Path) -> int:
    image_paths = list_final_jpgs(images_dir)
    if not image_paths:
        return 0
    highest = -1
    for path in image_paths:
        frame_index = parse_frame_index(path)
        if frame_index is None:
            continue
        highest = max(highest, frame_index)
    return highest + 1


def extract_all_frames(video_path: Path, images_dir: Path, fps: float, progress_callback=None, *, start_index: int = 0):
    images_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed opening video: {video_path}")
    idx = max(int(start_index), 0)
    if idx > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))
    last_emit = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        timestamp = idx / max(fps, 1e-9)
        out = images_dir / f"{idx:06d}_{timestamp:.6f}.jpg"
        tmp_out = out.with_name(f"{out.stem}.tmp{out.suffix}")
        if not cv2.imwrite(str(tmp_out), frame):
            raise RuntimeError(f"failed writing frame {tmp_out}")
        tmp_out.replace(out)
        idx += 1
        if progress_callback is not None:
            now = time.time()
            if idx <= 4 or now - last_emit >= 1.0:
                progress_callback(idx)
                last_emit = now
    cap.release()
    if progress_callback is not None:
        progress_callback(idx)
    return idx


def rename_images_with_timestamps(images_dir: Path, fps: float):
    image_paths = list_final_jpgs(images_dir)
    if not image_paths:
        raise RuntimeError(f"no jpg frames found in {images_dir}")

    renamed = 0
    for idx, path in enumerate(image_paths):
        if "_" in path.stem:
            continue
        timestamp = idx / max(fps, 1e-9)
        target = path.with_name(f"{idx:06d}_{timestamp:.6f}.jpg")
        path.rename(target)
        renamed += 1

    final_paths = list_final_jpgs(images_dir)
    return final_paths, renamed


def write_heuristic_calib(calib_path: Path, width: int, height: int, hfov_deg: float):
    hfov_rad = hfov_deg * 3.141592653589793 / 180.0
    fx = 0.5 * width / max(__import__("math").tan(0.5 * hfov_rad), 1e-9)
    fy = fx
    cx = width * 0.5
    cy = height * 0.5
    calib_path.write_text(f"{fx:.6f} {fy:.6f} {cx:.6f} {cy:.6f}\n", encoding="utf-8")
    return fx, fy, cx, cy


def write_status(status_path: Path | None, payload: dict):
    if status_path is None:
        return
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_suffix(status_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(status_path)


def colmap_qvec_to_rotation_matrix(qw: float, qx: float, qy: float, qz: float) -> list[list[float]]:
    norm = (qw * qw + qx * qx + qy * qy + qz * qz) ** 0.5
    if norm <= 1e-12:
        return [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    qw /= norm
    qx /= norm
    qy /= norm
    qz /= norm
    return [
        [
            1.0 - 2.0 * (qy * qy + qz * qz),
            2.0 * (qx * qy - qz * qw),
            2.0 * (qx * qz + qy * qw),
        ],
        [
            2.0 * (qx * qy + qz * qw),
            1.0 - 2.0 * (qx * qx + qz * qz),
            2.0 * (qy * qz - qx * qw),
        ],
        [
            2.0 * (qx * qz - qy * qw),
            2.0 * (qy * qz + qx * qw),
            1.0 - 2.0 * (qx * qx + qy * qy),
        ],
    ]


def transpose3x3(m: list[list[float]]) -> list[list[float]]:
    return [
        [m[0][0], m[1][0], m[2][0]],
        [m[0][1], m[1][1], m[2][1]],
        [m[0][2], m[1][2], m[2][2]],
    ]


def mat3_vec_mul(m: list[list[float]], v: list[float]) -> list[float]:
    return [
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    ]


def intrinsics_from_colmap_model(model: str, params: list[float]) -> tuple[float, float, float, float] | None:
    if model == "OPENCV" and len(params) >= 4:
        return float(params[0]), float(params[1]), float(params[2]), float(params[3])
    if model == "PINHOLE" and len(params) >= 4:
        return float(params[0]), float(params[1]), float(params[2]), float(params[3])
    if model in {"SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"} and len(params) >= 3:
        focal = float(params[0])
        return focal, focal, float(params[1]), float(params[2])
    return None


def write_cameras_json_from_sparse_txt(prep_root: Path) -> int:
    sparse_txt_dir = prep_root / "sparse_txt"
    cameras_txt = sparse_txt_dir / "cameras.txt"
    images_txt = sparse_txt_dir / "images.txt"
    output_path = prep_root / "cameras.json"

    if not cameras_txt.is_file() or not images_txt.is_file():
        return 0

    camera_entries: dict[int, dict[str, object]] = {}
    for raw_line in cameras_txt.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            camera_id = int(parts[0])
            model = parts[1]
            width = int(parts[2])
            height = int(parts[3])
            params = [float(x) for x in parts[4:]]
        except Exception:
            continue
        intrinsics = intrinsics_from_colmap_model(model, params)
        if intrinsics is None:
            continue
        fx, fy, cx, cy = intrinsics
        camera_entries[camera_id] = {
            "width": width,
            "height": height,
            "K": [
                [fx, 0.0, cx],
                [0.0, fy, cy],
                [0.0, 0.0, 1.0],
            ],
        }

    frames: list[dict[str, object]] = []
    image_lines = [
        line.strip()
        for line in images_txt.read_text(encoding="utf-8", errors="ignore").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for index in range(0, len(image_lines), 2):
        parts = image_lines[index].split()
        if len(parts) < 10:
            continue
        try:
            qw, qx, qy, qz = [float(x) for x in parts[1:5]]
            tx, ty, tz = [float(x) for x in parts[5:8]]
            camera_id = int(parts[8])
            image_name = parts[9]
        except Exception:
            continue
        camera_meta = camera_entries.get(camera_id)
        if camera_meta is None:
            continue

        r_w2c = colmap_qvec_to_rotation_matrix(qw, qx, qy, qz)
        r_c2w = transpose3x3(r_w2c)
        camera_center = mat3_vec_mul(r_c2w, [-tx, -ty, -tz])

        frames.append(
            {
                "image_name": image_name,
                "width": int(camera_meta["width"]),
                "height": int(camera_meta["height"]),
                "R": r_c2w,
                "T": camera_center,
                "K": camera_meta["K"],
            }
        )

    if not frames:
        return 0

    output_path.write_text(
        json.dumps({"cameras": frames}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return len(frames)


def build_attempt_estimate(profile: dict, selected_frames: int, matcher: str) -> dict:
    max_image_size = max(int(profile["max_image_size"]), 640)
    extract_threads = max(int(profile["extract_threads"]), 1)
    size_factor = max_image_size / 1280.0
    thread_factor = extract_threads ** 0.5
    exhaustive = matcher == "exhaustive"

    extract_sec = 24 + (selected_frames * 0.45 * size_factor) / max(thread_factor, 1.0)
    match_sec = (20 if exhaustive else 12) + selected_frames * (0.18 if exhaustive else 0.09)
    mapper_sec = 28 + selected_frames * 0.22
    bundle_sec = 10 + selected_frames * 0.05
    convert_sec = 4

    return {
        "feature_extractor": int(round(extract_sec)),
        "matcher": int(round(match_sec)),
        "mapper": int(round(mapper_sec)),
        "bundle_adjuster": int(round(bundle_sec)),
        "model_converter": int(round(convert_sec)),
    }


def estimate_remaining_budget(
    profile_rounds: list[dict],
    round_index: int,
    profile_index: int,
    matcher: str,
    current_selected_frames: int,
    current_phase_remaining: int,
) -> int:
    remaining = max(int(current_phase_remaining), 0)
    for next_profile_index in range(profile_index + 1, len(profile_rounds[round_index]["profiles"])):
        profile = profile_rounds[round_index]["profiles"][next_profile_index]
        remaining += sum(build_attempt_estimate(profile, current_selected_frames, matcher).values())

    for next_round_index in range(round_index + 1, len(profile_rounds)):
        round_cfg = profile_rounds[next_round_index]
        estimated_frames = max(20, int(round_cfg["max_frames"]))
        for profile in round_cfg["profiles"]:
            remaining += sum(build_attempt_estimate(profile, estimated_frames, matcher).values())

    return remaining


def run_checked(cmd, *, cwd=None):
    subprocess.run(cmd, check=True, cwd=cwd)


def clamp_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(1.0, float(value)))


def estimate_phase_remaining_from_ratio(elapsed_sec: int, ratio: float | None, fallback_sec: int) -> int:
    ratio = clamp_ratio(ratio)
    if ratio is None or ratio <= 0.0 or ratio >= 1.0 or elapsed_sec <= 0:
        return max(0, int(fallback_sec))
    return max(0, int(round(elapsed_sec * (1.0 - ratio) / max(ratio, 1e-6))))


def query_sqlite_count(db_path: Path, query: str) -> int:
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path), timeout=1) as conn:
            conn.execute("PRAGMA query_only = 1")
            row = conn.execute(query).fetchone()
            if not row:
                return 0
            return int(row[0] or 0)
    except Exception:
        return 0


def count_descriptor_images(db_path: Path) -> int:
    return query_sqlite_count(db_path, "SELECT COUNT(*) FROM descriptors")


def count_matched_pairs(db_path: Path) -> int:
    count = query_sqlite_count(db_path, "SELECT COUNT(*) FROM two_view_geometries")
    if count > 0:
        return count
    return query_sqlite_count(db_path, "SELECT COUNT(*) FROM matches")


def expected_match_pairs(frame_count: int, matcher_mode: str) -> int:
    if frame_count <= 1:
        return 1
    if matcher_mode == "exhaustive":
        return max(1, frame_count * (frame_count - 1) // 2)
    # sequential matcher does not match all pairs; keep expectation conservative
    return max(1, min(frame_count * 6, frame_count * (frame_count - 1) // 2))


def tail_text(path: Path, max_bytes: int = 256 * 1024) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            return handle.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


def parse_matcher_block_status(log_path: Path) -> dict:
    text = tail_text(log_path)
    matches = list(
        re.finditer(
            r"Matching block \[(\d+)/(\d+),\s*(\d+)/(\d+)\]",
            text,
            re.IGNORECASE,
        )
    )
    if not matches:
        return {}

    match = matches[-1]
    row_index = int(match.group(1))
    row_total = max(1, int(match.group(2)))
    col_index = int(match.group(3))
    col_total = max(1, int(match.group(4)))
    block_total = max(1, row_total * col_total)
    block_index = min(block_total, (row_index - 1) * col_total + col_index)

    line_start = text.rfind("\n", 0, match.start())
    line_start = 0 if line_start < 0 else line_start + 1
    prefix = text[line_start:match.start()]
    timestamp_match = re.search(
        r"I(\d{4})(\d{2})(\d{2})\s+(\d{2}):(\d{2}):(\d{2})",
        prefix,
    )
    block_started_at_epoch = None
    block_elapsed_sec = None
    if timestamp_match:
        try:
            started_struct = time.strptime(
                (
                    f"{timestamp_match.group(1)}-{timestamp_match.group(2)}-{timestamp_match.group(3)} "
                    f"{timestamp_match.group(4)}:{timestamp_match.group(5)}:{timestamp_match.group(6)}"
                ),
                "%Y-%m-%d %H:%M:%S",
            )
            block_started_at_epoch = int(time.mktime(started_struct))
            block_elapsed_sec = max(0, int(time.time()) - block_started_at_epoch)
        except Exception:
            block_started_at_epoch = None
            block_elapsed_sec = None

    return {
        "matcher_block_index": block_index,
        "matcher_block_total": block_total,
        "matcher_block_row_index": row_index,
        "matcher_block_row_total": row_total,
        "matcher_block_col_index": col_index,
        "matcher_block_col_total": col_total,
        "matcher_block_started_at_epoch": block_started_at_epoch,
        "matcher_block_elapsed_sec": block_elapsed_sec,
    }


def count_registered_images_from_log(log_path: Path) -> int:
    text = tail_text(log_path)
    best = 0
    registered_ids: set[int] = set()

    for match in re.finditer(r"Registered images:\s*(\d+)", text, re.IGNORECASE):
        try:
            best = max(best, int(match.group(1)))
        except Exception:
            pass

    for match in re.finditer(r"Registering image #(\d+)", text, re.IGNORECASE):
        try:
            registered_ids.add(int(match.group(1)))
        except Exception:
            pass

    return max(best, len(registered_ids))


def numeric_progress_probe(
    *,
    units_done: int,
    units_total: int,
    unit_label: str,
    action_label: str,
    progress_basis: str,
) -> dict:
    done = max(0, int(units_done))
    total = max(1, int(units_total))
    if done > total:
        return {
            "ratio": 1.0,
            "units_done": done,
            "units_total": done,
            "detail_suffix": f"已{action_label}{done} {unit_label}（已超过预估 {total}）",
            "progress_basis": progress_basis,
        }
    return {
        "ratio": done / max(total, 1),
        "units_done": done,
        "units_total": total,
        "detail_suffix": f"{done}/{total} {unit_label}已{action_label}",
        "progress_basis": progress_basis,
    }


def matcher_progress_probe(*, db_path: Path, log_path: Path, units_total: int) -> dict:
    base = numeric_progress_probe(
        units_done=count_matched_pairs(db_path),
        units_total=units_total,
        unit_label="组视角",
        action_label="匹配",
        progress_basis="prep_match_pairs",
    )
    block = parse_matcher_block_status(log_path)
    if not block:
        return base

    block_index = block.get("matcher_block_index")
    block_total = block.get("matcher_block_total")
    block_elapsed_sec = block.get("matcher_block_elapsed_sec")
    extra_parts: list[str] = []
    if isinstance(block_index, int) and isinstance(block_total, int) and block_total > 0:
        extra_parts.append(f"第 {block_index}/{block_total} 个匹配块")
    if isinstance(block_elapsed_sec, int):
        extra_parts.append(f"本块已运行 {block_elapsed_sec} 秒")

    if extra_parts:
        detail_suffix = str(base.get("detail_suffix") or "")
        base["detail_suffix"] = f"{detail_suffix}，{'，'.join(extra_parts)}"

    base["extra_metrics"] = {
        key: value for key, value in block.items() if value is not None
    }
    return base


def stream_process_to_logs(process: subprocess.Popen, log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8", errors="ignore")

    def pump():
        try:
            assert process.stdout is not None
            for line in process.stdout:
                handle.write(line)
                handle.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
        finally:
            handle.close()

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    return thread


def prepare_colmap_subset(images_dir: Path, subset_dir: Path, stride: int, max_frames: int):
    subset_dir.mkdir(parents=True, exist_ok=True)
    for path in subset_dir.glob("*"):
        path.unlink()

    candidates = [
        src
        for idx, src in enumerate(list_final_jpgs(images_dir))
        if idx % max(stride, 1) == 0
    ]
    if len(candidates) > max_frames:
        step = (len(candidates) - 1) / max(max_frames - 1, 1)
        picked = []
        seen = set()
        for i in range(max_frames):
            idx = int(round(i * step))
            idx = min(idx, len(candidates) - 1)
            if idx in seen:
                continue
            seen.add(idx)
            picked.append(candidates[idx])
        selected = picked
    else:
        selected = candidates

    if len(selected) < 20:
        raise RuntimeError(
            f"too few frames selected for COLMAP: {len(selected)} from {images_dir}"
        )

    for src in selected:
        shutil.copy2(src, subset_dir / src.name)

    return len(selected)


def run_colmap(prep_root: Path, args):
    colmap_binary = shutil.which(args.colmap_binary) or args.colmap_binary
    images_dir = prep_root / "images"
    subset_dir = prep_root / "images_colmap"
    db_path = prep_root / "colmap.db"
    sparse_dir = prep_root / "sparse"
    sparse0_dir = sparse_dir / "0"
    sparse_txt_dir = prep_root / "sparse_txt"

    matcher_mode = "sequential" if args.colmap_matcher == "sequential" else "exhaustive"
    base_max_frames = max(int(args.colmap_max_frames), 20)
    base_stride = max(int(args.colmap_frame_stride), 1)
    status_path = Path(args.status_json) if args.status_json else prep_root / "runtime_status.json"
    run_started_at = int(time.time())

    profile_rounds = [
        {
            "label": "safe_default",
            "stride": base_stride,
            "max_frames": base_max_frames,
            "profiles": [
                {
                    "name": "safe_1600",
                    "max_image_size": "1600",
                    "extract_threads": "4",
                    "match_threads": "4",
                    "mapper_threads": "4",
                    "estimate_affine_shape": "false",
                    "domain_size_pooling": "false",
                    "max_num_features": "8192",
                },
                {
                    "name": "safe_1280",
                    "max_image_size": "1280",
                    "extract_threads": "2",
                    "match_threads": "2",
                    "mapper_threads": "2",
                    "estimate_affine_shape": "false",
                    "domain_size_pooling": "false",
                    "max_num_features": "6144",
                },
            ],
        },
        {
            "label": "reduced_frames",
            "stride": max(base_stride, 2),
            "max_frames": max(32, min(base_max_frames // 2, 80)),
            "profiles": [
                {
                    "name": "safer_1024",
                    "max_image_size": "1024",
                    "extract_threads": "2",
                    "match_threads": "2",
                    "mapper_threads": "2",
                    "estimate_affine_shape": "false",
                    "domain_size_pooling": "false",
                    "max_num_features": "4096",
                },
            ],
        },
        {
            "label": "aggressive_rescue",
            "stride": max(base_stride, 3),
            "max_frames": max(24, min(base_max_frames // 3, 48)),
            "profiles": [
                {
                    "name": "tiny_960",
                    "max_image_size": "960",
                    "extract_threads": "1",
                    "match_threads": "1",
                    "mapper_threads": "1",
                    "estimate_affine_shape": "false",
                    "domain_size_pooling": "false",
                    "max_num_features": "3072",
                },
            ],
        },
    ]

    total_attempts = sum(len(round_cfg["profiles"]) for round_cfg in profile_rounds)
    completed_attempts = 0
    last_error = None
    active_profile = None
    active_round = None
    selected_frames = 0

    def write_runtime_phase(
        *,
        stage: str,
        detail: str,
        round_cfg: dict,
        round_index: int,
        profile: dict,
        profile_index: int,
        attempt_estimates: dict,
        phase_name: str,
        phase_progress_start: float,
        phase_progress_end: float,
        phase_budget_sec: int,
        phase_started_at: int,
        observed_ratio: float | None = None,
        units_done: int | None = None,
        units_total: int | None = None,
        detail_suffix: str | None = None,
        progress_basis: str = "prep_runtime_budget",
        explicit_remaining_sec: int | None = None,
        state: str = "processing",
        extra_metrics: dict | None = None,
    ):
        now = int(time.time())
        phase_elapsed = max(0, now - phase_started_at)
        ratio = clamp_ratio(observed_ratio)
        if ratio is not None:
            progress_value = phase_progress_start + (phase_progress_end - phase_progress_start) * ratio
        else:
            progress_value = phase_progress_start

        current_phase_remaining = estimate_phase_remaining_from_ratio(
            phase_elapsed,
            ratio,
            max(0, phase_budget_sec - phase_elapsed),
        )
        estimated_remaining_sec = explicit_remaining_sec
        if estimated_remaining_sec is None:
            estimated_remaining_sec = estimate_remaining_budget(
                profile_rounds,
                round_index,
                profile_index,
                matcher_mode,
                selected_frames,
                current_phase_remaining,
            )

        payload = {
            "state": state,
            "stage": stage,
            "detail": detail if not detail_suffix else f"{detail}（{detail_suffix}）",
            "progress_basis": progress_basis,
            "progress": progress_value,
            "progress_start": phase_progress_start,
            "progress_end": phase_progress_end,
            "phase_name": phase_name,
            "phase_budget_sec": phase_budget_sec,
            "phase_started_at_epoch": phase_started_at,
            "run_started_at_epoch": run_started_at,
            "elapsed_sec": max(0, now - run_started_at),
            "estimated_remaining_sec": max(0, int(estimated_remaining_sec)),
            "current_round": round_cfg["label"],
            "current_profile": profile["name"],
            "round_index": round_index + 1,
            "round_count": len(profile_rounds),
            "profile_index": completed_attempts + profile_index + 1,
            "profile_count": total_attempts,
            "selected_frames": selected_frames,
            "extracted_frames": len(list_final_jpgs(images_dir)),
            "matcher": matcher_mode,
            "use_gpu": args.colmap_use_gpu,
            "max_image_size": int(profile["max_image_size"]),
            "estimate_affine_shape": profile["estimate_affine_shape"] == "true",
            "domain_size_pooling": profile["domain_size_pooling"] == "true",
            "attempt_estimates": attempt_estimates,
        }
        if units_done is not None:
            payload["current_units"] = int(units_done)
        if units_total is not None:
            payload["target_units"] = int(units_total)
        if extra_metrics:
            for key, value in extra_metrics.items():
                if value is None:
                    continue
                payload[key] = value
        write_status(status_path, payload)

    def run_monitored_phase(
        *,
        cmd: list[str],
        stage: str,
        detail: str,
        round_cfg: dict,
        round_index: int,
        profile: dict,
        profile_index: int,
        attempt_estimates: dict,
        phase_name: str,
        phase_progress_start: float,
        phase_progress_end: float,
        phase_budget_sec: int,
        log_path: Path,
        progress_probe,
    ):
        phase_started_at = int(time.time())
        write_runtime_phase(
            stage=stage,
            detail=detail,
            round_cfg=round_cfg,
            round_index=round_index,
            profile=profile,
            profile_index=profile_index,
            attempt_estimates=attempt_estimates,
            phase_name=phase_name,
            phase_progress_start=phase_progress_start,
            phase_progress_end=phase_progress_end,
            phase_budget_sec=phase_budget_sec,
            phase_started_at=phase_started_at,
        )

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        pump_thread = stream_process_to_logs(process, log_path)

        last_emit = 0.0
        while True:
            code = process.poll()
            now = time.time()
            if now - last_emit >= 1.0:
                probe = progress_probe()
                ratio = probe.get("ratio")
                units_done = probe.get("units_done")
                units_total = probe.get("units_total")
                detail_suffix = probe.get("detail_suffix")
                basis = probe.get("progress_basis") or "prep_runtime_budget"
                extra_metrics = probe.get("extra_metrics")
                write_runtime_phase(
                    stage=stage,
                    detail=detail,
                    round_cfg=round_cfg,
                    round_index=round_index,
                    profile=profile,
                    profile_index=profile_index,
                    attempt_estimates=attempt_estimates,
                    phase_name=phase_name,
                    phase_progress_start=phase_progress_start,
                    phase_progress_end=phase_progress_end,
                    phase_budget_sec=phase_budget_sec,
                    phase_started_at=phase_started_at,
                    observed_ratio=ratio,
                    units_done=units_done,
                    units_total=units_total,
                    detail_suffix=detail_suffix,
                    progress_basis=basis,
                    extra_metrics=extra_metrics if isinstance(extra_metrics, dict) else None,
                )
                last_emit = now
            if code is not None:
                break
            time.sleep(0.5)

        pump_thread.join(timeout=2)
        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)

        write_runtime_phase(
            stage=stage,
            detail=detail,
            round_cfg=round_cfg,
            round_index=round_index,
            profile=profile,
            profile_index=profile_index,
            attempt_estimates=attempt_estimates,
            phase_name=phase_name,
            phase_progress_start=phase_progress_start,
            phase_progress_end=phase_progress_end,
            phase_budget_sec=phase_budget_sec,
            phase_started_at=phase_started_at,
            observed_ratio=1.0,
            progress_basis="prep_phase_complete",
        )

    for round_index, round_cfg in enumerate(profile_rounds):
        selected_frames = prepare_colmap_subset(
            images_dir,
            subset_dir,
            stride=round_cfg["stride"],
            max_frames=round_cfg["max_frames"],
        )
        print(
            f"[colmap-round] trying {round_cfg['label']} stride={round_cfg['stride']} max_frames={round_cfg['max_frames']} selected={selected_frames}",
            flush=True,
        )

        for profile_index, profile in enumerate(round_cfg["profiles"]):
            active_round = round_cfg["label"]
            active_profile = profile["name"]

            if db_path.exists():
                db_path.unlink()
            shutil.rmtree(sparse_dir, ignore_errors=True)
            shutil.rmtree(sparse_txt_dir, ignore_errors=True)
            sparse_dir.mkdir(parents=True, exist_ok=True)
            sparse_txt_dir.mkdir(parents=True, exist_ok=True)

            matcher = "sequential_matcher" if matcher_mode == "sequential" else "exhaustive_matcher"
            attempt_estimates = build_attempt_estimate(profile, selected_frames, matcher_mode)
            profile_progress_base = 24.0 + (completed_attempts / max(total_attempts, 1)) * 18.0
            profile_span = 18.0 / max(total_attempts, 1)
            feature_start = profile_progress_base
            match_start = profile_progress_base + profile_span * 0.45
            mapper_start = profile_progress_base + profile_span * 0.65
            bundle_start = profile_progress_base + profile_span * 0.85
            convert_start = profile_progress_base + profile_span * 0.94
            profile_end = profile_progress_base + profile_span

            try:
                feature_log = prep_root / f"{round_cfg['label']}_{profile['name']}_feature_extractor.log"
                match_log = prep_root / f"{round_cfg['label']}_{profile['name']}_matcher.log"
                mapper_log = prep_root / f"{round_cfg['label']}_{profile['name']}_mapper.log"
                bundle_log = prep_root / f"{round_cfg['label']}_{profile['name']}_bundle_adjuster.log"
                convert_log = prep_root / f"{round_cfg['label']}_{profile['name']}_model_converter.log"

                feature_cmd = [
                    colmap_binary,
                    "feature_extractor",
                    "--ImageReader.camera_model",
                    "OPENCV",
                    "--ImageReader.single_camera",
                    "1",
                    "--SiftExtraction.use_gpu",
                    "1" if args.colmap_use_gpu else "0",
                    "--SiftExtraction.estimate_affine_shape",
                    profile["estimate_affine_shape"],
                    "--SiftExtraction.domain_size_pooling",
                    profile["domain_size_pooling"],
                    "--SiftExtraction.max_image_size",
                    profile["max_image_size"],
                    "--SiftExtraction.max_num_features",
                    profile["max_num_features"],
                    "--SiftExtraction.num_threads",
                    profile["extract_threads"],
                    "--database_path",
                    str(db_path),
                    "--image_path",
                    str(subset_dir),
                ]
                run_monitored_phase(
                    cmd=feature_cmd,
                    stage="sfm_extract",
                    detail=f"正在做相机重建：{round_cfg['label']} / {profile['name']}，正在提取特征。",
                    round_cfg=round_cfg,
                    round_index=round_index,
                    profile=profile,
                    profile_index=profile_index,
                    attempt_estimates=attempt_estimates,
                    phase_name="feature_extractor",
                    phase_progress_start=feature_start,
                    phase_progress_end=match_start,
                    phase_budget_sec=attempt_estimates["feature_extractor"],
                    log_path=feature_log,
                    progress_probe=lambda: numeric_progress_probe(
                        units_done=count_descriptor_images(db_path),
                        units_total=selected_frames,
                        unit_label="张图",
                        action_label="提特征",
                        progress_basis="prep_feature_images",
                    ),
                )

                matcher_cmd = [
                    colmap_binary,
                    matcher,
                    "--database_path",
                    str(db_path),
                    "--SiftMatching.use_gpu",
                    "1" if args.colmap_use_gpu else "0",
                    "--SiftMatching.num_threads",
                    profile["match_threads"],
                ]
                if matcher == "sequential_matcher":
                    matcher_cmd += ["--SiftMatching.guided_matching", "true"]
                target_pairs = expected_match_pairs(selected_frames, matcher_mode)
                run_monitored_phase(
                    cmd=matcher_cmd,
                    stage="sfm_match",
                    detail=f"正在做相机重建：{round_cfg['label']} / {profile['name']}，正在匹配相邻视角。",
                    round_cfg=round_cfg,
                    round_index=round_index,
                    profile=profile,
                    profile_index=profile_index,
                    attempt_estimates=attempt_estimates,
                    phase_name="matcher",
                    phase_progress_start=match_start,
                    phase_progress_end=mapper_start,
                    phase_budget_sec=attempt_estimates["matcher"],
                    log_path=match_log,
                    progress_probe=lambda: matcher_progress_probe(
                        db_path=db_path,
                        log_path=match_log,
                        units_total=target_pairs,
                    ),
                )

                mapper_cmd = [
                    colmap_binary,
                    "mapper",
                    "--database_path",
                    str(db_path),
                    "--image_path",
                    str(subset_dir),
                    "--output_path",
                    str(sparse_dir),
                    "--Mapper.min_num_matches",
                    "12",
                    "--Mapper.init_min_num_inliers",
                    "40",
                    "--Mapper.abs_pose_min_num_inliers",
                    "20",
                    "--Mapper.num_threads",
                    profile["mapper_threads"],
                ]
                run_monitored_phase(
                    cmd=mapper_cmd,
                    stage="sfm_reconstruct",
                    detail=f"正在做相机重建：{round_cfg['label']} / {profile['name']}，正在求解稀疏模型。",
                    round_cfg=round_cfg,
                    round_index=round_index,
                    profile=profile,
                    profile_index=profile_index,
                    attempt_estimates=attempt_estimates,
                    phase_name="mapper",
                    phase_progress_start=mapper_start,
                    phase_progress_end=bundle_start,
                    phase_budget_sec=attempt_estimates["mapper"],
                    log_path=mapper_log,
                    progress_probe=lambda: numeric_progress_probe(
                        units_done=count_registered_images_from_log(mapper_log),
                        units_total=selected_frames,
                        unit_label="张图",
                        action_label="注册到稀疏模型",
                        progress_basis="prep_mapper_registered_images",
                    ),
                )

                if not sparse0_dir.is_dir():
                    raise RuntimeError(f"COLMAP mapper did not create sparse model at {sparse0_dir}")

                bundle_cmd = [
                    colmap_binary,
                    "bundle_adjuster",
                    "--input_path",
                    str(sparse0_dir),
                    "--output_path",
                    str(sparse0_dir),
                    "--BundleAdjustment.refine_principal_point",
                    "1",
                ]
                run_monitored_phase(
                    cmd=bundle_cmd,
                    stage="sfm_reconstruct",
                    detail=f"正在做相机重建：{round_cfg['label']} / {profile['name']}，正在做 bundle adjustment。",
                    round_cfg=round_cfg,
                    round_index=round_index,
                    profile=profile,
                    profile_index=profile_index,
                    attempt_estimates=attempt_estimates,
                    phase_name="bundle_adjuster",
                    phase_progress_start=bundle_start,
                    phase_progress_end=convert_start,
                    phase_budget_sec=attempt_estimates["bundle_adjuster"],
                    log_path=bundle_log,
                    progress_probe=lambda: {
                        "detail_suffix": "正在做 bundle adjustment",
                        "progress_basis": "prep_runtime_budget",
                    },
                )

                convert_cmd = [
                    colmap_binary,
                    "model_converter",
                    "--input_path",
                    str(sparse0_dir),
                    "--output_path",
                    str(sparse_txt_dir),
                    "--output_type",
                    "TXT",
                ]
                run_monitored_phase(
                    cmd=convert_cmd,
                    stage="sfm_reconstruct",
                    detail=f"正在整理相机重建结果：{round_cfg['label']} / {profile['name']}。",
                    round_cfg=round_cfg,
                    round_index=round_index,
                    profile=profile,
                    profile_index=profile_index,
                    attempt_estimates=attempt_estimates,
                    phase_name="model_converter",
                    phase_progress_start=convert_start,
                    phase_progress_end=profile_end,
                    phase_budget_sec=attempt_estimates["model_converter"],
                    log_path=convert_log,
                    progress_probe=lambda: {
                        "detail_suffix": "正在导出 TXT 相机模型",
                        "progress_basis": "prep_runtime_budget",
                    },
                )

                cameras_txt = sparse_txt_dir / "cameras.txt"
                if not cameras_txt.is_file():
                    raise RuntimeError(f"COLMAP cameras.txt missing at {cameras_txt}")

                camera_lines = [
                    line.strip()
                    for line in cameras_txt.read_text(encoding="utf-8", errors="ignore").splitlines()
                    if line.strip() and not line.startswith("#")
                ]
                if not camera_lines:
                    raise RuntimeError(f"COLMAP cameras.txt has no camera entries at {cameras_txt}")

                parts = camera_lines[-1].split()
                model = parts[1]
                width = int(parts[2])
                height = int(parts[3])
                params = [float(x) for x in parts[4:]]

                if model == "OPENCV" and len(params) >= 4:
                    fx, fy, cx, cy = params[:4]
                    calib_values = params
                elif model in {"PINHOLE", "SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"} and len(params) >= 3:
                    fx = params[0]
                    fy = params[1] if model == "PINHOLE" and len(params) >= 4 else fx
                    cx = params[-2]
                    cy = params[-1]
                    calib_values = [fx, fy, cx, cy]
                else:
                    raise RuntimeError(f"unsupported COLMAP camera model {model} with params {params}")

                calib_path = prep_root / "calib.txt"
                calib_path.write_text(" ".join(f"{x:.6f}" for x in calib_values) + "\n", encoding="utf-8")
                cameras_json_count = write_cameras_json_from_sparse_txt(prep_root)

                now = int(time.time())
                write_status(
                    status_path,
                    {
                        "state": "processing",
                        "stage": "sfm",
                        "detail": f"相机重建完成：{round_cfg['label']} / {profile['name']}，正在进入下一阶段。",
                        "progress_basis": "prep_runtime_budget",
                        "progress": 46.0,
                        "progress_start": 46.0,
                        "progress_end": 46.0,
                        "phase_name": "prep_complete",
                        "phase_budget_sec": 0,
                        "phase_started_at_epoch": now,
                        "run_started_at_epoch": run_started_at,
                        "elapsed_sec": max(0, now - run_started_at),
                        "estimated_remaining_sec": 0,
                        "current_round": round_cfg["label"],
                        "current_profile": profile["name"],
                        "round_index": round_index + 1,
                        "round_count": len(profile_rounds),
                        "profile_index": completed_attempts + profile_index + 1,
                        "profile_count": total_attempts,
                        "selected_frames": selected_frames,
                        "matcher": matcher_mode,
                        "max_image_size": int(profile["max_image_size"]),
                        "cameras_json_frames": cameras_json_count,
                    },
                )

                return {
                    "selected_frames": selected_frames,
                    "camera_model": model,
                    "width": width,
                    "height": height,
                    "fx": fx,
                    "fy": fy,
                    "cx": cx,
                    "cy": cy,
                    "matcher": args.colmap_matcher,
                    "use_gpu": args.colmap_use_gpu,
                    "frame_stride": round_cfg["stride"],
                    "max_frames": round_cfg["max_frames"],
                    "active_round": round_cfg["label"],
                    "active_profile": profile["name"],
                    "profile_rounds": [round_cfg["label"] for round_cfg in profile_rounds],
                    "cameras_json_frames": cameras_json_count,
                }
            except Exception as exc:
                last_error = exc
                now = int(time.time())
                write_status(
                    status_path,
                    {
                        "state": "processing",
                        "stage": "sfm",
                        "detail": f"{round_cfg['label']} / {profile['name']} 失败，正在尝试下一档更稳的预处理方案。",
                        "reason": str(exc),
                        "progress_basis": "prep_runtime_budget",
                        "progress": profile_end,
                        "progress_start": profile_end,
                        "progress_end": profile_end,
                        "phase_name": "fallback_retry",
                        "phase_budget_sec": 0,
                        "phase_started_at_epoch": now,
                        "run_started_at_epoch": run_started_at,
                        "elapsed_sec": max(0, now - run_started_at),
                        "estimated_remaining_sec": estimate_remaining_budget(
                            profile_rounds,
                            round_index,
                            profile_index,
                            matcher_mode,
                            selected_frames,
                            0,
                        ),
                        "current_round": round_cfg["label"],
                        "current_profile": profile["name"],
                        "round_index": round_index + 1,
                        "round_count": len(profile_rounds),
                        "profile_index": completed_attempts + profile_index + 1,
                        "profile_count": total_attempts,
                        "selected_frames": selected_frames,
                        "matcher": matcher_mode,
                        "max_image_size": int(profile["max_image_size"]),
                    },
                )
                print(
                    f"[colmap-profile] {round_cfg['label']}/{profile['name']} failed: {exc}",
                    flush=True,
                )

        completed_attempts += len(round_cfg["profiles"])

    raise RuntimeError(str(last_error) if last_error else "COLMAP preprocessing failed")


def maybe_reuse_live_sfm(prep_root: Path, status_path: Path, started_at: int):
    ready_path = prep_root / "LIVE_SFM_READY.json"
    if not ready_path.is_file():
        return None

    try:
        payload = json.loads(ready_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not payload.get("upload_completed"):
        return None

    colmap_calib = payload.get("colmap_calib")
    if not isinstance(colmap_calib, dict):
        return None

    sparse0_dir = prep_root / "sparse" / "0"
    sparse_txt_dir = prep_root / "sparse_txt"
    cameras_txt = sparse_txt_dir / "cameras.txt"
    calib_path = prep_root / "calib.txt"
    if not (sparse0_dir.is_dir() and cameras_txt.is_file() and calib_path.is_file()):
        return None

    now = int(time.time())
    write_status(
        status_path,
        {
            "state": "processing",
            "stage": "sfm",
            "title": "正在复用边上传边相机重建结果",
            "detail": (
                f"上传期间已经提前跑出了相机重建，正在直接复用这版结果："
                f"用 {int(payload.get('selected_frames') or 0)} 帧注册了 "
                f"{int(payload.get('registered_images') or 0)} 张图。"
            ),
            "progress_basis": "prep_reuse_live_sfm",
            "progress": 46.0,
            "progress_start": 46.0,
            "progress_end": 46.0,
            "phase_name": "reuse_live_sfm",
            "phase_budget_sec": 0,
            "phase_started_at_epoch": now,
            "run_started_at_epoch": started_at,
            "elapsed_sec": max(0, now - started_at),
            "estimated_remaining_sec": 0,
            "current_units": int(payload.get("registered_images") or 0),
            "target_units": int(payload.get("selected_frames") or 1),
            "unit_label": "张图",
        },
    )
    return colmap_calib


def main():
    args = parse_args()
    video_path = Path(args.video)
    prep_root = Path(args.prep_root)
    images_dir = prep_root / "images"
    calib_path = prep_root / "calib.txt"

    if not video_path.is_file():
        raise RuntimeError(f"missing video: {video_path}")
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed opening video: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = frame_count / max(fps, 1e-9)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    status_path = Path(args.status_json) if args.status_json else prep_root / "runtime_status.json"
    started_at = int(time.time())

    existing_frame_count = len(list_final_jpgs(images_dir)) if images_dir.is_dir() else 0
    should_extract = False
    if args.extract_if_missing:
        if existing_frame_count == 0:
            should_extract = True
        elif frame_count > 0 and existing_frame_count + 1 < frame_count:
            should_extract = True

    if should_extract:
        phase_started_at = int(time.time())
        progress_start = 22.0
        progress_end = 24.0
        phase_budget_sec = 45
        resume_index = next_missing_frame_index(images_dir) if images_dir.is_dir() else 0

        def emit_extract_status(extracted_frames: int):
            now = int(time.time())
            elapsed = max(0, now - phase_started_at)
            if frame_count > 0:
                ratio = clamp_ratio(extracted_frames / max(frame_count, 1))
                progress_value = progress_start + (progress_end - progress_start) * ratio
                remaining = estimate_phase_remaining_from_ratio(
                    elapsed,
                    ratio,
                    max(0, phase_budget_sec - elapsed),
                )
            else:
                ratio = None
                progress_value = progress_start
                remaining = max(0, phase_budget_sec - elapsed)

            payload = {
                "state": "processing",
                "stage": "preparing",
                "detail": "正在从原始视频里抽取帧图像。",
                "progress_basis": "prep_extract_frames" if ratio is not None else "prep_runtime_budget",
                "progress": progress_value,
                "progress_start": progress_start,
                "progress_end": progress_end,
                "phase_name": "extract_frames",
                "phase_budget_sec": phase_budget_sec,
                "phase_started_at_epoch": phase_started_at,
                "run_started_at_epoch": started_at,
                "elapsed_sec": max(0, now - started_at),
                "estimated_remaining_sec": remaining,
                "current_units": int(extracted_frames),
            }
            if frame_count > 0:
                payload["target_units"] = int(frame_count)
                payload["detail"] = f"正在从原始视频里抽取帧图像（{extracted_frames}/{frame_count} 帧）。"
            write_status(status_path, payload)

        emit_extract_status(resume_index)
        extract_all_frames(
            video_path,
            images_dir,
            fps,
            progress_callback=emit_extract_status,
            start_index=resume_index,
        )

    if not images_dir.is_dir():
        raise RuntimeError(f"missing extracted images: {images_dir}")

    final_paths, renamed = rename_images_with_timestamps(images_dir, fps)
    frames = len(final_paths)
    frame_inventory = summarize_frame_inventory(final_paths)
    frame_count_hint = frame_count
    frame_inventory_warning = None
    if frame_count_hint > 0:
        mismatch = frames - frame_count_hint
        tolerance = max(1, min(12, int(round(frame_count_hint * 0.001))))
        if abs(mismatch) > 1:
            # Treat CAP_PROP_FRAME_COUNT as a hint only. On remuxed / streaming inputs it can drift
            # by a few frames, and even larger deltas should not kill the whole job if we already
            # have a usable extracted image sequence on disk.
            frame_inventory_warning = {
                "video_frame_count_hint": int(frame_count_hint),
                "extracted_image_count": int(frames),
                "mismatch": int(mismatch),
                "tolerance": int(tolerance),
                "contiguous_from_zero": bool(frame_inventory["contiguous_from_zero"]),
                "missing_slots": int(frame_inventory["missing_slots"]),
            }
            print(
                "warning: frame count mismatch after preprocess; "
                f"using extracted images as source of truth "
                f"(video={frame_count_hint} images={frames} "
                f"contiguous={frame_inventory['contiguous_from_zero']} "
                f"missing_slots={frame_inventory['missing_slots']})",
                file=sys.stderr,
            )
            frame_count = frames
            duration_sec = frame_count / max(fps, 1e-9)

    colmap_calib = None
    heuristic_calib = None
    if args.run_colmap:
        if args.reuse_live_sfm_if_ready:
            colmap_calib = maybe_reuse_live_sfm(prep_root, status_path, started_at)
        if colmap_calib is None:
            colmap_calib = run_colmap(prep_root, args)
    elif not calib_path.is_file():
        heuristic_calib = write_heuristic_calib(calib_path, width, height, args.hfov_deg)

    manifest = {
        "video": str(video_path),
        "fps": fps,
        "frames": frame_count,
        "video_frame_count_hint": frame_count_hint,
        "extracted_image_count": frames,
        "duration_sec": duration_sec,
        "width": width,
        "height": height,
        "renamed_frames": renamed,
        "frame_inventory": frame_inventory,
        "frame_inventory_warning": frame_inventory_warning,
    }
    if colmap_calib is not None:
        manifest["colmap_calib"] = colmap_calib
    if heuristic_calib is not None:
        manifest["heuristic_calib"] = {
            "fx": heuristic_calib[0],
            "fy": heuristic_calib[1],
            "cx": heuristic_calib[2],
            "cy": heuristic_calib[3],
            "hfov_deg": args.hfov_deg,
        }
    (prep_root / "manifest.txt").write_text(
        "\n".join(f"{k}={v}" for k, v in manifest.items()) + "\n",
        encoding="utf-8",
    )
    (prep_root / "sequence_summary.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    (prep_root / ".complete").write_text("ok\n", encoding="utf-8")
    write_status(
        status_path,
        {
            "state": "processing",
            "stage": "sfm",
            "detail": "相机重建和校准完成，正在等待后续审查与训练。",
            "progress_basis": "prep_runtime_budget",
            "progress": 46.0,
            "progress_start": 46.0,
            "progress_end": 46.0,
            "phase_name": "prep_complete",
            "phase_budget_sec": 0,
            "phase_started_at_epoch": int(time.time()),
            "run_started_at_epoch": started_at,
            "elapsed_sec": max(0, int(time.time()) - started_at),
            "estimated_remaining_sec": 0,
        },
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
