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
    global_variance: float
    orb_feature_count: int
    target_texture_score: float
    target_contrast_score: float
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
    min_orb_features = ctx.pipeline_int(
        "visual_min_orb_features",
        config.curated_min_orb_features,
    )
    warn_orb_features = ctx.pipeline_int(
        "visual_warn_orb_features",
        config.curated_warn_orb_features,
    )
    min_target_signal = ctx.pipeline_float(
        "visual_min_target_signal",
        config.curated_min_target_signal,
    )
    warn_target_signal = ctx.pipeline_float(
        "visual_warn_target_signal",
        config.curated_warn_target_signal,
    )
    target_zone_anchor_x = ctx.pipeline_float("target_zone_anchor_x", 0.5)
    target_zone_anchor_y = ctx.pipeline_float("target_zone_anchor_y", 0.64)
    client_live_selection_source = ctx.pipeline_string("client_live_selection_source", "")
    client_live_timestamps_ms = _parse_client_live_timestamps_ms(
        ctx.pipeline_string("client_live_accepted_timestamps_ms", "")
    )
    target_zone_mode = ctx.pipeline_string("target_zone_mode", "subject")

    unreadable_frames: list[str] = []
    used_client_live_selection = (
        client_live_selection_source == "visual_realtime" and bool(client_live_timestamps_ms)
    )

    if client_live_selection_source == "visual_realtime" and not client_live_timestamps_ms:
        raise RuntimeError("curate_frames_missing_client_live_timestamps")

    minimum_slam_frames = max(4, config.curated_min_slam_frames)
    readable_frame_count = 0
    visually_valid_frame_count = 0
    client_live_backfill_count = 0
    client_live_rejected_count = 0
    client_live_soft_outlier_count = 0
    hard_reject_counts = {
        "blur": 0,
        "dark": 0,
        "bright": 0,
        "occupancy": 0,
        "unreadable": 0,
    }
    soft_downgrade_counts = {
        "redundant": 0,
        "low_texture": 0,
        "weak_quality": 0,
        "low_features": 0,
    }
    guidance_counts = {
        "recenter": 0,
        "new_angle": 0,
        "coverage": 0,
    }

    if used_client_live_selection:
        minimum_slam_frames = max(
            4,
            min(
                minimum_slam_frames,
                len(client_live_timestamps_ms),
                len(frame_paths),
            ),
        )
        selected_indices = _select_frame_indices_from_timestamps(
            frame_count=len(frame_paths),
            timestamps_ms=client_live_timestamps_ms,
            fps=config.extract_fps,
            max_frames=max(1, config.curated_max_frames),
        )
        curated_unique: list[_FrameMetrics] = []
        for index in selected_indices:
            metric = _score_frame(
                frame_paths[index],
                target_zone_anchor=(target_zone_anchor_x, target_zone_anchor_y),
                target_zone_mode=target_zone_mode,
            )
            if metric is None:
                unreadable_frames.append(frame_paths[index].name)
                client_live_rejected_count += 1
                hard_reject_counts["unreadable"] += 1
                continue
            readable_frame_count += 1
            hard_reasons = _hard_reject_reasons(
                metric,
                blur_threshold=blur_threshold,
                dark_threshold=dark_threshold,
                bright_threshold=bright_threshold,
                min_orb_features=min_orb_features,
                min_target_signal=min_target_signal,
            )
            if hard_reasons:
                client_live_soft_outlier_count += 1
                _tally_reasons(hard_reject_counts, hard_reasons)
            else:
                visually_valid_frame_count += 1
            curated_unique.append(metric)

        if len(curated_unique) < minimum_slam_frames:
            supplemented = _supplement_client_selected_frames(
                frame_paths=frame_paths,
                selected_indices=selected_indices,
                already_selected_names={frame.path.name for frame in curated_unique},
                unreadable_frames=unreadable_frames,
                blur_threshold=blur_threshold,
                dark_threshold=dark_threshold,
                bright_threshold=bright_threshold,
                min_orb_features=min_orb_features,
                min_target_signal=min_target_signal,
                target_zone_anchor=(target_zone_anchor_x, target_zone_anchor_y),
                target_zone_mode=target_zone_mode,
                min_frame_gap=max(1, int(math.ceil(min_accept_interval_sec * config.extract_fps))),
                minimum_needed=minimum_slam_frames - len(curated_unique),
                max_frames=max(1, config.curated_max_frames),
            )
            client_live_backfill_count = len(supplemented)
            curated_unique.extend(supplemented)
            readable_frame_count += len(supplemented)
            visually_valid_frame_count += len(supplemented)

        if len(curated_unique) < minimum_slam_frames:
            raise RuntimeError("curate_frames_insufficient_client_selected_frames")
    else:
        scored_frames: list[_FrameMetrics] = []
        for frame_path in frame_paths:
            metric = _score_frame(
                frame_path,
                target_zone_anchor=(target_zone_anchor_x, target_zone_anchor_y),
                target_zone_mode=target_zone_mode,
            )
            if metric is None:
                unreadable_frames.append(frame_path.name)
                hard_reject_counts["unreadable"] += 1
                continue
            scored_frames.append(metric)

        if not scored_frames:
            raise RuntimeError("curate_frames_no_readable_images")

        visually_valid = [
            frame
            for frame in scored_frames
            if not _hard_reject_reasons(
                frame,
                blur_threshold=blur_threshold,
                dark_threshold=dark_threshold,
                bright_threshold=bright_threshold,
                min_orb_features=min_orb_features,
                min_target_signal=min_target_signal,
            )
        ]
        for frame in scored_frames:
            _tally_reasons(
                hard_reject_counts,
                _hard_reject_reasons(
                    frame,
                    blur_threshold=blur_threshold,
                    dark_threshold=dark_threshold,
                    bright_threshold=bright_threshold,
                    min_orb_features=min_orb_features,
                    min_target_signal=min_target_signal,
                ),
            )
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
        readable_frame_count = len(scored_frames)
        visually_valid_frame_count = len(visually_valid)

    seen: set[str] = set()
    deduped: list[_FrameMetrics] = []
    for frame in curated_unique:
        if frame.path.name in seen:
            continue
        seen.add(frame.path.name)
        deduped.append(frame)
        if len(deduped) >= config.curated_max_frames:
            break

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

    for index, frame in enumerate(deduped):
        similarity = _similarity(frame.signature, deduped[index - 1].signature) if index > 0 else 0.0
        _tally_reasons(
            soft_downgrade_counts,
            _soft_downgrade_reasons(
                frame,
                similarity=similarity,
                max_similarity=max_similarity,
                warn_orb_features=warn_orb_features,
                warn_target_signal=warn_target_signal,
                min_global_variance=config.curated_min_global_variance,
            ),
        )
        _tally_reasons(
            guidance_counts,
            _guidance_flags(
                frame,
                similarity=similarity,
                max_similarity=max_similarity,
                warn_target_signal=warn_target_signal,
            ),
        )

    (ctx.output_dir / "curate_frames.json").write_text(
        json.dumps(
            {
                "stage": "curate_frames",
                "strategy": ctx.pipeline_string("strategy", "object_slam3r_surface_v1"),
                "selection_source": "client_live_timestamps" if used_client_live_selection else "server_visual_curation",
                "target_zone_mode": ctx.pipeline_string("target_zone_mode", "subject"),
                "input_frame_count": len(frame_paths),
                "readable_frame_count": readable_frame_count,
                "visually_valid_frame_count": visually_valid_frame_count,
                "curated_frame_count": len(deduped),
                "client_live_timestamp_count": len(client_live_timestamps_ms),
                "client_live_backfill_count": client_live_backfill_count,
                "client_live_rejected_count": client_live_rejected_count,
                "client_live_soft_outlier_count": client_live_soft_outlier_count,
                "client_live_runtime_summary": {
                    "total_samples": ctx.pipeline_int("client_live_total_samples", 0),
                    "hard_reject_blur_count": ctx.pipeline_int("client_live_hard_reject_blur_count", 0),
                    "hard_reject_dark_count": ctx.pipeline_int("client_live_hard_reject_dark_count", 0),
                    "hard_reject_bright_count": ctx.pipeline_int("client_live_hard_reject_bright_count", 0),
                    "hard_reject_occupancy_count": ctx.pipeline_int("client_live_hard_reject_occupancy_count", 0),
                    "soft_redundant_count": ctx.pipeline_int("client_live_soft_redundant_count", 0),
                    "soft_low_texture_count": ctx.pipeline_int("client_live_soft_low_texture_count", 0),
                    "soft_weak_quality_count": ctx.pipeline_int("client_live_soft_weak_quality_count", 0),
                    "guidance_recenter_count": ctx.pipeline_int("client_live_guidance_recenter_count", 0),
                    "guidance_new_angle_count": ctx.pipeline_int("client_live_guidance_new_angle_count", 0),
                    "guidance_coverage_count": ctx.pipeline_int("client_live_guidance_coverage_count", 0),
                },
                "policy_counts": {
                    "hard_reject": hard_reject_counts,
                    "soft_downgrade": soft_downgrade_counts,
                    "guidance_only": guidance_counts,
                },
                "thresholds": {
                    "blur_threshold_laplacian": blur_threshold,
                    "dark_threshold_brightness": dark_threshold,
                    "bright_threshold_brightness": bright_threshold,
                    "max_frame_similarity": max_similarity,
                    "min_accept_interval_sec": min_accept_interval_sec,
                    "min_orb_features": min_orb_features,
                    "warn_orb_features": warn_orb_features,
                    "min_target_signal": min_target_signal,
                    "warn_target_signal": warn_target_signal,
                },
                "client_live_accepted_frames": ctx.pipeline_int("client_live_accepted_frames", 0),
                "frames": [
                    {
                        "name": frame.path.name,
                        "blur_score": round(frame.blur_score, 3),
                        "mean_brightness": round(frame.mean_brightness, 3),
                        "global_variance": round(frame.global_variance, 3),
                        "orb_feature_count": frame.orb_feature_count,
                        "target_texture_score": round(frame.target_texture_score, 4),
                        "target_contrast_score": round(frame.target_contrast_score, 4),
                        "target_signal": round(_target_signal(frame), 4),
                        "similarity_to_previous_accepted": round(
                            _similarity(frame.signature, deduped[index - 1].signature) if index > 0 else 0.0,
                            4,
                        ),
                        "hard_reject_reasons": _hard_reject_reasons(
                            frame,
                            blur_threshold=blur_threshold,
                            dark_threshold=dark_threshold,
                            bright_threshold=bright_threshold,
                            min_orb_features=min_orb_features,
                            min_target_signal=min_target_signal,
                        ),
                        "soft_downgrade_reasons": _soft_downgrade_reasons(
                            frame,
                            similarity=_similarity(frame.signature, deduped[index - 1].signature) if index > 0 else 0.0,
                            max_similarity=max_similarity,
                            warn_orb_features=warn_orb_features,
                            warn_target_signal=warn_target_signal,
                            min_global_variance=config.curated_min_global_variance,
                        ),
                        "guidance_only_flags": _guidance_flags(
                            frame,
                            similarity=_similarity(frame.signature, deduped[index - 1].signature) if index > 0 else 0.0,
                            max_similarity=max_similarity,
                            warn_target_signal=warn_target_signal,
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


def _score_frame(
    frame_path: Path,
    *,
    target_zone_anchor: tuple[float, float],
    target_zone_mode: str,
) -> _FrameMetrics | None:
    image = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    target_texture_score, target_contrast_score = _target_zone_metrics(
        image,
        target_zone_anchor=target_zone_anchor,
        target_zone_mode=target_zone_mode,
    )
    orb_feature_count = _orb_feature_count(image)
    return _FrameMetrics(
        path=frame_path,
        blur_score=float(cv2.Laplacian(image, cv2.CV_64F).var()),
        mean_brightness=float(image.mean()),
        global_variance=float(image.var()),
        orb_feature_count=orb_feature_count,
        target_texture_score=target_texture_score,
        target_contrast_score=target_contrast_score,
        signature=_signature(image),
    )


def _target_signal(frame: _FrameMetrics) -> float:
    return frame.target_texture_score * 0.55 + frame.target_contrast_score * 0.45


def _hard_reject_reasons(
    frame: _FrameMetrics,
    *,
    blur_threshold: float,
    dark_threshold: float,
    bright_threshold: float,
    min_orb_features: int,
    min_target_signal: float,
) -> list[str]:
    reasons: list[str] = []
    if frame.blur_score < blur_threshold:
        reasons.append("blur")
    if frame.mean_brightness < dark_threshold:
        reasons.append("dark")
    if frame.mean_brightness > bright_threshold:
        reasons.append("bright")
    if frame.orb_feature_count < min_orb_features:
        reasons.append("low_features")
    if _target_signal(frame) < min_target_signal:
        reasons.append("occupancy")
    return reasons


def _soft_downgrade_reasons(
    frame: _FrameMetrics,
    *,
    similarity: float,
    max_similarity: float,
    warn_orb_features: int,
    warn_target_signal: float,
    min_global_variance: float,
) -> list[str]:
    reasons: list[str] = []
    if similarity > max_similarity:
        reasons.append("redundant")
    if frame.orb_feature_count < warn_orb_features:
        reasons.append("low_features")
    if frame.global_variance < min_global_variance:
        reasons.append("low_texture")
    if _target_signal(frame) < warn_target_signal:
        reasons.append("weak_quality")
    return reasons


def _guidance_flags(
    frame: _FrameMetrics,
    *,
    similarity: float,
    max_similarity: float,
    warn_target_signal: float,
) -> list[str]:
    flags: list[str] = []
    if _target_signal(frame) < warn_target_signal:
        flags.append("recenter")
    if similarity > max_similarity:
        flags.append("new_angle")
    else:
        flags.append("coverage")
    return flags


def _signature(image: "cv2.typing.MatLike") -> bytes:
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    return bytes(resized.flatten().tolist())


def _similarity(lhs: bytes, rhs: bytes) -> float:
    if not lhs or not rhs or len(lhs) != len(rhs):
        return 0.0
    difference = sum(abs(a - b) for a, b in zip(lhs, rhs)) / (255.0 * len(lhs))
    return float(max(0.0, min(1.0, 1.0 - difference)))


def _tally_reasons(counter: dict[str, int], reasons: list[str]) -> None:
    for reason in reasons:
        if reason in counter:
            counter[reason] += 1


def _orb_feature_count(image: "cv2.typing.MatLike") -> int:
    detector = cv2.ORB_create(nfeatures=max(config.curated_warn_orb_features, 1000))
    keypoints = detector.detect(image, None)
    return int(len(keypoints))


def _target_zone_metrics(
    image: "cv2.typing.MatLike",
    *,
    target_zone_anchor: tuple[float, float],
    target_zone_mode: str,
) -> tuple[float, float]:
    height, width = image.shape[:2]
    zone_width_fraction = 0.24 if target_zone_mode == "subject" else 0.38
    zone_height_fraction = 0.28 if target_zone_mode == "subject" else 0.34
    rect = _normalized_rect(
        anchor=target_zone_anchor,
        width_fraction=zone_width_fraction,
        height_fraction=zone_height_fraction,
        image_width=width,
        image_height=height,
    )
    ring_rect = _expanded_rect(
        rect,
        padding=3 if target_zone_mode == "subject" else 4,
        max_width=width,
        max_height=height,
    )
    zone_values = _pixel_values(image, rect)
    ring_values = _pixel_values(image, ring_rect, excluding=rect)
    zone_variance = _variance(zone_values)
    zone_mean = _mean(zone_values)
    ring_mean = _mean(ring_values)
    texture_score = min(zone_variance / 420.0, 1.0)
    contrast_score = min(abs(zone_mean - ring_mean) / 28.0, 1.0)
    return float(texture_score), float(contrast_score)


def _normalized_rect(
    *,
    anchor: tuple[float, float],
    width_fraction: float,
    height_fraction: float,
    image_width: int,
    image_height: int,
) -> tuple[int, int, int, int]:
    width = max(4, int(round(float(image_width) * width_fraction)))
    height = max(4, int(round(float(image_height) * height_fraction)))
    center_x = int(round(anchor[0] * float(image_width)))
    center_y = int(round(anchor[1] * float(image_height)))
    x = min(max(center_x - width // 2, 0), max(image_width - width, 0))
    y = min(max(center_y - height // 2, 0), max(image_height - height, 0))
    return x, y, width, height


def _expanded_rect(
    rect: tuple[int, int, int, int],
    *,
    padding: int,
    max_width: int,
    max_height: int,
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    min_x = max(x - padding, 0)
    min_y = max(y - padding, 0)
    max_x = min(x + width + padding, max_width)
    max_y = min(y + height + padding, max_height)
    return min_x, min_y, max(max_x - min_x, 1), max(max_y - min_y, 1)


def _pixel_values(
    image: "cv2.typing.MatLike",
    rect: tuple[int, int, int, int],
    *,
    excluding: tuple[int, int, int, int] | None = None,
) -> list[float]:
    x, y, width, height = rect
    values: list[float] = []
    for row in range(y, y + height):
        for col in range(x, x + width):
            if excluding is not None:
                ex, ey, ew, eh = excluding
                if ex <= col < ex + ew and ey <= row < ey + eh:
                    continue
            values.append(float(image[row, col]))
    return values


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _variance(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean_value = _mean(values)
    return float(sum((value - mean_value) ** 2 for value in values) / len(values))


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


def _select_frame_indices_from_timestamps(
    *,
    frame_count: int,
    timestamps_ms: list[int],
    fps: float,
    max_frames: int,
) -> list[int]:
    if frame_count <= 0 or fps <= 0:
        return []

    selected: list[int] = []
    seen_indices: set[int] = set()
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
        seen_indices.add(resolved_index)
        selected.append(resolved_index)
        if len(selected) >= max_frames:
            break
    return selected


def _supplement_client_selected_frames(
    *,
    frame_paths: list[Path],
    selected_indices: list[int],
    already_selected_names: set[str],
    unreadable_frames: list[str],
    blur_threshold: float,
    dark_threshold: float,
    bright_threshold: float,
    min_orb_features: int,
    min_target_signal: float,
    target_zone_anchor: tuple[float, float],
    target_zone_mode: str,
    min_frame_gap: int,
    minimum_needed: int,
    max_frames: int,
) -> list[_FrameMetrics]:
    if minimum_needed <= 0:
        return []

    frame_count = len(frame_paths)
    used_indices = set(selected_indices)
    accepted: list[_FrameMetrics] = []
    accepted_indices: list[int] = list(selected_indices)
    candidate_indices: list[int] = []
    seen_candidates: set[int] = set()

    search_radius = max(4, min(18, min_frame_gap * 3))
    for base_index in selected_indices:
        for radius in range(1, search_radius + 1):
            for candidate in (base_index - radius, base_index + radius):
                if candidate < 0 or candidate >= frame_count or candidate in seen_candidates:
                    continue
                seen_candidates.add(candidate)
                candidate_indices.append(candidate)

    for candidate in range(frame_count):
        if candidate in seen_candidates:
            continue
        candidate_indices.append(candidate)

    for candidate in candidate_indices:
        if candidate in used_indices:
            continue
        if any(abs(candidate - accepted_index) < min_frame_gap for accepted_index in accepted_indices):
            continue
        metric = _score_frame(
            frame_paths[candidate],
            target_zone_anchor=target_zone_anchor,
            target_zone_mode=target_zone_mode,
        )
        if metric is None:
            unreadable_frames.append(frame_paths[candidate].name)
            continue
        if metric.path.name in already_selected_names:
            continue
        if _hard_reject_reasons(
            metric,
            blur_threshold=blur_threshold,
            dark_threshold=dark_threshold,
            bright_threshold=bright_threshold,
            min_orb_features=min_orb_features,
            min_target_signal=min_target_signal,
        ):
            continue

        accepted.append(metric)
        accepted_indices.append(candidate)
        used_indices.add(candidate)
        already_selected_names.add(metric.path.name)
        if len(accepted) >= minimum_needed or (len(accepted_indices) >= max_frames):
            break

    return accepted




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
