from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..adapters.default_delivery_adapter import bake_default_texture_delivery
from ..config import config
from ..context import JobContext
from ..io_utils import write_json_atomic
from ..paths import ensure_job_layout

_PROGRESS_POLL_INTERVAL_SEC = 1.0


@dataclass(frozen=True)
class BakeAttemptPlan:
    attempt_index: int
    reason: str
    max_views: int
    atlas_size: int
    projection_image_size: int
    mesh_face_cap: int


def run_bake_default_texture(ctx: JobContext, *, progress_callback=None) -> None:
    ctx.current_stage = "bake_default_texture"
    _run_bake_default_texture_subprocess(ctx, progress_callback=progress_callback)


def _run_bake_default_texture_subprocess(
    ctx: JobContext,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    assert ctx.delivery_dir is not None
    progress_path = ctx.delivery_dir / "bake_default_texture.progress.json"
    log_path = ctx.delivery_dir / "bake_default_texture.log"
    runtime_path = ctx.delivery_dir / "bake_default_texture.runtime.json"
    for stale in (progress_path, log_path, runtime_path):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    attempt_plans = _build_bake_attempt_plans()
    runtime_payload: dict[str, Any] = {
        "job_id": ctx.job_id,
        "status": "running",
        "attempts": [],
        "updated_at_epoch_sec": time.time(),
    }
    _write_json(runtime_path, runtime_payload)

    last_error: str | None = None
    last_progress_signature: str | None = None
    for attempt_plan in attempt_plans:
        if attempt_plan.attempt_index > 0:
            _emit_retry_progress(
                progress_callback=progress_callback,
                attempt_plan=attempt_plan,
                total_attempts=len(attempt_plans),
            )

        attempt_result, last_progress_signature = _run_single_bake_attempt(
            ctx,
            attempt_plan=attempt_plan,
            progress_path=progress_path,
            log_path=log_path,
            runtime_path=runtime_path,
            progress_callback=progress_callback,
            last_progress_signature=last_progress_signature,
        )
        runtime_payload["attempts"].append(attempt_result)
        runtime_payload["selected_attempt"] = int(attempt_plan.attempt_index)
        runtime_payload["updated_at_epoch_sec"] = time.time()
        _write_json(runtime_path, runtime_payload)

        if int(attempt_result["return_code"]) == 0:
            runtime_payload["status"] = "ok"
            runtime_payload["updated_at_epoch_sec"] = time.time()
            _write_json(runtime_path, runtime_payload)
            return

        if _should_retry_bake_attempt(attempt_result) and attempt_plan != attempt_plans[-1]:
            continue

        last_error = _format_bake_failure(attempt_result, log_path=log_path)
        break

    runtime_payload["status"] = "failed"
    runtime_payload["updated_at_epoch_sec"] = time.time()
    if last_error is not None:
        runtime_payload["last_error"] = last_error
    _write_json(runtime_path, runtime_payload)
    raise RuntimeError(last_error or f"bake_default_texture_failed:log={log_path}")


def _build_bake_attempt_plans() -> list[BakeAttemptPlan]:
    plans = [
        BakeAttemptPlan(
            attempt_index=0,
            reason="initial_budget",
            max_views=max(1, int(config.delivery_texture_max_views)),
            atlas_size=max(256, int(config.delivery_texture_atlas_size)),
            projection_image_size=max(256, int(config.delivery_projection_image_size)),
            mesh_face_cap=max(8_192, int(config.delivery_bake_mesh_proxy_face_cap)),
        )
    ]

    max_retries = max(0, int(config.delivery_bake_max_retries))
    for _ in range(max_retries):
        previous = plans[-1]
        next_views = max(
            int(config.delivery_bake_retry_max_views_floor),
            int(previous.max_views * float(config.delivery_bake_retry_max_views_scale)),
        )
        next_atlas_size = max(
            int(config.delivery_bake_retry_atlas_floor),
            _round_down_to_multiple(
                int(previous.atlas_size * float(config.delivery_bake_retry_atlas_scale)),
                256,
            ),
        )
        next_projection_size = max(
            int(config.delivery_bake_retry_projection_floor),
            _round_down_to_multiple(
                int(previous.projection_image_size * float(config.delivery_bake_retry_projection_scale)),
                64,
            ),
        )
        next_mesh_face_cap = max(
            int(config.delivery_bake_retry_mesh_face_cap_floor),
            int(previous.mesh_face_cap * float(config.delivery_bake_retry_mesh_face_cap_scale)),
        )
        if (
            next_views == previous.max_views
            and next_atlas_size == previous.atlas_size
            and next_projection_size == previous.projection_image_size
            and next_mesh_face_cap == previous.mesh_face_cap
        ):
            break
        plans.append(
            BakeAttemptPlan(
                attempt_index=len(plans),
                reason="retry_tighter_texture_budget",
                max_views=int(next_views),
                atlas_size=int(next_atlas_size),
                projection_image_size=int(next_projection_size),
                mesh_face_cap=int(next_mesh_face_cap),
            )
        )

    return plans


def _emit_retry_progress(
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    attempt_plan: BakeAttemptPlan,
    total_attempts: int,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        {
            "progress": 0.02,
            "title": f"正在收紧 HQ 纹理预算并重试 {attempt_plan.attempt_index + 1}/{total_attempts}",
            "detail": "上一次 HQ 纹理投影被系统提前终止，这次会减少投影视角并缩小 atlas 继续回传成品。",
            "metrics": {
                "texture_phase": "retry_with_tighter_budget",
                "texture_attempt_index": str(attempt_plan.attempt_index),
                "texture_total_attempts": str(total_attempts),
                "texture_max_views": str(attempt_plan.max_views),
                "texture_atlas_size": str(attempt_plan.atlas_size),
                "texture_projection_image_size": str(attempt_plan.projection_image_size),
                "texture_mesh_face_cap": str(attempt_plan.mesh_face_cap),
            },
        }
    )


def _run_single_bake_attempt(
    ctx: JobContext,
    *,
    attempt_plan: BakeAttemptPlan,
    progress_path: Path,
    log_path: Path,
    runtime_path: Path,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    last_progress_signature: str | None,
) -> tuple[dict[str, Any], str | None]:
    command = [
        sys.executable,
        "-m",
        "worker_object_slam3r_surface_v1.pipeline.run_bake_default_texture",
        "--child",
        "--job-id",
        ctx.job_id,
        "--progress-path",
        str(progress_path),
        "--attempt-index",
        str(attempt_plan.attempt_index),
    ]
    env = dict(os.environ)
    env.update(
        {
            "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_MAX_VIEWS": str(attempt_plan.max_views),
            "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_ATLAS_SIZE": str(attempt_plan.atlas_size),
            "OBJECT_SLAM3R_SURFACE_DELIVERY_PROJECTION_IMAGE_SIZE": str(attempt_plan.projection_image_size),
            "OBJECT_SLAM3R_SURFACE_DELIVERY_BAKE_MESH_PROXY_FACE_CAP": str(attempt_plan.mesh_face_cap),
        }
    )

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(
            "[bake_default_texture] "
            f"attempt_start index={attempt_plan.attempt_index} "
            f"reason={attempt_plan.reason} "
            f"max_views={attempt_plan.max_views} "
            f"atlas_size={attempt_plan.atlas_size} "
            f"projection_image_size={attempt_plan.projection_image_size} "
            f"mesh_face_cap={attempt_plan.mesh_face_cap}\n"
        )
        log_file.flush()
        process = subprocess.Popen(
            command,
            cwd=os.getcwd(),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        start_time = time.time()
        timed_out = False
        stalled = False
        peak_rss_bytes = 0
        last_progress_change_time = time.time()
        last_liveness_time = last_progress_change_time
        last_cpu_jiffies = _read_linux_process_cpu_jiffies(process.pid)

        while True:
            previous_signature = last_progress_signature
            last_progress_signature = _relay_progress_if_updated(
                progress_path,
                progress_callback=progress_callback,
                last_signature=last_progress_signature,
            )
            if last_progress_signature != previous_signature:
                last_progress_change_time = time.time()
                last_liveness_time = last_progress_change_time
            peak_rss_bytes = max(peak_rss_bytes, _read_linux_process_rss_bytes(process.pid))
            current_cpu_jiffies = _read_linux_process_cpu_jiffies(process.pid)
            if current_cpu_jiffies > last_cpu_jiffies:
                last_cpu_jiffies = current_cpu_jiffies
                last_liveness_time = time.time()
            _write_json(
                runtime_path,
                {
                    "job_id": ctx.job_id,
                    "status": "running",
                    "updated_at_epoch_sec": time.time(),
                    "active_attempt": {
                        "attempt_index": int(attempt_plan.attempt_index),
                        "reason": attempt_plan.reason,
                        "pid": int(process.pid),
                        "elapsed_sec": round(time.time() - start_time, 2),
                        "peak_rss_bytes": int(peak_rss_bytes),
                        "peak_rss_mb": round(peak_rss_bytes / (1024.0 * 1024.0), 2),
                        "cpu_jiffies": int(last_cpu_jiffies),
                        "last_progress_change_epoch_sec": float(last_progress_change_time),
                        "last_liveness_epoch_sec": float(last_liveness_time),
                    },
                },
            )
            return_code = process.poll()
            if return_code is not None:
                break
            if time.time() - start_time > float(config.delivery_bake_subprocess_timeout_sec):
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                return_code = process.returncode
                break
            if time.time() - last_liveness_time > float(config.delivery_bake_stall_timeout_sec):
                stalled = True
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                return_code = process.returncode
                break
            time.sleep(_PROGRESS_POLL_INTERVAL_SEC)

        _relay_progress_if_updated(
            progress_path,
            progress_callback=progress_callback,
            last_signature=last_progress_signature,
        )

    exit_signal = -int(return_code) if isinstance(return_code, int) and return_code < 0 else None
    exit_signal_name = None
    if exit_signal is not None:
        try:
            exit_signal_name = signal.Signals(exit_signal).name
        except ValueError:
            exit_signal_name = f"SIG{exit_signal}"

    status = "ok"
    if timed_out:
        status = "timeout"
    elif stalled:
        status = "stalled"
    elif exit_signal is not None:
        status = "killed"
    elif int(return_code) != 0:
        status = "failed"

    return (
        {
            "attempt_index": int(attempt_plan.attempt_index),
            "reason": attempt_plan.reason,
            "max_views": int(attempt_plan.max_views),
            "atlas_size": int(attempt_plan.atlas_size),
            "projection_image_size": int(attempt_plan.projection_image_size),
            "mesh_face_cap": int(attempt_plan.mesh_face_cap),
            "pid": int(process.pid),
            "status": status,
            "timed_out": bool(timed_out),
            "stalled": bool(stalled),
            "return_code": int(return_code or 0),
            "exit_signal": exit_signal,
            "exit_signal_name": exit_signal_name,
            "peak_rss_bytes": int(peak_rss_bytes),
            "peak_rss_mb": round(peak_rss_bytes / (1024.0 * 1024.0), 2),
            "elapsed_sec": round(time.time() - start_time, 2),
            "cpu_jiffies": int(last_cpu_jiffies),
            "last_progress_change_epoch_sec": float(last_progress_change_time),
            "last_liveness_epoch_sec": float(last_liveness_time),
            "log_path": str(log_path),
        },
        last_progress_signature,
    )


def _should_retry_bake_attempt(attempt_result: dict[str, Any]) -> bool:
    if bool(attempt_result.get("timed_out")):
        return True
    if bool(attempt_result.get("stalled")):
        return True
    exit_signal = attempt_result.get("exit_signal")
    if isinstance(exit_signal, int) and exit_signal > 0:
        return True
    return int(attempt_result.get("return_code") or 0) in {137}


def _format_bake_failure(attempt_result: dict[str, Any], *, log_path: Path) -> str:
    peak_rss_mb = float(attempt_result.get("peak_rss_mb") or 0.0)
    if bool(attempt_result.get("timed_out")):
        return (
            "bake_default_texture_timeout:"
            f"timeout_sec={int(config.delivery_bake_subprocess_timeout_sec)}:"
            f"peak_rss_mb={peak_rss_mb:.2f}:"
            f"log={log_path}:"
            f"tail={_tail_text(log_path, line_count=80)}"
        )
    if bool(attempt_result.get("stalled")):
        return (
            "bake_default_texture_stalled:"
            f"stall_timeout_sec={int(config.delivery_bake_stall_timeout_sec)}:"
            f"peak_rss_mb={peak_rss_mb:.2f}:"
            f"log={log_path}:"
            f"tail={_tail_text(log_path, line_count=80)}"
        )
    return_code = int(attempt_result.get("return_code") or 0)
    exit_signal = attempt_result.get("exit_signal")
    exit_signal_name = attempt_result.get("exit_signal_name")
    if isinstance(exit_signal, int) and exit_signal > 0:
        return (
            "bake_default_texture_subprocess_failed:"
            f"return_code={return_code}:"
            f"exit_signal={exit_signal}:"
            f"exit_signal_name={exit_signal_name}:"
            f"peak_rss_mb={peak_rss_mb:.2f}:"
            f"log={log_path}:"
            f"tail={_tail_text(log_path, line_count=80)}"
        )
    return (
        "bake_default_texture_subprocess_failed:"
        f"return_code={return_code}:"
        f"peak_rss_mb={peak_rss_mb:.2f}:"
        f"log={log_path}:"
        f"tail={_tail_text(log_path, line_count=80)}"
    )


def _relay_progress_if_updated(
    progress_path: Path,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    last_signature: str | None,
) -> str | None:
    if progress_callback is None or not progress_path.exists():
        return last_signature
    try:
        raw = progress_path.read_text(encoding="utf-8")
    except OSError:
        return last_signature
    if not raw or raw == last_signature:
        return last_signature
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return last_signature
    if isinstance(payload, dict):
        progress_callback(payload)
        return raw
    return last_signature


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload, ensure_ascii=False)


def _tail_text(path: Path, *, line_count: int) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return " | ".join(line.strip() for line in lines[-line_count:] if line.strip())


def _read_linux_process_rss_bytes(pid: int) -> int:
    try:
        status_text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    for line in status_text.splitlines():
        if not line.startswith("VmRSS:"):
            continue
        parts = line.split()
        if len(parts) < 2:
            return 0
        try:
            return int(parts[1]) * 1024
        except ValueError:
            return 0
    return 0


def _read_linux_process_cpu_jiffies(pid: int) -> int:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    parts = stat_text.split()
    if len(parts) < 15:
        return 0
    try:
        utime = int(parts[13])
        stime = int(parts[14])
    except ValueError:
        return 0
    return utime + stime


def _child_progress_callback(progress_path: Path) -> Callable[[dict[str, Any]], None]:
    def callback(payload: dict[str, Any]) -> None:
        _write_json(progress_path, payload)

    return callback


def _build_local_ctx(job_id: str) -> JobContext:
    root_dir = Path(config.local_jobs_directory) / job_id
    input_dir = root_dir / "input"
    output_dir = root_dir / "output"
    ctx = JobContext(
        job_id=job_id,
        worker_id="bake-subprocess",
        assignment={},
        root_dir=root_dir,
        input_dir=input_dir,
        output_dir=output_dir,
        input_video=input_dir / "capture.mp4",
        output_prefix=f"artifacts/{job_id}/",
    )
    ensure_job_layout(ctx)
    _restore_local_ctx_paths(ctx)
    return ctx


def _restore_local_ctx_paths(ctx: JobContext) -> None:
    assert ctx.slam3r_dir is not None
    contract_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    if not contract_path.exists():
        return
    try:
        payload = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    scene_dir = payload.get("scene_dir")
    if scene_dir:
        ctx.sparse2dgs_scene_dir = Path(str(scene_dir))


def _run_child(job_id: str, progress_path: Path, attempt_index: int) -> int:
    ctx = _build_local_ctx(job_id)
    ctx.current_stage = "bake_default_texture"
    print(
        f"[bake_default_texture] child_start job_id={job_id} attempt_index={attempt_index}",
        flush=True,
    )
    _write_json(
        progress_path,
        {
            "progress": 0.02,
            "title": "正在准备投影 HQ 纹理",
            "detail": "正在载入优化后的 HQ 网格和投影视角。",
            "metrics": {
                "texture_phase": "child_start",
                "texture_attempt_index": str(attempt_index),
            },
        },
    )
    try:
        bake_default_texture_delivery(
            ctx,
            progress_callback=_child_progress_callback(progress_path),
        )
    except Exception:
        traceback.print_exc()
        return 1
    _write_json(
        progress_path,
        {
            "progress": 1.0,
            "title": "HQ 纹理投影完成",
            "detail": "纹理投影完成，正在准备写出成品与清单。",
            "metrics": {
                "texture_phase": "done",
                "texture_attempt_index": str(attempt_index),
            },
        },
    )
    print(f"[bake_default_texture] child_done job_id={job_id}", flush=True)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bake_default_texture in a subprocess-safe mode.")
    parser.add_argument("--child", action="store_true", help="Run the bake child worker inline.")
    parser.add_argument("--job-id", required=True, help="Local job id under config.local_jobs_directory.")
    parser.add_argument(
        "--progress-path",
        required=True,
        help="Path to the JSON progress file written by the child and polled by the parent.",
    )
    parser.add_argument(
        "--attempt-index",
        type=int,
        default=0,
        help="Retry attempt index used for progress metrics and budget overrides.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.child:
        raise SystemExit("--child is required when invoking this module directly")
    return _run_child(args.job_id, Path(args.progress_path), int(args.attempt_index))


def _round_down_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 0:
        return value
    return max(multiple, (value // multiple) * multiple)


if __name__ == "__main__":
    raise SystemExit(main())
