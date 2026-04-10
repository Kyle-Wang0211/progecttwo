from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..config import config
from ..context import JobContext


_PROGRESS_PREFIX = "AETHER_PROGRESS "


def _emit_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    stage: str,
    title: str,
    detail: str,
    progress_fraction: float,
    metrics: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    callback(
        {
            "stage": stage,
            "title": title,
            "detail": detail,
            "progress_fraction": progress_fraction,
            "metrics": metrics or {},
        }
    )


def _run_text_command(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except Exception:
        return ""
    return completed.stdout or ""


def _collect_process_tree_metrics(root_pid: int) -> dict[str, Any]:
    stdout = _run_text_command(
        ["ps", "-axo", "pid=,ppid=,%cpu=,%mem=,stat=,command="]
    )
    rows: dict[int, dict[str, Any]] = {}
    children_by_parent: dict[int, list[int]] = {}
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            cpu_percent = float(parts[2])
            mem_percent = float(parts[3])
        except ValueError:
            continue
        rows[pid] = {
            "pid": pid,
            "ppid": ppid,
            "cpu_percent": cpu_percent,
            "mem_percent": mem_percent,
            "state": parts[4],
            "command": parts[5],
        }
        children_by_parent.setdefault(ppid, []).append(pid)

    tree_pids: list[int] = []
    stack = [root_pid]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in rows:
            tree_pids.append(current)
        stack.extend(children_by_parent.get(current, []))

    elapsed_text = _run_text_command(["ps", "-p", str(root_pid), "-o", "etime="]).strip()
    total_cpu = sum(float(rows[pid]["cpu_percent"]) for pid in tree_pids if pid in rows)
    total_mem = sum(float(rows[pid]["mem_percent"]) for pid in tree_pids if pid in rows)
    child_count = max(0, len(tree_pids) - 1)
    return {
        "pids": tree_pids,
        "child_count": child_count,
        "cpu_percent_total": round(total_cpu, 2),
        "mem_percent_total": round(total_mem, 2),
        "elapsed": elapsed_text,
    }


def _collect_gpu_metrics(tree_pids: set[int]) -> dict[str, Any]:
    app_stdout = _run_text_command(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    gpu_memory_mb = 0
    matched_gpu_pids = 0
    for raw_line in app_stdout.splitlines():
        parts = [item.strip() for item in raw_line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            used_memory_mb = float(parts[2])
        except ValueError:
            continue
        if pid in tree_pids:
            matched_gpu_pids += 1
            gpu_memory_mb += used_memory_mb

    util_stdout = _run_text_command(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    gpu_utilization = 0.0
    memory_utilization = 0.0
    total_used_mb = 0.0
    total_memory_mb = 0.0
    first_line = next((line.strip() for line in util_stdout.splitlines() if line.strip()), "")
    if first_line:
        parts = [item.strip() for item in first_line.split(",")]
        if len(parts) >= 4:
            try:
                gpu_utilization = float(parts[0])
                memory_utilization = float(parts[1])
                total_used_mb = float(parts[2])
                total_memory_mb = float(parts[3])
            except ValueError:
                pass

    return {
        "gpu_process_count": matched_gpu_pids,
        "gpu_memory_mb": round(gpu_memory_mb, 1),
        "gpu_utilization_percent": round(gpu_utilization, 1),
        "gpu_memory_utilization_percent": round(memory_utilization, 1),
        "gpu_total_used_mb": round(total_used_mb, 1),
        "gpu_total_memory_mb": round(total_memory_mb, 1),
    }


def _finalize_alive_detail(metrics: dict[str, Any]) -> str:
    cpu_total = metrics.get("cpu_percent_total", 0.0)
    gpu_util = metrics.get("gpu_utilization_percent", 0.0)
    gpu_mem = metrics.get("gpu_memory_mb", 0.0)
    child_count = metrics.get("child_count", 0)
    output_age = metrics.get("last_output_age_sec", 0)
    elapsed = metrics.get("elapsed", "")
    elapsed_suffix = f" 已运行 {elapsed}。" if elapsed else ""
    return (
        f"原生 finalize 仍在运行：CPU {cpu_total:.0f}%，GPU {gpu_util:.0f}%，"
        f"子进程 {child_count} 个，GPU 显存 {gpu_mem:.0f}MB，距上次输出 {output_age:.0f}s。"
        f"{elapsed_suffix}"
    )


def run_splatslam_pipeline(
    ctx: JobContext,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    assert ctx.splatslam_dir is not None
    assert ctx.curated_dir is not None

    summary_path = ctx.splatslam_dir / "splatslam.json"
    runner = config.splatslam_runner_script.strip()
    repo_path = Path(config.splatslam_repo).expanduser()

    runner_path = Path(runner).expanduser()
    if not runner_path.exists():
        raise RuntimeError(f"splatslam_runner_missing:{runner_path}")
    if not repo_path.exists():
        raise RuntimeError(f"splatslam_repo_missing:{repo_path}")
    command = [
        config.splatslam_python_bin,
        str(runner_path),
        "--repo",
        str(repo_path),
        "--entrypoint",
        config.splatslam_entrypoint,
        "--images-dir",
        str(ctx.curated_dir),
        "--masks-dir",
        str(ctx.masks_dir) if ctx.masks_dir else "",
        "--output-dir",
        str(ctx.splatslam_dir),
        "--summary-json",
        str(summary_path),
        "--export-format",
        config.splatslam_export_format,
        "--horizontal-fov-degrees",
        str(config.splatslam_assumed_horizontal_fov_degrees),
        "--final-refine-iters",
        str(config.splatslam_final_refine_iters),
        "--tracking-warmup",
        str(config.splatslam_tracking_warmup),
        "--init-iters",
        str(config.splatslam_init_iters),
        "--mapping-iters",
        str(config.splatslam_mapping_iters),
        "--frontend-init-update-iters",
        str(config.splatslam_frontend_init_update_iters),
        "--frontend-init-proximity-radius",
        str(config.splatslam_frontend_init_proximity_radius),
        "--frontend-init-proximity-nms",
        str(config.splatslam_frontend_init_proximity_nms),
        "--frontend-keyframe-thresh",
        str(config.splatslam_frontend_keyframe_thresh),
        "--frontend-motion-filter-thresh",
        str(config.splatslam_frontend_motion_filter_thresh),
        "--frontend-window",
        str(config.splatslam_frontend_window),
        "--frontend-max-factors",
        str(config.splatslam_frontend_max_factors),
        "--frontend-lowmem-steps",
        str(config.splatslam_frontend_lowmem_steps),
        "--frontend-skip-video-ba",
        "1" if config.splatslam_frontend_skip_video_ba else "0",
        "--output-max-edge",
        str(config.splatslam_output_max_edge),
    ]
    if config.splatslam_frontend_use_lowmem_update:
        command.append("--frontend-use-lowmem-update")
    if config.splatslam_enable_online_ba:
        command.append("--enable-online-ba")
    if config.splatslam_apply_masks_to_rgb:
        command.append("--apply-masks-to-rgb")

    total_curated_frames = max(1, len(list(ctx.curated_dir.glob("*.jpg"))))
    last_mapping_frame = -1
    last_output_line = ""
    last_output_at = time.monotonic()
    current_stage = "splatslam_prepare"
    current_title = "正在准备 Splat-SLAM"
    current_detail = "正在准备 object-first 输入并启动 Splat-SLAM。"
    current_progress = 0.46
    progress_lock = threading.Lock()

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    stop_event = threading.Event()

    def _forward_progress(payload: dict[str, Any]) -> None:
        nonlocal current_stage, current_title, current_detail, current_progress
        stage = str(payload.get("stage") or current_stage)
        title = str(payload.get("title") or current_title)
        detail = str(payload.get("detail") or current_detail)
        progress_fraction = float(payload.get("progress_fraction") or current_progress)
        with progress_lock:
            current_stage = stage
            current_title = title
            current_detail = detail
            current_progress = progress_fraction
        if progress_callback is not None:
            progress_callback(payload)

    def _alive_probe_loop() -> None:
        interval = max(5.0, float(config.splatslam_alive_probe_interval_sec))
        while not stop_event.wait(interval):
            if process.poll() is not None:
                return
            with progress_lock:
                stage = current_stage
                title = current_title
                progress_fraction = current_progress
                output_age_sec = max(0.0, time.monotonic() - last_output_at)
            if stage != "splatslam_finalize":
                continue
            process_metrics = _collect_process_tree_metrics(process.pid)
            gpu_metrics = _collect_gpu_metrics(set(process_metrics.get("pids", [])))
            metrics = {
                **process_metrics,
                **gpu_metrics,
                "last_output_age_sec": round(output_age_sec, 1),
            }
            detail = _finalize_alive_detail(metrics)
            print(
                "[object_splatslam_v1] "
                f"job={ctx.job_id} stage=splatslam_finalize alive "
                f"cpu_total={metrics['cpu_percent_total']} "
                f"gpu_util={metrics['gpu_utilization_percent']} "
                f"gpu_mem_mb={metrics['gpu_memory_mb']} "
                f"child_count={metrics['child_count']} "
                f"last_output_age_sec={metrics['last_output_age_sec']}",
                flush=True,
            )
            _emit_progress(
                progress_callback,
                stage=stage,
                title=title,
                detail=detail,
                progress_fraction=progress_fraction,
                metrics=metrics,
            )

    alive_thread = threading.Thread(
        target=_alive_probe_loop,
        name=f"splatslam-alive-{ctx.job_id[:8]}",
        daemon=True,
    )
    alive_thread.start()
    try:
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            last_output_line = line
            last_output_at = time.monotonic()
            if line.startswith(_PROGRESS_PREFIX):
                payload_raw = line[len(_PROGRESS_PREFIX):]
                try:
                    payload = json.loads(payload_raw)
                except json.JSONDecodeError:
                    continue
                _forward_progress(payload)
                continue

            if "Mapping Frame" in line:
                suffix = line.split("Mapping Frame", 1)[1].strip()
                try:
                    frame_index = int(suffix)
                except ValueError:
                    frame_index = None
                if frame_index is not None and frame_index != last_mapping_frame:
                    last_mapping_frame = frame_index
                    mapping_ratio = max(0.0, min(1.0, frame_index / total_curated_frames))
                    _forward_progress(
                        {
                            "stage": "splatslam_mapping",
                            "title": "正在执行 Splat-SLAM 映射",
                            "detail": f"正在融合第 {frame_index}/{total_curated_frames} 张关键帧。",
                            "progress_fraction": 0.60 + mapping_ratio * 0.12,
                            "metrics": {
                                "splatslam_mapping_frame": frame_index,
                                "splatslam_curated_frames": total_curated_frames,
                            },
                        }
                    )
                continue

            if "Tracking Done!" in line or "Mapping Done!" in line:
                _forward_progress(
                    {
                        "stage": "splatslam_finalize",
                        "title": "正在完成 Splat-SLAM",
                        "detail": "正在保存位姿、gaussians 和默认对象 splat。",
                        "progress_fraction": 0.74,
                    }
                )

        return_code = process.wait()
    finally:
        stop_event.set()
        alive_thread.join(timeout=1.0)
        if process.stdout:
            process.stdout.close()

    if return_code != 0:
        raise RuntimeError(f"splatslam_failed:{last_output_line}")

    if not summary_path.exists():
        raise RuntimeError("splatslam_summary_missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    keyframe_count = int(summary.get("keyframe_count", 0) or 0)
    if keyframe_count < max(1, config.splatslam_min_keyframes):
        raise RuntimeError(
            f"splatslam_insufficient_keyframes:{keyframe_count}"
        )
    return summary
