#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from types import SimpleNamespace

from prepare_real_video_owndata import list_final_jpgs, run_colmap, write_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prep-root", required=True)
    parser.add_argument("--feed-root", required=True)
    parser.add_argument("--status-json", required=True)
    parser.add_argument("--ready-json", required=True)
    parser.add_argument("--stop-flag", required=True)
    parser.add_argument("--upload-complete-flag", required=True)
    parser.add_argument("--current-tier", default="")
    parser.add_argument("--poll-interval-sec", type=float, default=2.0)
    parser.add_argument("--min-frames", type=int, default=48)
    parser.add_argument("--min-new-frames", type=int, default=24)
    parser.add_argument("--colmap-binary", default="colmap")
    parser.add_argument("--colmap-frame-stride", type=int, default=5)
    parser.add_argument("--colmap-max-frames", type=int, default=96)
    parser.add_argument("--colmap-matcher", choices=("sequential", "exhaustive"), default="sequential")
    parser.add_argument("--colmap-use-gpu", action="store_true")
    return parser.parse_args()


def write_runtime_status(
    *,
    status_path: Path,
    started_at: int,
    current_tier: str,
    phase_name: str,
    detail: str,
    progress_basis: str,
    progress: float,
    current_units: int | None = None,
    target_units: int | None = None,
    unit_label: str | None = None,
    estimated_remaining_sec: int | None = None,
    title: str = "正在边上传边做相机重建",
    extra_metrics: dict[str, int | str | bool] | None = None,
) -> None:
    now = int(time.time())
    payload = {
        "state": "assigned",
        "stage": "sfm",
        "phase_name": phase_name,
        "current_tier": current_tier or None,
        "title": title,
        "detail": detail,
        "progress_basis": progress_basis,
        "progress": float(progress),
        "elapsed_sec": max(0, now - started_at),
        "estimated_remaining_sec": estimated_remaining_sec,
        "updated_at_epoch": now,
    }
    if current_units is not None:
        payload["current_units"] = int(current_units)
    if target_units is not None:
        payload["target_units"] = int(target_units)
    if unit_label:
        payload["unit_label"] = unit_label
    if extra_metrics:
        for key, value in extra_metrics.items():
            if isinstance(value, (bool, int, float, str)):
                payload[key] = value
    write_status(status_path, payload)


def count_frames(images_dir: Path) -> int:
    return len(list_final_jpgs(images_dir))


def read_live_audit_summary(feed_root: Path) -> dict:
    path = feed_root / "live_audit_summary.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> None:
    args = parse_args()
    prep_root = Path(args.prep_root)
    feed_root = Path(args.feed_root)
    status_path = Path(args.status_json)
    ready_path = Path(args.ready_json)
    stop_flag = Path(args.stop_flag)
    upload_complete_flag = Path(args.upload_complete_flag)
    images_dir = prep_root / "images"

    started_at = int(time.time())
    last_attempt_frame_count = 0
    last_success_frame_count = 0
    last_success_payload: dict | None = None

    while True:
        if stop_flag.exists():
            break

        extracted_frames = count_frames(images_dir)
        audit_summary = read_live_audit_summary(feed_root)
        accepted_frames = int(audit_summary.get("accepted_frames") or 0)
        upload_completed = upload_complete_flag.exists()

        min_frames = max(4, args.min_frames)
        if extracted_frames < min_frames:
            write_runtime_status(
                status_path=status_path,
                started_at=started_at,
                current_tier=args.current_tier,
                phase_name="sfm_wait_live",
                detail=f"边上传边相机重建等待更多帧，当前已抽取 {extracted_frames} 帧，达到 {min_frames} 帧后启动。",
                progress_basis="prep_live_sfm_wait_frames",
                progress=22.0,
                current_units=extracted_frames,
                target_units=min_frames,
                unit_label="帧",
                extra_metrics={
                    "extracted_frames": int(extracted_frames),
                    "accepted_live_frames": int(accepted_frames),
                },
            )
            time.sleep(max(0.5, args.poll_interval_sec))
            continue

        should_run = False
        if last_success_payload is None:
            should_run = True
        elif extracted_frames >= last_attempt_frame_count + max(1, args.min_new_frames):
            should_run = True
        elif upload_completed and extracted_frames > last_attempt_frame_count:
            should_run = True

        if not should_run:
            registered = int((last_success_payload or {}).get("registered_images") or 0)
            selected = int((last_success_payload or {}).get("selected_frames") or max(extracted_frames, 1))
            detail = (
                f"边上传边相机重建已拿到一版稀疏模型，当前已抽取 {extracted_frames} 帧、"
                f"最近一次用 {selected} 帧注册了 {registered} 张图；等待更多帧后刷新。"
            )
            if upload_completed:
                detail = (
                    f"上传已完成，边上传边相机重建已完成最终一版，最近一次用 {selected} 帧"
                    f"注册了 {registered} 张图，等待正式预处理复用。"
                )
            write_runtime_status(
                status_path=status_path,
                started_at=started_at,
                current_tier=args.current_tier,
                phase_name="live_sfm_ready",
                detail=detail,
                progress_basis="prep_live_sfm_ready",
                progress=46.0,
                current_units=registered,
                target_units=selected,
                unit_label="张图",
                title="边上传边相机重建已拿到结果",
                extra_metrics={
                    "extracted_frames": int(extracted_frames),
                    "accepted_live_frames": int(accepted_frames),
                    "selected_frames": int(selected),
                    "registered_images": int(registered),
                },
            )
            if upload_completed:
                break
            time.sleep(max(0.5, args.poll_interval_sec))
            continue

        last_attempt_frame_count = extracted_frames
        colmap_args = SimpleNamespace(
            prep_root=str(prep_root),
            run_colmap=True,
            colmap_binary=args.colmap_binary,
            colmap_frame_stride=max(1, args.colmap_frame_stride),
            colmap_max_frames=max(8, args.colmap_max_frames),
            colmap_matcher=args.colmap_matcher,
            colmap_use_gpu=bool(args.colmap_use_gpu),
            status_json=str(status_path),
        )

        try:
            colmap_payload = run_colmap(prep_root, colmap_args)
        except Exception as exc:
            write_runtime_status(
                status_path=status_path,
                started_at=started_at,
                current_tier=args.current_tier,
                phase_name="live_sfm_retry_wait",
                detail=f"边上传边相机重建这轮没有收敛：{exc}。等待更多帧后重试。",
                progress_basis="prep_live_sfm_retry_wait",
                progress=30.0,
                current_units=extracted_frames,
                target_units=max(extracted_frames, min_frames),
                unit_label="帧",
                title="边上传边相机重建待重试",
                extra_metrics={
                    "extracted_frames": int(extracted_frames),
                    "accepted_live_frames": int(accepted_frames),
                },
            )
            time.sleep(max(1.0, args.poll_interval_sec))
            continue

        last_success_frame_count = extracted_frames
        last_success_payload = {
            "ready_at_epoch": int(time.time()),
            "current_tier": args.current_tier or None,
            "upload_completed": bool(upload_completed),
            "captured_frame_count": int(extracted_frames),
            "accepted_live_frames": int(accepted_frames),
            "selected_frames": int(colmap_payload.get("selected_frames") or 0),
            "registered_images": int(colmap_payload.get("registered_images") or 0),
            "colmap_calib": colmap_payload,
            "prep_root": str(prep_root),
            "feed_root": str(feed_root),
        }

        mapper_log = prep_root / f"{colmap_payload.get('active_round', 'safe_default')}_{colmap_payload.get('active_profile', 'safe_1600')}_mapper.log"
        try:
            from prepare_real_video_owndata import count_registered_images_from_log

            last_success_payload["registered_images"] = int(count_registered_images_from_log(mapper_log))
        except Exception:
            pass

        tmp_ready = ready_path.with_suffix(ready_path.suffix + ".tmp")
        tmp_ready.write_text(json.dumps(last_success_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_ready.replace(ready_path)

        write_runtime_status(
            status_path=status_path,
            started_at=started_at,
            current_tier=args.current_tier,
            phase_name="live_sfm_ready",
            detail=(
                f"边上传边相机重建已跑出一版稀疏模型：已抽取 {extracted_frames} 帧，"
                f"选了 {last_success_payload['selected_frames']} 帧做 COLMAP，"
                f"注册了 {last_success_payload['registered_images']} 张图。"
            ),
            progress_basis="prep_live_sfm_ready",
            progress=46.0,
            current_units=int(last_success_payload["registered_images"]),
            target_units=int(last_success_payload["selected_frames"]),
            unit_label="张图",
            title="边上传边相机重建已拿到结果",
            extra_metrics={
                "extracted_frames": int(extracted_frames),
                "accepted_live_frames": int(accepted_frames),
                "selected_frames": int(last_success_payload["selected_frames"]),
                "registered_images": int(last_success_payload["registered_images"]),
            },
        )

        if upload_completed:
            break

        time.sleep(max(0.5, args.poll_interval_sec))


if __name__ == "__main__":
    main()
