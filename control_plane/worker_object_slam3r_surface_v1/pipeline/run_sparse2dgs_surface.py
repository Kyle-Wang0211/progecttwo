from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from ..adapters.sparse2dgs_adapter import run_sparse2dgs_surface_reconstruction
from ..config import config
from ..context import JobContext
from ..io_utils import write_json_atomic
from ..paths import ensure_job_layout

_PROGRESS_POLL_INTERVAL_SEC = 1.0
_RUNTIME_HEARTBEAT_INTERVAL_SEC = 5.0


def run_sparse2dgs_surface(
    ctx: JobContext,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    ctx.current_stage = "sparse2dgs_surface"
    _run_sparse2dgs_surface_subprocess(ctx, progress_callback=progress_callback)


def _run_sparse2dgs_surface_subprocess(
    ctx: JobContext,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    assert ctx.sparse2dgs_dir is not None
    progress_path = ctx.sparse2dgs_dir / "sparse2dgs_surface.progress.json"
    log_path = ctx.sparse2dgs_dir / "sparse2dgs_surface.log"
    runtime_path = ctx.sparse2dgs_dir / "sparse2dgs_surface.runtime.json"
    for stale in (progress_path, log_path, runtime_path):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass

    runtime_payload: dict[str, Any] = {
        "job_id": ctx.job_id,
        "status": "running",
        "stage": "sparse2dgs_surface",
        "timeout_sec": int(config.sparse2dgs_stage_timeout_sec),
        "updated_at_epoch_sec": time.time(),
    }
    _write_json(runtime_path, runtime_payload)

    command = [
        sys.executable,
        "-m",
        "worker_object_slam3r_surface_v1.pipeline.run_sparse2dgs_surface",
        "--child",
        "--job-id",
        ctx.job_id,
        "--progress-path",
        str(progress_path),
    ]

    last_signature: str | None = None
    last_progress_epoch_sec = time.time()
    last_runtime_write_epoch_sec = 0.0
    latest_training_step = 0
    latest_training_total_steps = 0
    latest_training_progress_percent = 0.0
    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write("[sparse2dgs_surface] child_start\n")
        log_file.flush()
        process = subprocess.Popen(
            command,
            cwd=os.getcwd(),
            env=dict(os.environ),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        start_time = time.time()
        timed_out = False
        peak_rss_bytes = 0

        while True:
            last_signature, latest_payload = _relay_progress_if_updated(
                progress_path,
                progress_callback=progress_callback,
                last_signature=last_signature,
            )
            if latest_payload:
                latest_training_step = int(latest_payload.get("training_step") or 0)
                latest_training_total_steps = int(latest_payload.get("training_total_steps") or 0)
                latest_training_progress_percent = float(latest_payload.get("training_progress_percent") or 0.0)
                last_progress_epoch_sec = time.time()

            current_rss_bytes = _read_linux_process_rss_bytes(process.pid)
            peak_rss_bytes = max(peak_rss_bytes, current_rss_bytes)
            now = time.time()
            runtime_payload.update(
                {
                    "status": "running",
                    "child_pid": process.pid,
                    "training_step": latest_training_step,
                    "training_total_steps": latest_training_total_steps,
                    "training_progress_percent": latest_training_progress_percent,
                    "current_rss_bytes": int(current_rss_bytes),
                    "current_rss_mb": round(current_rss_bytes / (1024.0 * 1024.0), 2),
                    "peak_rss_bytes": int(peak_rss_bytes),
                    "peak_rss_mb": round(peak_rss_bytes / (1024.0 * 1024.0), 2),
                    "last_progress_update_epoch_sec": last_progress_epoch_sec,
                    "stalled_for_sec": round(max(0.0, now - last_progress_epoch_sec), 2),
                    "updated_at_epoch_sec": now,
                }
            )
            if latest_payload or now - last_runtime_write_epoch_sec >= _RUNTIME_HEARTBEAT_INTERVAL_SEC:
                _write_json(runtime_path, runtime_payload)
                last_runtime_write_epoch_sec = now

            return_code = process.poll()
            if return_code is not None:
                break
            if time.time() - start_time > float(config.sparse2dgs_stage_timeout_sec):
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

        _, latest_payload = _relay_progress_if_updated(
            progress_path,
            progress_callback=progress_callback,
            last_signature=last_signature,
        )
        if latest_payload:
            runtime_payload.update(
                {
                    "training_step": int(latest_payload.get("training_step") or 0),
                    "training_total_steps": int(latest_payload.get("training_total_steps") or 0),
                    "training_progress_percent": float(latest_payload.get("training_progress_percent") or 0.0),
                }
            )

    exit_signal = -int(return_code) if isinstance(return_code, int) and return_code < 0 else None
    exit_signal_name = None
    if exit_signal is not None:
        try:
            exit_signal_name = signal.Signals(exit_signal).name
        except ValueError:
            exit_signal_name = f"SIG{exit_signal}"

    runtime_payload.update(
        {
            "return_code": int(return_code or 0),
            "timed_out": bool(timed_out),
            "exit_signal": exit_signal,
            "exit_signal_name": exit_signal_name,
            "elapsed_sec": round(time.time() - start_time, 2),
            "peak_rss_bytes": int(peak_rss_bytes),
            "peak_rss_mb": round(peak_rss_bytes / (1024.0 * 1024.0), 2),
            "updated_at_epoch_sec": time.time(),
        }
    )

    if timed_out:
        runtime_payload["status"] = "failed"
        runtime_payload["error"] = "sparse2dgs_surface_subprocess_timeout"
        _write_json(runtime_path, runtime_payload)
        raise RuntimeError("sparse2dgs_surface_subprocess_timeout")

    if int(return_code or 0) != 0:
        runtime_payload["status"] = "failed"
        runtime_payload["error"] = (
            "sparse2dgs_surface_subprocess_failed:"
            f"return_code={int(return_code or 0)}:log={log_path}"
        )
        _write_json(runtime_path, runtime_payload)
        raise RuntimeError(str(runtime_payload["error"]))

    runtime_payload["status"] = "ok"
    _write_json(runtime_path, runtime_payload)


def _run_sparse2dgs_surface_child(
    ctx: JobContext,
    *,
    progress_path: Path,
) -> None:
    ctx.current_stage = "sparse2dgs_surface"

    def callback(step: int, total: int) -> None:
        _write_json(
            progress_path,
            {
                "progress": min(max(step / max(total, 1), 0.0), 1.0),
                "title": "正在执行 Sparse2DGS 表面重建",
                "detail": f"当前训练 {step}/{total}",
                "metrics": {
                    "training_step": str(step),
                    "training_total_steps": str(total),
                    "training_progress_percent": f"{(step / max(total, 1)) * 100:.1f}",
                },
            },
        )

    run_sparse2dgs_surface_reconstruction(ctx, progress_callback=callback)


def _relay_progress_if_updated(
    progress_path: Path,
    *,
    progress_callback: Callable[[int, int], None] | None,
    last_signature: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    payload = _read_json(progress_path)
    if payload is None:
        return last_signature, None
    signature = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    if signature == last_signature:
        return last_signature, payload

    metrics = payload.get("metrics") if isinstance(payload, dict) else None
    step = 0
    total = 0
    if isinstance(metrics, dict):
        try:
            step = int(metrics.get("training_step") or 0)
        except (TypeError, ValueError):
            step = 0
        try:
            total = int(metrics.get("training_total_steps") or 0)
        except (TypeError, ValueError):
            total = 0
    if progress_callback is not None and total > 0:
        progress_callback(step, total)
    return signature, {
        "training_step": step,
        "training_total_steps": total,
        "training_progress_percent": float(metrics.get("training_progress_percent") or 0.0) if isinstance(metrics, dict) else 0.0,
    }


def _read_linux_process_rss_bytes(pid: int) -> int:
    status_path = Path(f"/proc/{pid}/status")
    try:
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("VmRSS:"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return 0
            return int(parts[1]) * 1024
    except OSError:
        return 0
    return 0


def _write_json(path: Path, payload: dict[str, object]) -> None:
    write_json_atomic(path, payload, ensure_ascii=False)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _build_local_ctx(job_id: str) -> JobContext:
    root_dir = Path(config.local_jobs_directory) / job_id
    input_dir = root_dir / "input"
    output_dir = root_dir / "output"
    ctx = JobContext(
        job_id=job_id,
        worker_id="sparse2dgs-subprocess",
        assignment={},
        root_dir=root_dir,
        input_dir=input_dir,
        output_dir=output_dir,
        input_video=input_dir / "capture.mp4",
        output_prefix=f"artifacts/{job_id}/",
    )
    ensure_job_layout(ctx)
    return ctx


def _main() -> int:
    parser = argparse.ArgumentParser(description="Run Sparse2DGS surface reconstruction.")
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--progress-path", type=Path, required=True)
    args = parser.parse_args()

    if not args.child:
        raise RuntimeError("run_sparse2dgs_surface_requires_child")

    ctx = _build_local_ctx(args.job_id)
    try:
        _run_sparse2dgs_surface_child(ctx, progress_path=args.progress_path)
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
