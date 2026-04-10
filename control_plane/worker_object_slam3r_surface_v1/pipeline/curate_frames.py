from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2

from ..config import config
from ..context import JobContext


@dataclass(frozen=True)
class _FrameMetrics:
    path: Path
    blur_score: float
    mean_brightness: float
    signature: bytes


def curate_frames(ctx: JobContext) -> None:
    ctx.current_stage = "curate"
    assert ctx.curated_dir is not None
    assert ctx.frames_dir is not None

    for stale in ctx.curated_dir.glob("*"):
        if stale.is_file():
            stale.unlink()

    frame_paths = sorted(
        path
        for path in ctx.frames_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    if not frame_paths:
        raise RuntimeError("curate_frames_missing_extracted_frames")

    blur_threshold = ctx.pipeline_float(
        "visual_blur_threshold_laplacian",
        config.curated_min_blur_score,
    )
    dark_threshold = ctx.pipeline_float(
        "visual_dark_threshold_brightness",
        config.curated_dark_threshold_brightness,
    )
    bright_threshold = ctx.pipeline_float(
        "visual_bright_threshold_brightness",
        config.curated_bright_threshold_brightness,
    )
    max_similarity = ctx.pipeline_float(
        "visual_max_frame_similarity",
        config.curated_max_frame_similarity,
    )
    min_accept_interval_sec = ctx.pipeline_float(
        "visual_min_accept_interval_sec",
        config.curated_min_accept_interval_sec,
    )
    client_live_selection_source = ctx.pipeline_string("client_live_selection_source", "")
    client_live_timestamps_ms = _parse_client_live_timestamps_ms(
        ctx.pipeline_string("client_live_accepted_timestamps_ms", "")
    )

    scored_frames: list[_FrameMetrics] = []
    unreadable_frames: list[str] = []
    for frame_path in frame_paths:
        image = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            unreadable_frames.append(frame_path.name)
            continue
        scored_frames.append(
            _FrameMetrics(
                path=frame_path,
                blur_score=float(cv2.Laplacian(image, cv2.CV_64F).var()),
                mean_brightness=float(image.mean()),
                signature=_signature(image),
            )
        )

    if not scored_frames:
        raise RuntimeError("curate_frames_no_readable_images")

    visually_valid = [
        frame
        for frame in scored_frames
        if frame.blur_score >= blur_threshold
        and dark_threshold <= frame.mean_brightness <= bright_threshold
    ]
    if not visually_valid:
        visually_valid = scored_frames

    used_client_live_selection = (
        client_live_selection_source == "visual_realtime" and bool(client_live_timestamps_ms)
    )

    if client_live_selection_source == "visual_realtime" and not client_live_timestamps_ms:
        raise RuntimeError("curate_frames_missing_client_live_timestamps")

    if used_client_live_selection:
        curated_unique = _select_frames_from_timestamps(
            frames=scored_frames,
            timestamps_ms=client_live_timestamps_ms,
            fps=config.extract_fps,
            max_frames=max(1, config.curated_max_frames),
        )
    else:
        curated_unique = _novelty_filter(
            frames=visually_valid,
            max_similarity=max_similarity,
            min_frame_gap=max(1, int(math.ceil(min_accept_interval_sec * config.extract_fps))),
            max_frames=max(1, config.curated_max_frames),
        )

        if not curated_unique:
            curated_unique = visually_valid[: max(1, min(len(visually_valid), config.curated_max_frames))]

        if curated_unique:
            first_frame = visually_valid[0]
            if curated_unique[0].path.name != first_frame.path.name:
                curated_unique.insert(0, first_frame)

    seen: set[str] = set()
    deduped: list[_FrameMetrics] = []
    for frame in curated_unique:
        if frame.path.name in seen:
            continue
        seen.add(frame.path.name)
        deduped.append(frame)
        if len(deduped) >= config.curated_max_frames:
            break

    minimum_slam_frames = max(4, config.curated_min_slam_frames)
    if used_client_live_selection:
        minimum_slam_frames = max(
            4,
            min(
                minimum_slam_frames,
                len(client_live_timestamps_ms),
                len(scored_frames),
            ),
        )
    if used_client_live_selection and len(deduped) < minimum_slam_frames:
        raise RuntimeError("curate_frames_insufficient_client_selected_frames")

    if not used_client_live_selection and len(deduped) < minimum_slam_frames:
        for frame in visually_valid:
            if frame.path.name in seen:
                continue
            seen.add(frame.path.name)
            deduped.append(frame)
            if len(deduped) >= minimum_slam_frames:
                break

    for frame in deduped:
        shutil.copy2(frame.path, ctx.curated_dir / frame.path.name)

    (ctx.curated_dir / "curate_frames.json").write_text(
        json.dumps(
            {
                "stage": "curate_frames",
                "strategy": ctx.pipeline_string("strategy", "object_slam3r_surface_v1"),
                "selection_source": "client_live_timestamps" if used_client_live_selection else "server_visual_curation",
                "target_zone_mode": ctx.pipeline_string("target_zone_mode", "subject"),
                "input_frame_count": len(frame_paths),
                "readable_frame_count": len(scored_frames),
                "visually_valid_frame_count": len(visually_valid),
                "curated_frame_count": len(deduped),
                "client_live_timestamp_count": len(client_live_timestamps_ms),
                "thresholds": {
                    "blur_threshold_laplacian": blur_threshold,
                    "dark_threshold_brightness": dark_threshold,
                    "bright_threshold_brightness": bright_threshold,
                    "max_frame_similarity": max_similarity,
                    "min_accept_interval_sec": min_accept_interval_sec,
                },
                "client_live_accepted_frames": ctx.pipeline_int("client_live_accepted_frames", 0),
                "frames": [
                    {
                        "name": frame.path.name,
                        "blur_score": round(frame.blur_score, 3),
                        "mean_brightness": round(frame.mean_brightness, 3),
                        "similarity_to_previous_accepted": round(
                            _similarity(frame.signature, deduped[index - 1].signature) if index > 0 else 0.0,
                            4,
                        ),
                    }
                    for index, frame in enumerate(deduped)
                ],
                "unreadable_frames": unreadable_frames,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _signature(image: "cv2.typing.MatLike") -> bytes:
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    return bytes(resized.flatten().tolist())


def _similarity(lhs: bytes, rhs: bytes) -> float:
    if not lhs or not rhs or len(lhs) != len(rhs):
        return 0.0
    difference = sum(abs(a - b) for a, b in zip(lhs, rhs)) / (255.0 * len(lhs))
    return float(max(0.0, min(1.0, 1.0 - difference)))


def _novelty_filter(
    *,
    frames: list[_FrameMetrics],
    max_similarity: float,
    min_frame_gap: int,
    max_frames: int,
) -> list[_FrameMetrics]:
    accepted: list[_FrameMetrics] = []
    last_signature = b""
    last_index = -10_000

    for index, frame in enumerate(frames):
        if not accepted:
            accepted.append(frame)
            last_signature = frame.signature
            last_index = index
            continue

        if index - last_index < min_frame_gap:
            continue

        similarity = _similarity(frame.signature, last_signature)
        if similarity > max_similarity:
            continue

        accepted.append(frame)
        last_signature = frame.signature
        last_index = index
        if len(accepted) >= max_frames:
            break

    return accepted


def _parse_client_live_timestamps_ms(raw: str) -> list[int]:
    values: list[int] = []
    for token in raw.split(","):
        stripped = token.strip()
        if not stripped:
            continue
        try:
            value = int(stripped)
        except ValueError:
            continue
        if value < 0:
            continue
        values.append(value)
    return values


def _select_frames_from_timestamps(
    *,
    frames: list[_FrameMetrics],
    timestamps_ms: list[int],
    fps: float,
    max_frames: int,
) -> list[_FrameMetrics]:
    if not frames or fps <= 0:
        return []

    selected: list[_FrameMetrics] = []
    seen_indices: set[int] = set()
    frame_count = len(frames)

    for timestamp_ms in timestamps_ms:
        frame_index = int(round((timestamp_ms / 1000.0) * fps))
        frame_index = max(0, min(frame_count - 1, frame_index))
        resolved_index = _nearest_unused_index(
            target_index=frame_index,
            frame_count=frame_count,
            used_indices=seen_indices,
            search_radius=3,
        )
        if resolved_index is None:
            continue
        frame = frames[resolved_index]
        seen_indices.add(resolved_index)
        selected.append(frame)
        if len(selected) >= max_frames:
            break

    return selected


def _nearest_unused_index(
    *,
    target_index: int,
    frame_count: int,
    used_indices: set[int],
    search_radius: int,
) -> int | None:
    if target_index not in used_indices:
        return target_index

    for distance in range(1, search_radius + 1):
        lower = target_index - distance
        upper = target_index + distance
        if lower >= 0 and lower not in used_indices:
            return lower
        if upper < frame_count and upper not in used_indices:
            return upper

    for candidate in range(frame_count):
        if candidate not in used_indices:
            return candidate
    return None
