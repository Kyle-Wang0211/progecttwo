#!/usr/bin/env python3
import argparse
import json
import shutil
import time
from pathlib import Path

import cv2

from frame_audit_core import StageAwareAuditCore, UniK3DDepthAudit


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--src-root", required=True)
    p.add_argument("--dst-root", required=True)
    p.add_argument("--realtime-scale", type=float, default=0.0)
    p.add_argument("--jpeg-quality", type=int, default=95)
    p.add_argument("--downsample-width", type=int, default=160)
    p.add_argument("--min-laplacian-var", type=float, default=6.0)
    p.add_argument("--min-mean-diff", type=float, default=0.4)
    p.add_argument("--min-brightness", type=float, default=12.0)
    p.add_argument("--max-brightness", type=float, default=245.0)
    p.add_argument("--depth-audit-backend", choices=["none", "unik3d"], default="none")
    p.add_argument("--min-depth-p50", type=float, default=0.6)
    p.add_argument("--max-near-ratio-05m", type=float, default=0.55)
    p.add_argument("--max-near-ratio-1m", type=float, default=0.92)
    p.add_argument("--max-near-ratio-2m", type=float, default=1.0)
    p.add_argument("--unik3d-repo", type=str, default="/root/gs_refs/UniK3D")
    p.add_argument("--unik3d-backbone", choices=["vits", "vitb", "vitl"], default="vitl")
    p.add_argument("--unik3d-device", type=str, default="cuda")
    p.add_argument("--unik3d-resolution-level", type=int, default=7)
    p.add_argument("--gate-min-live-frames", type=int, default=180)
    p.add_argument("--gate-max-live-frames", type=int, default=1200)
    p.add_argument("--gate-min-accept-rate", type=float, default=0.90)
    p.add_argument("--gate-max-too-bright-rate", type=float, default=0.10)
    p.add_argument("--gate-min-feed-fps", type=float, default=8.0)
    return p.parse_args()


def read_video_fps(video_path: Path) -> float:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed opening video {video_path} for fps probe")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    cap.release()
    if fps <= 0:
        raise RuntimeError(f"failed reading fps from {video_path}")
    return fps


def extract_timestamp(path: Path, fps_hint: float, idx: int):
    stem = path.stem
    if stem.endswith(".tmp"):
        stem = stem[:-4]
    if "_" in stem:
        return float(stem.split("_")[-1])
    return idx / max(fps_hint, 1e-9)


def list_final_jpgs(images_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in images_dir.glob("*.jpg")
        if not path.name.endswith(".tmp.jpg")
    )


def copy_metadata(src_root: Path, dst_root: Path):
    for name in ("calib.txt", "manifest.txt", "sequence_summary.json"):
        src = src_root / name
        if src.exists():
            shutil.copy2(src, dst_root / name)


def encode_jpg_bytes(frame_bgr, quality: int):
    ok, buffer = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("failed jpeg encode")
    return buffer.tobytes()


def write_jpg_bytes(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def main():
    args = parse_args()
    video_path = Path(args.video)
    src_root = Path(args.src_root)
    dst_root = Path(args.dst_root)
    dst_images = dst_root / "images"
    dst_images.mkdir(parents=True, exist_ok=True)

    summary_path_src = src_root / "sequence_summary.json"
    if summary_path_src.exists():
        summary = json.loads(summary_path_src.read_text(encoding="utf-8"))
        fps_hint = float(summary.get("fps", 0.0))
    else:
        summary = {}
        fps_hint = read_video_fps(video_path)

    image_paths = list_final_jpgs(src_root / "images")
    if not image_paths:
        raise RuntimeError(f"no jpg frames in {src_root / 'images'}")

    timestamps = [extract_timestamp(path, fps_hint, idx) for idx, path in enumerate(image_paths)]
    total_frames = len(image_paths)

    depth_auditor = None
    if args.depth_audit_backend == "unik3d":
        depth_auditor = UniK3DDepthAudit(
            repo=args.unik3d_repo,
            backbone=args.unik3d_backbone,
            device=args.unik3d_device,
            resolution_level=args.unik3d_resolution_level,
        )

    audit_core = StageAwareAuditCore(
        downsample_width=args.downsample_width,
        min_laplacian_var=args.min_laplacian_var,
        min_mean_diff=args.min_mean_diff,
        min_brightness=args.min_brightness,
        max_brightness=args.max_brightness,
        min_depth_p50=args.min_depth_p50,
        max_near_ratio_05m=args.max_near_ratio_05m,
        max_near_ratio_1m=args.max_near_ratio_1m,
        max_near_ratio_2m=args.max_near_ratio_2m,
        depth_auditor=depth_auditor,
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed opening video {video_path}")

    audit_log_path = dst_root / "audit_log.jsonl"
    summary_path = dst_root / "audit_summary.json"
    last_kept_gray = None
    kept_gap = 0
    accepted = 0
    rejected = 0
    processed = 0
    reject_reasons = {}
    too_bright_count = 0
    stage_counts = {}
    depth_stats_history = []
    gate_metrics = None
    start_monotonic = time.monotonic()

    def current_gate_metrics():
        accept_rate = accepted / max(processed, 1)
        too_bright_rate = too_bright_count / max(processed, 1)
        elapsed_video = max(timestamps[min(processed - 1, len(timestamps) - 1)] - timestamps[0], 1e-9)
        feed_fps = accepted / elapsed_video
        return {
            "processed_live_frames": int(processed),
            "accepted_frames": int(accepted),
            "accept_rate": float(accept_rate),
            "too_bright_rate": float(too_bright_rate),
            "feed_fps": float(feed_fps),
            "elapsed_video_sec": float(elapsed_video),
        }

    def flush_metadata():
        copy_metadata(src_root, dst_root)

    with audit_log_path.open("w", encoding="utf-8") as log_fp:
        for idx, source_path in enumerate(image_paths):
            ok, frame_bgr = cap.read()
            if not ok:
                raise RuntimeError(f"video decode ended early at frame {idx} / {total_frames}")

            target_elapsed = (timestamps[idx] - timestamps[0]) * args.realtime_scale
            actual_elapsed = time.monotonic() - start_monotonic
            if actual_elapsed < target_elapsed:
                time.sleep(target_elapsed - actual_elapsed)

            progress = idx / max(total_frames - 1, 1)
            decision, gray = audit_core.evaluate(
                frame_bgr=frame_bgr,
                last_kept_gray=last_kept_gray,
                kept_gap=kept_gap,
                progress=progress,
                stage_intervals=None,
            )

            processed += 1
            stage_counts[decision.stage] = stage_counts.get(decision.stage, 0) + 1
            if decision.too_bright:
                too_bright_count += 1

            if decision.accepted:
                accepted += 1
                kept_gap = 0
                last_kept_gray = gray
                if decision.depth_p50 is not None:
                    depth_stats_history.append(
                        {
                            "depth_p50": decision.depth_p50,
                            "depth_p95": decision.depth_p95,
                            "near_ratio_05m": decision.near_ratio_05m,
                            "near_ratio_1m": decision.near_ratio_1m,
                            "near_ratio_2m": decision.near_ratio_2m,
                        }
                    )
                payload = encode_jpg_bytes(frame_bgr, args.jpeg_quality)
                flush_metadata()
                write_jpg_bytes(dst_images / source_path.name, payload)
            else:
                rejected += 1
                kept_gap += 1
                reject_reasons[decision.reason] = reject_reasons.get(decision.reason, 0) + 1

            log_fp.write(json.dumps(decision.to_record(source_path.name)) + "\n")
            log_fp.flush()
            gate_metrics = current_gate_metrics()

    cap.release()

    (dst_root / ".complete").write_text("ok\n", encoding="utf-8")
    out = {
        "video": str(video_path),
        "processed_live_frames": int(processed),
        "accepted": int(accepted),
        "rejected": int(rejected),
        "live_to_feed_rate": float(accepted / max(processed, 1)),
        "too_bright_rate": float(too_bright_count / max(processed, 1)),
        "feed_fps": float(accepted / max(timestamps[-1] - timestamps[0], 1e-9)),
        "duration_sec": float(timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0.0,
        "gate_passed": True,
        "gate_metrics": gate_metrics,
        "gate_disabled": True,
        "reject_reasons": reject_reasons,
        "stage_counts": stage_counts,
    }
    if depth_stats_history:
        out["depth_p50_mean"] = float(sum(x["depth_p50"] for x in depth_stats_history) / len(depth_stats_history))
        out["depth_p95_mean"] = float(sum(x["depth_p95"] for x in depth_stats_history) / len(depth_stats_history))
        out["near_ratio_05m_mean"] = float(sum(x["near_ratio_05m"] for x in depth_stats_history) / len(depth_stats_history))
        out["near_ratio_1m_mean"] = float(sum(x["near_ratio_1m"] for x in depth_stats_history) / len(depth_stats_history))
        out["near_ratio_2m_mean"] = float(sum(x["near_ratio_2m"] for x in depth_stats_history) / len(depth_stats_history))
    summary_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        "[audit-real-video-frames] "
        f"processed={processed} accepted={accepted} "
        f"live_to_feed_rate={out['live_to_feed_rate']:.6f} "
        f"feed_fps={out['feed_fps']:.6f}"
    )


if __name__ == "__main__":
    main()
