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

    for frame in deduped:
        shutil.copy2(frame.path, ctx.curated_dir / frame.path.name)

    (ctx.curated_dir / "curate_frames.json").write_text(
        json.dumps(
            {
                "stage": "curate_frames",
                "strategy": ctx.pipeline_string("strategy", "object_fast_publish_v1"),
                "target_zone_mode": ctx.pipeline_string("target_zone_mode", "subject"),
                "input_frame_count": len(frame_paths),
                "readable_frame_count": len(scored_frames),
                "visually_valid_frame_count": len(visually_valid),
                "curated_frame_count": len(deduped),
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
