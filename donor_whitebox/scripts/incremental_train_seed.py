#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path


ANSI_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
TQDM_RE = re.compile(
    r"(?P<label>.+?):\s*(?P<pct>\d{1,3})%\|.*?\|\s*(?P<cur>\d+)\/(?P<tot>\d+)\s*\[(?P<elapsed>[0-9:]+)<(?P<remaining>[0-9:]+)"
)
KEYFRAME_RE = re.compile(r"Processing keyframe\s+(?P<cur>\d+)\s+gs\s+(?P<gaussians>\d+)")
OOM_RE = re.compile(r"(torch\.cuda\.OutOfMemoryError|CUDA out of memory|Tried to allocate)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/root/donor_whitebox")
    parser.add_argument("--repo", default="/root/gs_refs/HI-SLAM2")
    parser.add_argument("--prep-root", required=True)
    parser.add_argument("--ready-json", required=True)
    parser.add_argument("--status-json", required=True)
    parser.add_argument("--stop-flag", required=True)
    parser.add_argument("--upload-complete-flag", required=True)
    parser.add_argument("--current-tier", default="")
    parser.add_argument("--poll-interval-sec", type=float, default=1.0)
    parser.add_argument("--min-selected-frames", type=int, default=96)
    parser.add_argument("--min-registered-images", type=int, default=32)
    parser.add_argument("--max-seed-images", type=int, default=48)
    parser.add_argument("--min-seed-images", type=int, default=8)
    parser.add_argument("--oom-retry-limit", type=int, default=3)
    parser.add_argument("--skip-tsdf", action="store_true")
    return parser.parse_args()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def list_final_jpgs(images_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in images_dir.glob("*.jpg")
            if ".tmp" not in path.name
        ),
        key=lambda path: path.name,
    )


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def build_seed_signature(payload: dict) -> str:
    return json.dumps(
        {
            "camera_source": str(payload.get("camera_source") or ""),
            "selected_frames": int(payload.get("selected_frames") or 0),
            "registered_images": int(payload.get("registered_images") or 0),
            "active_round": str((payload.get("colmap_calib") or {}).get("active_round") or ""),
            "active_profile": str((payload.get("colmap_calib") or {}).get("active_profile") or ""),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def evenly_sample(paths: list[Path], count: int) -> list[Path]:
    if count <= 0:
        return []
    if count >= len(paths):
        return list(paths)
    step = (len(paths) - 1) / max(count - 1, 1)
    picked: list[Path] = []
    seen: set[int] = set()
    for index in range(count):
        source_index = min(int(round(index * step)), len(paths) - 1)
        if source_index in seen:
            continue
        seen.add(source_index)
        picked.append(paths[source_index])
    return picked


def write_calib(path: Path, colmap_calib: dict) -> None:
    fx = float(colmap_calib["fx"])
    fy = float(colmap_calib["fy"])
    cx = float(colmap_calib["cx"])
    cy = float(colmap_calib["cy"])
    path.write_text(f"{fx:.6f} {fy:.6f} {cx:.6f} {cy:.6f}\n", encoding="utf-8")


def write_heuristic_calib(path: Path, *, width: int, height: int, hfov_deg: float) -> dict:
    hfov_rad = hfov_deg * math.pi / 180.0
    fx = 0.5 * width / max(math.tan(0.5 * hfov_rad), 1e-9)
    fy = fx
    cx = width * 0.5
    cy = height * 0.5
    path.write_text(f"{fx:.6f} {fy:.6f} {cx:.6f} {cy:.6f}\n", encoding="utf-8")
    return {
        "camera_model": "PINHOLE",
        "width": int(width),
        "height": int(height),
        "fx": float(fx),
        "fy": float(fy),
        "cx": float(cx),
        "cy": float(cy),
        "active_round": "heuristic",
        "active_profile": "prewarm",
    }


def build_heuristic_ready_payload(*, prep_root: Path, min_selected_frames: int) -> dict | None:
    source_images_dir = prep_root / "images"
    if not source_images_dir.is_dir():
        return None
    source_images = list_final_jpgs(source_images_dir)
    selected_frames = len(source_images)
    if selected_frames < max(1, min_selected_frames):
        return None
    summary = load_json(prep_root / "sequence_summary.json") or {}
    width = int(summary.get("width") or 0)
    height = int(summary.get("height") or 0)
    if width <= 0 or height <= 0:
        return None
    return {
        "camera_source": "heuristic_prewarm",
        "selected_frames": int(selected_frames),
        "registered_images": 0,
        "hfov_deg": float(summary.get("hfov_deg") or 62.0),
        "sequence_summary": summary,
    }


def build_seed_snapshot(*, prep_root: Path, payload: dict, seed_root: Path, seed_frame_limit: int | None) -> dict:
    source_images_dir = prep_root / "images_colmap"
    if not source_images_dir.is_dir():
        source_images_dir = prep_root / "images"
    if not source_images_dir.is_dir():
        raise RuntimeError(f"missing source images dir: {source_images_dir}")

    source_images = list_final_jpgs(source_images_dir)
    if not source_images:
        raise RuntimeError(f"no seed images found in {source_images_dir}")

    requested_selected_frames = int(payload.get("selected_frames") or 0)
    desired_count = len(source_images)
    if requested_selected_frames > 0:
        desired_count = min(desired_count, requested_selected_frames)
    if seed_frame_limit is not None and seed_frame_limit > 0:
        desired_count = min(desired_count, seed_frame_limit)
    desired_count = max(1, desired_count)
    selected_source_images = evenly_sample(source_images, desired_count)

    colmap_calib = payload.get("colmap_calib")
    camera_source = str(payload.get("camera_source") or "live_sfm_seed_snapshot")
    summary = payload.get("sequence_summary")
    if not isinstance(summary, dict):
        summary = load_json(prep_root / "sequence_summary.json") or {}

    tmp_root = seed_root.parent / f"{seed_root.name}.tmpbuild"
    shutil.rmtree(tmp_root, ignore_errors=True)
    images_out = tmp_root / "images"
    images_out.mkdir(parents=True, exist_ok=True)

    for src in selected_source_images:
        shutil.copy2(src, images_out / src.name)

    if isinstance(colmap_calib, dict):
        write_calib(tmp_root / "calib.txt", colmap_calib)
    else:
        width = int(summary.get("width") or 0)
        height = int(summary.get("height") or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError("missing colmap_calib and sequence_summary width/height for heuristic seed")
        colmap_calib = write_heuristic_calib(
            tmp_root / "calib.txt",
            width=width,
            height=height,
            hfov_deg=float(payload.get("hfov_deg") or summary.get("hfov_deg") or 62.0),
        )

    manifest = {
        "source": camera_source,
        "selected_frames": len(selected_source_images),
        "requested_selected_frames": requested_selected_frames,
        "registered_images": int(payload.get("registered_images") or 0),
        "seed_image_count": len(selected_source_images),
        "seed_source_image_count": len(source_images),
        "camera_model": str(colmap_calib.get("camera_model") or "PINHOLE"),
        "width": int(colmap_calib.get("width") or 0),
        "height": int(colmap_calib.get("height") or 0),
        "active_round": str(colmap_calib.get("active_round") or ""),
        "active_profile": str(colmap_calib.get("active_profile") or ""),
    }
    (tmp_root / "manifest.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in manifest.items()) + "\n",
        encoding="utf-8",
    )
    write_json(tmp_root / "sequence_summary.json", manifest)
    (tmp_root / ".complete").write_text("ok\n", encoding="utf-8")

    shutil.rmtree(seed_root, ignore_errors=True)
    shutil.move(str(tmp_root), str(seed_root))
    return manifest


def cleanup_process_group(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 2.0
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.2)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return


def read_log_tail(path: Path, max_bytes: int = 512 * 1024) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes), os.SEEK_SET)
        data = handle.read().decode("utf-8", errors="ignore")
    return ANSI_RE.sub("", data).replace("\r", "\n")


def last_tqdm_snapshot(text: str):
    latest = None
    for match in TQDM_RE.finditer(text):
        latest = match
    return latest


def last_keyframe_snapshot(text: str):
    latest = None
    for match in KEYFRAME_RE.finditer(text):
        latest = match
    return latest


def is_oom_log(text: str) -> bool:
    return bool(OOM_RE.search(text))


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
    extra_metrics: dict | None = None,
    title: str = "正在边上传边训练 3D 模型",
) -> None:
    now = int(time.time())
    payload = {
        "state": "processing",
        "stage": "train",
        "phase_name": phase_name,
        "current_tier": current_tier or None,
        "title": title,
        "detail": detail,
        "progress_basis": progress_basis,
        "progress": float(progress),
        "elapsed_sec": max(0, now - started_at),
        "updated_at_epoch": now,
        "estimated_remaining_sec": None,
    }
    if current_units is not None:
        payload["current_units"] = int(current_units)
    if target_units is not None:
        payload["target_units"] = int(target_units)
    if unit_label:
        payload["unit_label"] = unit_label
    if extra_metrics:
        for key, value in extra_metrics.items():
            if value is None:
                continue
            payload[key] = value
    write_json(status_path, payload)


def start_runtime(
    *,
    root: Path,
    repo: Path,
    seed_root: Path,
    log_path: Path,
    skip_tsdf: bool,
    current_tier: str,
) -> subprocess.Popen[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    env = os.environ.copy()
    env["ROOT"] = str(root)
    env["HI_SLAM2_REPO"] = str(repo)
    env["HI_SLAM2_SKIP_TSDF"] = "1" if skip_tsdf else "0"
    env["HI_SLAM2_TRAIN_INPUT_ROOT"] = str(seed_root)
    env["PYTHONUNBUFFERED"] = "1"
    run_dir_name = seed_root.parent.name
    run_name = run_dir_name.removeprefix("hislam2_") if run_dir_name.startswith("hislam2_") else run_dir_name
    try:
        return subprocess.Popen(
            [
                "bash",
                str(root / "scripts" / "remote_run_hislam2_realvideo_train_phase.sh"),
                run_name,
                current_tier,
            ],
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=env,
        )
    finally:
        handle.close()


def main() -> None:
    args = parse_args()
    root = Path(args.root)
    repo = Path(args.repo)
    prep_root = Path(args.prep_root)
    ready_path = Path(args.ready_json)
    status_path = Path(args.status_json)
    stop_flag = Path(args.stop_flag)
    upload_complete_flag = Path(args.upload_complete_flag)

    seed_root = prep_root.parent / f"{args.current_tier}_train_seed"
    seed_info_path = prep_root.parent / f"{args.current_tier}_train_seed.json"
    log_path = root / "logs" / prep_root.parent.name.replace("hislam2_", "") / f"{args.current_tier}_seed_train.log"

    started_at = int(time.time())
    process: subprocess.Popen[str] | None = None
    launched_signature: str | None = None
    seed_manifest: dict | None = None
    terminate_requested = False
    blocked_signature: str | None = None
    blocked_reason: str | None = None
    last_seed_frame_limit: int | None = None
    oom_retry_counts: dict[str, int] = {}

    def request_terminate(_signum: int, _frame) -> None:
        nonlocal terminate_requested
        terminate_requested = True

    signal.signal(signal.SIGTERM, request_terminate)
    signal.signal(signal.SIGINT, request_terminate)

    try:
        while True:
            if terminate_requested or stop_flag.exists():
                break

            upload_completed = upload_complete_flag.exists()
            ready_payload = load_json(ready_path)
            seed_payload = ready_payload

            if process is None:
                if seed_payload is None:
                    seed_payload = build_heuristic_ready_payload(
                        prep_root=prep_root,
                        min_selected_frames=max(1, args.min_selected_frames),
                    )
                if seed_payload is None:
                    if upload_completed:
                        break
                    time.sleep(max(0.5, args.poll_interval_sec))
                    continue

                selected_frames = int(seed_payload.get("selected_frames") or 0)
                registered_images = int(seed_payload.get("registered_images") or 0)
                camera_source = str(seed_payload.get("camera_source") or "")
                requires_registered_images = camera_source != "heuristic_prewarm"
                base_signature = build_seed_signature(seed_payload)
                if blocked_signature and blocked_signature != base_signature:
                    blocked_signature = None
                    blocked_reason = None
                if selected_frames < max(1, args.min_selected_frames) or (
                    requires_registered_images and registered_images < max(1, args.min_registered_images)
                ):
                    if upload_completed:
                        break
                    time.sleep(max(0.5, args.poll_interval_sec))
                    continue

                if blocked_signature == base_signature:
                    detail = (
                        f"种子训练这版输入暂不重试：已选 {selected_frames} 帧，已注册 {registered_images} 张图。"
                        f"原因：{blocked_reason or '等待更多输入后再试'}。"
                    )
                    if upload_completed:
                        detail = (
                            f"上传已完成，当前种子训练不再继续重试，后续改由正式 full train 接管。"
                            f"原因：{blocked_reason or '等待正式阶段'}。"
                        )
                        break
                    write_runtime_status(
                        status_path=status_path,
                        started_at=started_at,
                        current_tier=args.current_tier,
                        phase_name="seed_wait_more_input",
                        detail=detail,
                        progress_basis="seed_wait_more_input",
                        progress=48.0,
                        current_units=registered_images,
                        target_units=max(selected_frames, 1),
                        unit_label="张图",
                        extra_metrics={
                            "selected_frames": int(selected_frames),
                            "registered_images": int(registered_images),
                            "upload_completed": bool(upload_completed),
                            "seed_blocked": True,
                        },
                        title="种子训练等待更多输入",
                    )
                    time.sleep(max(0.5, args.poll_interval_sec))
                    continue

                retry_count = oom_retry_counts.get(base_signature, 0)
                desired_seed_limit = selected_frames if selected_frames > 0 else args.max_seed_images
                if args.max_seed_images > 0:
                    desired_seed_limit = min(desired_seed_limit, args.max_seed_images)
                desired_seed_limit = max(desired_seed_limit, args.min_seed_images)
                for _ in range(retry_count):
                    if desired_seed_limit <= args.min_seed_images:
                        break
                    desired_seed_limit = max(args.min_seed_images, desired_seed_limit // 2)

                current_signature = f"{base_signature}|seed_limit={desired_seed_limit}"
                if launched_signature == current_signature:
                    if upload_completed:
                        break
                    time.sleep(max(0.5, args.poll_interval_sec))
                    continue

                seed_manifest = build_seed_snapshot(
                    prep_root=prep_root,
                    payload=seed_payload,
                    seed_root=seed_root,
                    seed_frame_limit=desired_seed_limit,
                )
                write_json(
                    seed_info_path,
                    {
                        "signature": current_signature,
                        "seed_root": str(seed_root),
                        "selected_frames": selected_frames,
                        "registered_images": registered_images,
                        "camera_source": camera_source or "live_sfm_seed_snapshot",
                        "seed_manifest": seed_manifest,
                        "seed_frame_limit": int(desired_seed_limit),
                        "oom_retry_count": int(retry_count),
                    },
                )
                process = start_runtime(
                    root=root,
                    repo=repo,
                    seed_root=seed_root,
                    log_path=log_path,
                    skip_tsdf=bool(args.skip_tsdf),
                    current_tier=args.current_tier,
                )
                launched_signature = current_signature
                last_seed_frame_limit = int(desired_seed_limit)
                blocked_signature = None
                blocked_reason = None

            if process is not None:
                log_text = read_log_tail(log_path)
                tqdm_snapshot = last_tqdm_snapshot(log_text)
                keyframe_snapshot = last_keyframe_snapshot(log_text)
                current_units = None
                target_units = None
                progress = 0.0
                unit_label: str | None = None
                phase_name = "seed_booting"
                progress_basis = "seed_runtime_booting"
                title = "正在启动种子训练"
                if str((seed_manifest or {}).get("source") or "") == "heuristic_prewarm":
                    detail = (
                        f"正在用启发式相机参数提前启动训练：已选 {int((seed_manifest or {}).get('selected_frames') or 0)} 帧，"
                        f"先不等正式相机重建收口，正在等待第一批实时训练步数。"
                    )
                else:
                    detail = (
                        f"正在用种子快照提前启动训练：已选 {int((seed_manifest or {}).get('selected_frames') or 0)} 帧，"
                        f"已注册 {int((seed_manifest or {}).get('registered_images') or 0)} 张图，正在等待第一批实时训练步数。"
                    )
                if tqdm_snapshot is not None:
                    current_units = int(tqdm_snapshot.group("cur"))
                    target_units = max(int(tqdm_snapshot.group("tot")), 1)
                    progress = max(0.0, min(1.0, float(current_units) / float(target_units)))
                    unit_label = "步"
                    phase_name = "seed_full"
                    progress_basis = "runtime_tqdm_steps"
                    title = "正在边上传边训练 3D 模型"
                    if str((seed_manifest or {}).get("source") or "") == "heuristic_prewarm":
                        detail = (
                            f"正在用启发式相机参数边上传边训练种子模型：已选 {int((seed_manifest or {}).get('selected_frames') or 0)} 帧，"
                            f"训练进度 {current_units}/{target_units} 步。"
                        )
                    else:
                        detail = (
                            f"正在边上传边训练种子模型：已选 {int((seed_manifest or {}).get('selected_frames') or 0)} 帧，"
                            f"已注册 {int((seed_manifest or {}).get('registered_images') or 0)} 张图，"
                            f"训练进度 {current_units}/{target_units} 步。"
                        )
                elif keyframe_snapshot is not None:
                    current_units = max(int(keyframe_snapshot.group("cur")), 0)
                    target_units = max(int((seed_manifest or {}).get("seed_image_count") or 0), 1)
                    current_units = min(current_units, target_units)
                    progress = max(0.0, min(1.0, float(current_units) / float(target_units)))
                    unit_label = "关键帧"
                    phase_name = "seed_full"
                    progress_basis = "runtime_tqdm_steps"
                    title = "正在边上传边训练 3D 模型"
                    if str((seed_manifest or {}).get("source") or "") == "heuristic_prewarm":
                        detail = (
                            f"正在用启发式相机参数边上传边训练种子模型：已选 {int((seed_manifest or {}).get('selected_frames') or 0)} 帧，"
                            f"关键帧进度 {current_units}/{target_units}。"
                        )
                    else:
                        detail = (
                            f"正在边上传边训练种子模型：已选 {int((seed_manifest or {}).get('selected_frames') or 0)} 帧，"
                            f"已注册 {int((seed_manifest or {}).get('registered_images') or 0)} 张图，"
                            f"关键帧进度 {current_units}/{target_units}。"
                        )
                write_runtime_status(
                    status_path=status_path,
                    started_at=started_at,
                    current_tier=args.current_tier,
                    phase_name=phase_name,
                    detail=detail,
                    progress_basis=progress_basis,
                    progress=progress,
                    current_units=current_units,
                    target_units=target_units,
                    unit_label=unit_label if target_units is not None else None,
                    extra_metrics={
                        "selected_frames": int((seed_manifest or {}).get("selected_frames") or 0),
                        "registered_images": int((seed_manifest or {}).get("registered_images") or 0),
                        "seed_image_count": int((seed_manifest or {}).get("seed_image_count") or 0),
                        "upload_completed": bool(upload_completed),
                        "seed_runtime_gaussians": int(keyframe_snapshot.group("gaussians")) if keyframe_snapshot is not None else None,
                        "seed_frame_limit": int(last_seed_frame_limit or 0),
                        "oom_retry_count": int(oom_retry_counts.get(build_seed_signature(seed_payload or {}), 0)) if seed_payload else 0,
                    },
                    title=title,
                )

                if process.poll() is not None:
                    exit_code = process.returncode
                    last_log_text = read_log_tail(log_path)
                    base_signature = build_seed_signature(seed_payload or {})
                    if exit_code == 0:
                        blocked_signature = base_signature
                        blocked_reason = "这版种子训练已经跑完，等待更多输入后再刷新。"
                    elif is_oom_log(last_log_text):
                        next_retry_count = oom_retry_counts.get(base_signature, 0) + 1
                        oom_retry_counts[base_signature] = next_retry_count
                        if (last_seed_frame_limit or 0) > args.min_seed_images and next_retry_count <= args.oom_retry_limit:
                            reduced_limit = max(args.min_seed_images, max(1, (last_seed_frame_limit or args.max_seed_images) // 2))
                            write_runtime_status(
                                status_path=status_path,
                                started_at=started_at,
                                current_tier=args.current_tier,
                                phase_name="seed_retry_smaller",
                                detail=(
                                    f"种子训练这轮显存不够，正在把训练快照从 {int(last_seed_frame_limit or 0)} 帧"
                                    f"缩到 {int(reduced_limit)} 帧后重试。"
                                ),
                                progress_basis="seed_retry_smaller",
                                progress=50.0,
                                current_units=int(reduced_limit),
                                target_units=max(int(last_seed_frame_limit or reduced_limit), 1),
                                unit_label="帧",
                                extra_metrics={
                                    "upload_completed": bool(upload_completed),
                                    "seed_frame_limit": int(reduced_limit),
                                    "oom_retry_count": int(next_retry_count),
                                },
                                title="种子训练正在缩小快照后重试",
                            )
                        else:
                            blocked_signature = base_signature
                            blocked_reason = "种子训练多次显存不足，等待更多输入或正式 full train 接管。"
                            write_runtime_status(
                                status_path=status_path,
                                started_at=started_at,
                                current_tier=args.current_tier,
                                phase_name="seed_wait_more_input",
                                detail=blocked_reason,
                                progress_basis="seed_wait_more_input",
                                progress=48.0,
                                current_units=int((seed_manifest or {}).get("registered_images") or 0),
                                target_units=max(int((seed_manifest or {}).get("selected_frames") or 1), 1),
                                unit_label="张图",
                                extra_metrics={
                                    "upload_completed": bool(upload_completed),
                                    "seed_frame_limit": int(last_seed_frame_limit or 0),
                                    "oom_retry_count": int(next_retry_count),
                                    "seed_blocked": True,
                                },
                                title="种子训练等待正式阶段接管",
                            )
                    else:
                        blocked_signature = base_signature
                        blocked_reason = f"种子训练子进程退出（code={exit_code}），等待更多输入后再试。"
                        write_runtime_status(
                            status_path=status_path,
                            started_at=started_at,
                            current_tier=args.current_tier,
                            phase_name="seed_wait_more_input",
                            detail=blocked_reason,
                            progress_basis="seed_wait_more_input",
                            progress=48.0,
                            current_units=int((seed_manifest or {}).get("registered_images") or 0),
                            target_units=max(int((seed_manifest or {}).get("selected_frames") or 1), 1),
                            unit_label="张图",
                            extra_metrics={
                                "upload_completed": bool(upload_completed),
                                "seed_frame_limit": int(last_seed_frame_limit or 0),
                                "seed_blocked": True,
                            },
                            title="种子训练等待更多输入",
                        )
                    process = None
                    launched_signature = None
                    if upload_completed:
                        break
                    time.sleep(max(0.5, args.poll_interval_sec))
                    continue

            time.sleep(max(0.5, args.poll_interval_sec))
    finally:
        cleanup_process_group(process)


if __name__ == "__main__":
    main()
