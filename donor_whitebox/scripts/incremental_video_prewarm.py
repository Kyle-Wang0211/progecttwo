#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2

from frame_audit_core import StageAwareAuditCore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--prep-root", required=True)
    parser.add_argument("--feed-root", required=True)
    parser.add_argument("--status-json", required=True)
    parser.add_argument("--stop-flag", required=True)
    parser.add_argument("--upload-complete-flag", required=True)
    parser.add_argument("--current-tier", default="")
    parser.add_argument("--target-size-bytes", type=int, default=0)
    parser.add_argument("--poll-interval-sec", type=float, default=2.0)
    parser.add_argument("--progress-start", type=float, default=0.13)
    parser.add_argument("--progress-end", type=float, default=0.22)
    parser.add_argument("--downsample-width", type=int, default=160)
    parser.add_argument("--min-laplacian-var", type=float, default=6.0)
    parser.add_argument("--min-mean-diff", type=float, default=0.4)
    parser.add_argument("--min-brightness", type=float, default=12.0)
    parser.add_argument("--max-brightness", type=float, default=245.0)
    return parser.parse_args()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_jpg(path: Path, frame_bgr) -> None:
    tmp_path = path.with_name(f"{path.stem}.tmp{path.suffix}")
    if not cv2.imwrite(str(tmp_path), frame_bgr):
        raise RuntimeError(f"failed writing frame {tmp_path}")
    tmp_path.replace(path)


def parse_frame_index(path: Path) -> int | None:
    stem = path.stem
    prefix = stem.split("_", 1)[0]
    if not prefix.isdigit():
        return None
    try:
        return int(prefix)
    except ValueError:
        return None


def next_frame_index(images_dir: Path) -> int:
    highest = -1
    for path in sorted(images_dir.glob("*.jpg")):
        frame_index = parse_frame_index(path)
        if frame_index is None:
            continue
        highest = max(highest, frame_index)
    return highest + 1


def write_live_sequence_summary(
    *,
    prep_root: Path,
    video_path: Path,
    fps: float,
    frame_count: int,
    width: int,
    height: int,
    upload_completed: bool,
) -> None:
    duration_sec = 0.0
    if fps > 0 and frame_count > 0:
        duration_sec = frame_count / fps
    summary = {
        "video": str(video_path),
        "fps": float(fps),
        "frames": int(frame_count),
        "duration_sec": float(duration_sec),
        "width": int(width),
        "height": int(height),
        "stream_live": True,
        "upload_completed": bool(upload_completed),
    }
    write_json(prep_root / "sequence_summary.json", summary)
    (prep_root / "manifest.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in summary.items()) + "\n",
        encoding="utf-8",
    )


def build_status(
    *,
    args: argparse.Namespace,
    started_at: int,
    downloaded_bytes: int,
    extracted_frames: int,
    accepted_frames: int,
    phase_name: str,
    detail: str,
    progress_basis: str,
    frame_count_hint: int,
    upload_completed: bool,
) -> dict:
    now = int(time.time())
    ratio = 0.0
    if upload_completed and frame_count_hint > 0:
        ratio = max(0.0, min(1.0, float(extracted_frames) / float(max(frame_count_hint, 1))))
    elif args.target_size_bytes > 0:
        ratio = max(0.0, min(1.0, float(downloaded_bytes) / float(max(args.target_size_bytes, 1))))

    progress = float(args.progress_start) + (float(args.progress_end) - float(args.progress_start)) * ratio
    payload = {
        "state": "assigned",
        "stage": "preparing",
        "phase_name": phase_name,
        "current_tier": args.current_tier or None,
        "title": "正在边上传边预热",
        "detail": detail,
        "progress": progress,
        "progress_basis": progress_basis,
        "elapsed_sec": max(0, now - started_at),
        "estimated_remaining_sec": None,
        "current_units": int(extracted_frames),
        "extracted_frames": int(extracted_frames),
        "unit_label": "帧",
        "accepted_live_frames": int(accepted_frames),
        "downloaded_bytes": int(downloaded_bytes),
        "upload_completed": bool(upload_completed),
        "updated_at_epoch": now,
    }
    if upload_completed and frame_count_hint > 0:
        payload["target_units"] = int(frame_count_hint)
        if extracted_frames > 0 and 0.0 < ratio < 1.0:
            elapsed = max(1, now - started_at)
            payload["estimated_remaining_sec"] = int(round(elapsed * (1.0 - ratio) / max(ratio, 1e-6)))
    return payload


def main() -> None:
    args = parse_args()
    video_path = Path(args.video)
    prep_root = Path(args.prep_root)
    feed_root = Path(args.feed_root)
    status_path = Path(args.status_json)
    stop_flag = Path(args.stop_flag)
    upload_complete_flag = Path(args.upload_complete_flag)

    images_dir = prep_root / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    feed_root.mkdir(parents=True, exist_ok=True)
    live_audit_log = feed_root / "live_audit_log.jsonl"
    live_audit_summary = feed_root / "live_audit_summary.json"

    audit_core = StageAwareAuditCore(
        downsample_width=args.downsample_width,
        min_laplacian_var=args.min_laplacian_var,
        min_mean_diff=args.min_mean_diff,
        min_brightness=args.min_brightness,
        max_brightness=args.max_brightness,
        depth_auditor=None,
    )

    started_at = int(time.time())
    extracted_frames = next_frame_index(images_dir)
    accepted_frames = 0
    rejected_frames = 0
    too_bright_count = 0
    stage_counts: dict[str, int] = {}
    reject_reasons: dict[str, int] = {}
    last_kept_gray = None
    kept_gap = 0
    idle_polls_after_complete = 0

    live_audit_log.parent.mkdir(parents=True, exist_ok=True)
    with live_audit_log.open("w", encoding="utf-8") as audit_log_handle:
        while True:
            if stop_flag.exists():
                break

            upload_completed = upload_complete_flag.exists()
            downloaded_bytes = video_path.stat().st_size if video_path.exists() else 0

            if downloaded_bytes <= 0:
                write_json(
                    status_path,
                    build_status(
                        args=args,
                        started_at=started_at,
                        downloaded_bytes=0,
                        extracted_frames=extracted_frames,
                        accepted_frames=accepted_frames,
                        phase_name="stream_probe_live",
                        detail="正在等待上传过来的前几块视频数据落到本地。",
                        progress_basis="prep_stream_probe_live",
                        frame_count_hint=0,
                        upload_completed=upload_completed,
                    ),
                )
                time.sleep(max(0.5, args.poll_interval_sec))
                continue

            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                write_json(
                    status_path,
                    build_status(
                        args=args,
                        started_at=started_at,
                        downloaded_bytes=downloaded_bytes,
                        extracted_frames=extracted_frames,
                        accepted_frames=accepted_frames,
                        phase_name="stream_probe_live",
                        detail="视频字节已经到达，但容器头还不足以解码，继续等待更多 chunk。",
                        progress_basis="prep_stream_probe_live",
                        frame_count_hint=0,
                        upload_completed=upload_completed,
                    ),
                )
                time.sleep(max(0.5, args.poll_interval_sec))
                continue

            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps <= 0:
                fps = 30.0
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            frame_count_hint = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

            if extracted_frames > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, float(extracted_frames))

            newly_extracted = 0
            while True:
                ok, frame_bgr = cap.read()
                if not ok:
                    break
                timestamp = extracted_frames / max(fps, 1e-9)
                image_path = images_dir / f"{extracted_frames:06d}_{timestamp:.6f}.jpg"
                atomic_write_jpg(image_path, frame_bgr)

                progress_hint = 0.0
                if args.target_size_bytes > 0:
                    progress_hint = max(0.0, min(1.0, float(downloaded_bytes) / float(max(args.target_size_bytes, 1))))
                decision, gray = audit_core.evaluate(
                    frame_bgr=frame_bgr,
                    last_kept_gray=last_kept_gray,
                    kept_gap=kept_gap,
                    progress=progress_hint,
                    stage_intervals=None,
                )
                stage_counts[decision.stage] = stage_counts.get(decision.stage, 0) + 1
                if decision.too_bright:
                    too_bright_count += 1
                if decision.accepted:
                    accepted_frames += 1
                    kept_gap = 0
                    last_kept_gray = gray
                else:
                    rejected_frames += 1
                    kept_gap += 1
                    reject_reasons[decision.reason] = reject_reasons.get(decision.reason, 0) + 1

                audit_log_handle.write(json.dumps(decision.to_record(image_path.name), ensure_ascii=False) + "\n")
                audit_log_handle.flush()

                extracted_frames += 1
                newly_extracted += 1

            cap.release()

            write_live_sequence_summary(
                prep_root=prep_root,
                video_path=video_path,
                fps=fps,
                frame_count=extracted_frames,
                width=width,
                height=height,
                upload_completed=upload_completed,
            )

            detail = (
                f"正在边上传边抽帧和预审核，已抽取 {extracted_frames} 帧，预审核通过 {accepted_frames} 帧。"
                if extracted_frames > 0
                else "视频已经可解码，正在等待第一批帧。"
            )
            write_json(
                status_path,
                build_status(
                    args=args,
                    started_at=started_at,
                    downloaded_bytes=downloaded_bytes,
                    extracted_frames=extracted_frames,
                    accepted_frames=accepted_frames,
                    phase_name="audit_live" if extracted_frames > 0 else "extract_frames_live",
                    detail=detail,
                    progress_basis="prep_audit_live" if extracted_frames > 0 else "prep_extract_frames_live",
                    frame_count_hint=frame_count_hint,
                    upload_completed=upload_completed,
                ),
            )

            write_json(
                live_audit_summary,
                {
                    "video": str(video_path),
                    "extracted_frames": int(extracted_frames),
                    "accepted_frames": int(accepted_frames),
                    "rejected_frames": int(rejected_frames),
                    "too_bright_rate": float(too_bright_count / max(extracted_frames, 1)),
                    "accept_rate": float(accepted_frames / max(extracted_frames, 1)),
                    "stage_counts": stage_counts,
                    "reject_reasons": reject_reasons,
                    "upload_completed": bool(upload_completed),
                    "updated_at_epoch": int(time.time()),
                },
            )

            if upload_completed:
                if newly_extracted <= 0:
                    idle_polls_after_complete += 1
                else:
                    idle_polls_after_complete = 0
                if idle_polls_after_complete >= 2:
                    break

            time.sleep(max(0.5, args.poll_interval_sec))


if __name__ == "__main__":
    main()
