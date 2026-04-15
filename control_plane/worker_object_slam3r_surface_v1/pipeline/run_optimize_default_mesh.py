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

from ..adapters.default_delivery_adapter import optimize_default_mesh_delivery
from ..config import config
from ..context import JobContext
from ..io_utils import write_json_atomic
from ..paths import ensure_job_layout

_PROGRESS_POLL_INTERVAL_SEC = 1.0


@dataclass(frozen=True)
class OptimizeAttemptPlan:
    attempt_index: int
    reason: str
    working_mesh_trigger_faces: int
    working_mesh_face_cap: int
    working_mesh_min_faces: int
    working_mesh_target_ratio: float


def run_optimize_default_mesh(ctx: JobContext, *, progress_callback=None) -> None:
    ctx.current_stage = "optimize_default_mesh"
    _run_optimize_default_mesh_subprocess(ctx, progress_callback=progress_callback)


def _run_optimize_default_mesh_subprocess(
    ctx: JobContext,
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    assert ctx.delivery_dir is not None
    progress_path = ctx.delivery_dir / "optimize_default_mesh.progress.json"
    log_path = ctx.delivery_dir / "optimize_default_mesh.log"
    runtime_path = ctx.delivery_dir / "optimize_default_mesh.runtime.json"
    for stale in (progress_path, log_path, runtime_path):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    attempt_plans = _build_optimize_attempt_plans()
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

        attempt_result, last_progress_signature = _run_single_optimize_attempt(
            ctx,
            attempt_plan=attempt_plan,
            progress_path=progress_path,
            log_path=log_path,
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

        if _should_retry_optimize_attempt(attempt_result) and attempt_plan != attempt_plans[-1]:
            continue

        last_error = _format_optimize_failure(attempt_result, log_path=log_path)
        break

    runtime_payload["status"] = "failed"
    runtime_payload["updated_at_epoch_sec"] = time.time()
    if last_error is not None:
        runtime_payload["last_error"] = last_error
    _write_json(runtime_path, runtime_payload)
    raise RuntimeError(last_error or f"optimize_default_mesh_failed:log={log_path}")


def _build_optimize_attempt_plans() -> list[OptimizeAttemptPlan]:
    plans = [
        OptimizeAttemptPlan(
            attempt_index=0,
            reason="initial_budget",
            working_mesh_trigger_faces=int(config.delivery_working_mesh_trigger_faces),
            working_mesh_face_cap=int(config.delivery_working_mesh_face_cap),
            working_mesh_min_faces=int(config.delivery_working_mesh_min_faces),
            working_mesh_target_ratio=float(config.delivery_working_mesh_target_ratio),
        )
    ]

    max_retries = max(0, int(config.delivery_optimize_max_retries))
    for _ in range(max_retries):
        previous = plans[-1]
        next_face_cap = max(
            int(config.delivery_optimize_retry_face_cap_floor),
            int(round(previous.working_mesh_face_cap * float(config.delivery_optimize_retry_face_cap_scale))),
        )
        next_trigger = max(
            int(config.delivery_optimize_retry_trigger_face_floor),
            int(round(previous.working_mesh_trigger_faces * float(config.delivery_optimize_retry_trigger_face_scale))),
        )
        next_min_faces = max(
            int(config.delivery_optimize_retry_min_faces_floor),
            int(round(previous.working_mesh_min_faces * float(config.delivery_optimize_retry_min_faces_scale))),
        )
        next_target_ratio = max(
            float(config.delivery_optimize_retry_target_ratio_floor),
            previous.working_mesh_target_ratio * float(config.delivery_optimize_retry_target_ratio_scale),
        )
        next_face_cap = max(next_face_cap, next_min_faces)
        next_trigger = max(next_trigger, next_face_cap)

        if (
            next_face_cap == previous.working_mesh_face_cap
            and next_trigger == previous.working_mesh_trigger_faces
            and next_min_faces == previous.working_mesh_min_faces
            and abs(next_target_ratio - previous.working_mesh_target_ratio) < 1e-6
        ):
            break

        plans.append(
            OptimizeAttemptPlan(
                attempt_index=len(plans),
                reason="retry_tighter_working_mesh",
                working_mesh_trigger_faces=int(next_trigger),
                working_mesh_face_cap=int(next_face_cap),
                working_mesh_min_faces=int(next_min_faces),
                working_mesh_target_ratio=float(next_target_ratio),
            )
        )

    return plans


def _emit_retry_progress(
    *,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    attempt_plan: OptimizeAttemptPlan,
    total_attempts: int,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        {
            "progress": 0.02,
            "title": f"正在收紧 HQ 网格预算并重试 {attempt_plan.attempt_index + 1}/{total_attempts}",
            "detail": (
                "上一次网格优化被系统提前终止，这次会更早构建 working mesh，"
                "并用更保守的面数预算继续优化。"
            ),
            "metrics": {
                "optimize_phase": "retry_with_tighter_budget",
                "optimize_attempt_index": str(attempt_plan.attempt_index),
                "optimize_total_attempts": str(total_attempts),
                "working_mesh_trigger_faces": str(attempt_plan.working_mesh_trigger_faces),
                "working_mesh_face_cap": str(attempt_plan.working_mesh_face_cap),
                "working_mesh_min_faces": str(attempt_plan.working_mesh_min_faces),
                "working_mesh_target_ratio": f"{attempt_plan.working_mesh_target_ratio:.5f}",
            },
        }
    )


def _run_single_optimize_attempt(
    ctx: JobContext,
    *,
    attempt_plan: OptimizeAttemptPlan,
    progress_path: Path,
    log_path: Path,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    last_progress_signature: str | None,
) -> tuple[dict[str, Any], str | None]:
    command = [
        sys.executable,
        "-m",
        "worker_object_slam3r_surface_v1.pipeline.run_optimize_default_mesh",
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
            "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_TRIGGER_FACES": str(attempt_plan.working_mesh_trigger_faces),
            "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_FACE_CAP": str(attempt_plan.working_mesh_face_cap),
            "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_MIN_FACES": str(attempt_plan.working_mesh_min_faces),
            "OBJECT_SLAM3R_SURFACE_DELIVERY_WORKING_MESH_TARGET_RATIO": f"{attempt_plan.working_mesh_target_ratio:.6f}",
        }
    )

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(
            "[optimize_default_mesh] "
            f"attempt_start index={attempt_plan.attempt_index} "
            f"reason={attempt_plan.reason} "
            f"trigger_faces={attempt_plan.working_mesh_trigger_faces} "
            f"face_cap={attempt_plan.working_mesh_face_cap} "
            f"min_faces={attempt_plan.working_mesh_min_faces} "
            f"target_ratio={attempt_plan.working_mesh_target_ratio:.6f}\n"
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
        peak_rss_bytes = 0

        while True:
            last_progress_signature = _relay_progress_if_updated(
                progress_path,
                progress_callback=progress_callback,
                last_signature=last_progress_signature,
            )
            peak_rss_bytes = max(peak_rss_bytes, _read_linux_process_rss_bytes(process.pid))
            return_code = process.poll()
            if return_code is not None:
                break
            if time.time() - start_time > float(config.delivery_optimize_subprocess_timeout_sec):
                timed_out = True
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
    elif exit_signal is not None:
        status = "killed"
    elif int(return_code) != 0:
        status = "failed"

    return (
        {
            "attempt_index": int(attempt_plan.attempt_index),
            "reason": attempt_plan.reason,
            "working_mesh_trigger_faces": int(attempt_plan.working_mesh_trigger_faces),
            "working_mesh_face_cap": int(attempt_plan.working_mesh_face_cap),
            "working_mesh_min_faces": int(attempt_plan.working_mesh_min_faces),
            "working_mesh_target_ratio": float(attempt_plan.working_mesh_target_ratio),
            "pid": int(process.pid),
            "status": status,
            "timed_out": bool(timed_out),
            "return_code": int(return_code or 0),
            "exit_signal": exit_signal,
            "exit_signal_name": exit_signal_name,
            "elapsed_sec": round(time.time() - start_time, 2),
            "peak_rss_bytes": int(peak_rss_bytes),
            "peak_rss_mb": round(peak_rss_bytes / (1024.0 * 1024.0), 2),
            "log_path": str(log_path),
        },
        last_progress_signature,
    )


def _should_retry_optimize_attempt(attempt_result: dict[str, Any]) -> bool:
    if bool(attempt_result.get("timed_out")):
        return True
    exit_signal = attempt_result.get("exit_signal")
    if isinstance(exit_signal, int) and exit_signal > 0:
        return True
    return int(attempt_result.get("return_code") or 0) in {137}


def _format_optimize_failure(attempt_result: dict[str, Any], *, log_path: Path) -> str:
    log_tail = _tail_text(log_path, line_count=80)
    return_code = int(attempt_result.get("return_code") or 0)
    peak_rss_mb = float(attempt_result.get("peak_rss_mb") or 0.0)
    if bool(attempt_result.get("timed_out")):
        return (
            "optimize_default_mesh_timeout:"
            f"timeout_sec={int(config.delivery_optimize_subprocess_timeout_sec)}:"
            f"peak_rss_mb={peak_rss_mb:.2f}:"
            f"log={log_path}:"
            f"tail={log_tail}"
        )
    exit_signal = attempt_result.get("exit_signal")
    exit_signal_name = attempt_result.get("exit_signal_name")
    if isinstance(exit_signal, int) and exit_signal > 0:
        return (
            "optimize_default_mesh_subprocess_failed:"
            f"return_code={return_code}:"
            f"exit_signal={exit_signal}:"
            f"exit_signal_name={exit_signal_name}:"
            f"peak_rss_mb={peak_rss_mb:.2f}:"
            f"log={log_path}:"
            f"tail={log_tail}"
        )
    return (
        "optimize_default_mesh_subprocess_failed:"
        f"return_code={return_code}:"
        f"peak_rss_mb={peak_rss_mb:.2f}:"
        f"log={log_path}:"
        f"tail={log_tail}"
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


def _tail_text(path: Path, *, line_count: int) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return " | ".join(line.strip() for line in lines[-line_count:] if line.strip())


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    write_json_atomic(path, payload, ensure_ascii=False)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_progress(path, payload)


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


def _child_progress_callback(progress_path: Path) -> Callable[[dict[str, Any]], None]:
    def callback(payload: dict[str, Any]) -> None:
        _write_progress(progress_path, payload)

    return callback


def _build_local_ctx(job_id: str) -> JobContext:
    root_dir = Path(config.local_jobs_directory) / job_id
    input_dir = root_dir / "input"
    output_dir = root_dir / "output"
    ctx = JobContext(
        job_id=job_id,
        worker_id="optimize-subprocess",
        assignment={},
        root_dir=root_dir,
        input_dir=input_dir,
        output_dir=output_dir,
        input_video=input_dir / "capture.mp4",
        output_prefix=f"artifacts/{job_id}/",
    )
    ensure_job_layout(ctx)
    return ctx


def _run_child(job_id: str, progress_path: Path, *, attempt_index: int) -> int:
    ctx = _build_local_ctx(job_id)
    ctx.current_stage = "optimize_default_mesh"
    print(
        "[optimize_default_mesh] "
        f"child_start job_id={job_id} "
        f"attempt_index={attempt_index} "
        f"trigger_faces={config.delivery_working_mesh_trigger_faces} "
        f"face_cap={config.delivery_working_mesh_face_cap} "
        f"min_faces={config.delivery_working_mesh_min_faces} "
        f"target_ratio={config.delivery_working_mesh_target_ratio:.6f}",
        flush=True,
    )
    try:
        optimize_default_mesh_delivery(
            ctx,
            progress_callback=_child_progress_callback(progress_path),
        )
    except Exception:
        traceback.print_exc()
        return 1
    print(f"[optimize_default_mesh] child_done job_id={job_id}", flush=True)
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run optimize_default_mesh in a subprocess-safe mode.")
    parser.add_argument("--child", action="store_true", help="Run the optimize child worker inline.")
    parser.add_argument("--job-id", required=True, help="Local job id under config.local_jobs_directory.")
    parser.add_argument("--attempt-index", type=int, default=0, help="Optimize attempt index for logging.")
    parser.add_argument(
        "--progress-path",
        required=True,
        help="Path to the JSON progress file written by the child and polled by the parent.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.child:
        raise SystemExit("--child is required when invoking this module directly")
    return _run_child(args.job_id, Path(args.progress_path), attempt_index=int(args.attempt_index))


if __name__ == "__main__":
    raise SystemExit(main())
