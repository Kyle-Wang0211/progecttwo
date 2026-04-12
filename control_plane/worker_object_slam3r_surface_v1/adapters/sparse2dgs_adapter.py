from __future__ import annotations

import json
import math
import os
import re
import selectors
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .contract_bridge_adapter import bridge_slam3r_to_sparse2dgs_scene
from ..config import config
from ..context import JobContext

SPARSE2DGS_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2025/html/"
    "Wu_Sparse2DGS_Geometry-Prioritized_Gaussian_Splatting_for_Surface_Reconstruction_from_Sparse_Views_CVPR_2025_paper.html"
)
SPARSE2DGS_TRAINING_STEP_PATTERN = re.compile(r"Training progress:.*?\|\s*(\d+)/(\d+)")


@dataclass(frozen=True)
class Sparse2DGSAttemptPlan:
    attempt_index: int
    target_views: int
    max_image_size: int
    total_pixel_budget: int
    reason: str


def run_sparse2dgs_surface_reconstruction(
    ctx: JobContext,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    assert ctx.slam3r_dir is not None
    assert ctx.sparse2dgs_dir is not None

    command = _render_command(
        template=config.sparse2dgs_command_template,
        ctx=ctx,
        repo_dir=Path(config.sparse2dgs_repo),
    )
    if not command:
        raise RuntimeError("sparse2dgs_command_not_configured")

    attempts_dir = ctx.sparse2dgs_dir / "_attempt_logs"
    attempts_dir.mkdir(parents=True, exist_ok=True)
    initial_contract = _load_sparse2dgs_scene_contract(ctx)
    attempt_plans = _build_sparse2dgs_attempt_plans(initial_contract)
    attempt_summaries: list[dict[str, object]] = []
    final_log_path = ctx.sparse2dgs_dir / "sparse2dgs_train.log"
    final_attempt_plan: Sparse2DGSAttemptPlan | None = None

    try:
        for attempt_plan in attempt_plans:
            if attempt_plan.attempt_index > 0:
                _rebuild_sparse2dgs_scene_for_attempt(ctx, attempt_plan)
            current_contract = _load_sparse2dgs_scene_contract(ctx)
            _reset_sparse2dgs_output_dir(ctx.sparse2dgs_dir, preserve={attempts_dir.name})
            log_path = attempts_dir / f"sparse2dgs_train_attempt_{attempt_plan.attempt_index + 1}.log"
            try:
                _run_sparse2dgs_command(
                    command,
                    cwd=str(Path(config.sparse2dgs_repo)),
                    timeout=config.sparse2dgs_stage_timeout_sec,
                    log_path=log_path,
                    progress_callback=progress_callback,
                )
            except subprocess.CalledProcessError as exc:
                oom_reason = _parse_sparse2dgs_oom_reason(log_path, ctx)
                attempt_summaries.append(
                    {
                        "attempt_index": int(attempt_plan.attempt_index),
                        "target_views": int(current_contract.get("selected_frame_count") or attempt_plan.target_views),
                        "exported_image_size": current_contract.get("exported_image_size"),
                        "total_pixel_budget": int(
                            current_contract.get("bridge_budget", {}).get("total_pixel_budget")
                            or attempt_plan.total_pixel_budget
                        ),
                        "reason": attempt_plan.reason,
                        "status": "oom_retry" if oom_reason is not None else "failed",
                        "log_path": str(log_path),
                        "error": oom_reason or str(exc),
                    }
                )
                shutil.copy2(log_path, final_log_path)
                if oom_reason is not None and attempt_plan != attempt_plans[-1]:
                    continue
                if oom_reason is not None:
                    raise RuntimeError(oom_reason) from exc
                raise

            shutil.copy2(log_path, final_log_path)
            final_attempt_plan = attempt_plan
            attempt_summaries.append(
                {
                    "attempt_index": int(attempt_plan.attempt_index),
                    "target_views": int(current_contract.get("selected_frame_count") or attempt_plan.target_views),
                    "exported_image_size": current_contract.get("exported_image_size"),
                    "total_pixel_budget": int(
                        current_contract.get("bridge_budget", {}).get("total_pixel_budget")
                        or attempt_plan.total_pixel_budget
                    ),
                    "reason": attempt_plan.reason,
                    "status": "ok",
                    "log_path": str(log_path),
                }
            )
            break
        else:
            raise RuntimeError("sparse2dgs_attempts_exhausted")
    finally:
        _clear_sparse2dgs_attempt_overrides(ctx)

    if not final_log_path.exists():
        raise RuntimeError("sparse2dgs_train_log_missing")

    default_asset = _resolve_sparse2dgs_default_asset(ctx.sparse2dgs_dir)

    summary = {
        "paper": "Sparse2DGS",
        "paper_url": SPARSE2DGS_PAPER_URL,
        "repo": config.sparse2dgs_repo,
        "command": command,
        "scene_dir": str(ctx.sparse2dgs_scene_dir or ""),
        "slam3r_summary": str(ctx.slam3r_dir / config.slam3r_summary_filename),
        "output_dir": str(ctx.sparse2dgs_dir),
        "default_asset": str(default_asset),
        "train_log": str(final_log_path),
        "attempts": attempt_summaries,
        "selected_attempt": int(final_attempt_plan.attempt_index) if final_attempt_plan is not None else 0,
    }
    (ctx.sparse2dgs_dir / config.sparse2dgs_summary_filename).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _render_command(*, template: str, ctx: JobContext, repo_dir: Path) -> list[str]:
    if not template.strip():
        return []
    rendered = template.format(
        curated_dir=str(ctx.curated_dir),
        slam3r_dir=str(ctx.slam3r_dir),
        scene_dir=str(ctx.sparse2dgs_scene_dir or ""),
        sparse2dgs_dir=str(ctx.sparse2dgs_dir),
        output_dir=str(ctx.sparse2dgs_dir),
        repo_dir=str(repo_dir),
    )
    return shlex.split(rendered)


def _resolve_sparse2dgs_default_asset(output_dir: Path) -> Path:
    point_cloud_root = output_dir / "point_cloud"
    if not point_cloud_root.exists():
        raise RuntimeError(f"sparse2dgs_point_cloud_missing:{point_cloud_root}")

    candidates: list[tuple[int, Path]] = []
    for child in point_cloud_root.iterdir():
        if not child.is_dir() or not child.name.startswith("iteration_"):
            continue
        try:
            iteration = int(child.name.split("iteration_", 1)[1])
        except ValueError:
            continue
        point_cloud = child / "point_cloud.ply"
        if point_cloud.is_file():
            candidates.append((iteration, point_cloud))

    if not candidates:
        raise RuntimeError(f"sparse2dgs_default_asset_missing:{point_cloud_root}")

    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _load_sparse2dgs_scene_contract(ctx: JobContext) -> dict[str, object]:
    assert ctx.slam3r_dir is not None
    contract_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    if not contract_path.exists():
        raise RuntimeError(f"sparse2dgs_scene_contract_missing:{contract_path}")
    try:
        return json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"sparse2dgs_scene_contract_invalid:{contract_path}") from exc


def _build_sparse2dgs_attempt_plans(contract: dict[str, object]) -> list[Sparse2DGSAttemptPlan]:
    selected_views = max(
        int(contract.get("selected_frame_count") or 0),
        min(
            int(contract.get("selected_frame_target") or 0),
            max(int(contract.get("frame_count") or 0), 0),
        ),
    )
    exported_size = contract.get("exported_image_size") or []
    if not isinstance(exported_size, list) or len(exported_size) != 2:
        raise RuntimeError("sparse2dgs_scene_contract_missing_export_size")
    current_longest_side = max(int(exported_size[0]), int(exported_size[1]))
    bridge_budget = contract.get("bridge_budget") or {}
    if not isinstance(bridge_budget, dict):
        bridge_budget = {}
    current_pixel_budget = max(
        1_000_000,
        int(bridge_budget.get("total_pixel_budget") or config.sparse2dgs_contract_total_pixel_budget),
    )
    min_image_size = max(256, int(config.sparse2dgs_contract_min_image_size))
    min_views = max(8, int(config.sparse2dgs_min_views))
    scale = min(max(float(config.sparse2dgs_oom_resolution_scale), 0.50), 0.95)
    view_drop_step = max(1, int(config.sparse2dgs_oom_view_drop_step))
    max_attempts = max(1, 1 + int(config.sparse2dgs_max_oom_retries))

    plans: list[Sparse2DGSAttemptPlan] = [
        Sparse2DGSAttemptPlan(
            attempt_index=0,
            target_views=max(selected_views, min_views),
            max_image_size=current_longest_side,
            total_pixel_budget=current_pixel_budget,
            reason="initial_budget",
        )
    ]

    current_views = plans[0].target_views
    current_max_image_size = plans[0].max_image_size
    current_budget = plans[0].total_pixel_budget
    while len(plans) < max_attempts:
        next_views = current_views
        next_max_image_size = current_max_image_size
        next_budget = current_budget
        reason = ""

        if current_max_image_size > min_image_size:
            scaled_size = _round_down_to_multiple(int(math.floor(current_max_image_size * scale)), 64)
            next_max_image_size = max(min_image_size, scaled_size)
            next_budget = max(1_000_000, int(current_budget * scale * scale))
            reason = "oom_reduce_resolution"
        elif current_views > min_views:
            next_views = max(min_views, current_views - view_drop_step)
            reason = "oom_reduce_views"
        else:
            break

        if (
            next_views == current_views
            and next_max_image_size == current_max_image_size
            and next_budget == current_budget
        ):
            break

        current_views = next_views
        current_max_image_size = next_max_image_size
        current_budget = next_budget
        plans.append(
            Sparse2DGSAttemptPlan(
                attempt_index=len(plans),
                target_views=current_views,
                max_image_size=current_max_image_size,
                total_pixel_budget=current_budget,
                reason=reason,
            )
        )

    return plans


def _rebuild_sparse2dgs_scene_for_attempt(ctx: JobContext, attempt_plan: Sparse2DGSAttemptPlan) -> None:
    ctx.sparse2dgs_attempt_index = int(attempt_plan.attempt_index)
    ctx.sparse2dgs_target_views_override = int(attempt_plan.target_views)
    ctx.sparse2dgs_contract_max_image_size_override = int(attempt_plan.max_image_size)
    ctx.sparse2dgs_contract_total_pixel_budget_override = int(attempt_plan.total_pixel_budget)
    bridge_slam3r_to_sparse2dgs_scene(ctx)


def _clear_sparse2dgs_attempt_overrides(ctx: JobContext) -> None:
    ctx.sparse2dgs_attempt_index = 0
    ctx.sparse2dgs_target_views_override = None
    ctx.sparse2dgs_contract_max_image_size_override = None
    ctx.sparse2dgs_contract_total_pixel_budget_override = None


def _reset_sparse2dgs_output_dir(output_dir: Path, *, preserve: set[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for child in output_dir.iterdir():
        if child.name in preserve:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
            continue
        child.unlink(missing_ok=True)


def _run_sparse2dgs_command(
    command: list[str],
    *,
    cwd: str,
    timeout: int,
    log_path: Path,
    progress_callback: Callable[[int, int], None] | None = None,
) -> None:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log_handle:
        _write_sparse2dgs_log_header(log_handle, command=command, cwd=cwd, timeout=timeout)

        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,
            bufsize=0,
            env=env,
        )

        assert process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        start_time = time.monotonic()
        trailing = ""
        last_reported: list[tuple[int, int] | None] = [None]

        try:
            while True:
                if time.monotonic() - start_time > timeout:
                    process.kill()
                    _write_sparse2dgs_log_footer(
                        log_handle,
                        status="timeout",
                        elapsed_sec=time.monotonic() - start_time,
                    )
                    raise subprocess.TimeoutExpired(command, timeout)

                events = selector.select(timeout=1.0)
                if not events:
                    if process.poll() is not None:
                        break
                    continue

                for key, _ in events:
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue

                    decoded = chunk.decode("utf-8", errors="replace")
                    sys.stdout.write(decoded)
                    sys.stdout.flush()
                    log_handle.write(decoded)
                    log_handle.flush()
                    trailing = _consume_sparse2dgs_output(
                        trailing + decoded,
                        progress_callback=progress_callback,
                        last_reported=last_reported,
                    )

                if process.poll() is not None and not selector.get_map():
                    break
        finally:
            selector.close()

        if trailing:
            _emit_sparse2dgs_progress(
                trailing,
                progress_callback=progress_callback,
                last_reported=last_reported,
            )

        return_code = process.wait()
        elapsed_sec = time.monotonic() - start_time
        _write_sparse2dgs_log_footer(
            log_handle,
            status="ok" if return_code == 0 else "failed",
            elapsed_sec=elapsed_sec,
            return_code=return_code,
        )
        if return_code != 0:
            raise subprocess.CalledProcessError(return_code, command)


def _parse_sparse2dgs_oom_reason(log_path: Path, ctx: JobContext) -> str | None:
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    if "OutOfMemoryError" not in content and "CUDA out of memory" not in content:
        return None

    detail = ""
    contract_path = ctx.slam3r_dir / "sparse2dgs_scene_contract.json"
    if contract_path.exists():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            selected_views = contract.get("selected_frame_count")
            exported_size = contract.get("exported_image_size")
            detail = f":selected_views={selected_views}:exported_image_size={exported_size}"
        except (OSError, json.JSONDecodeError):
            detail = ""
    return f"sparse2dgs_cuda_oom{detail}"


def _write_sparse2dgs_log_header(
    log_handle,
    *,
    command: list[str],
    cwd: str,
    timeout: int,
) -> None:
    log_handle.write(f"[sparse2dgs] cwd={cwd}\n")
    log_handle.write(f"[sparse2dgs] timeout_sec={timeout}\n")
    log_handle.write(f"[sparse2dgs] command={shlex.join(command)}\n")
    log_handle.write("[sparse2dgs] --- begin combined stdout/stderr ---\n")
    log_handle.flush()


def _round_down_to_multiple(value: int, multiple: int) -> int:
    if multiple <= 0:
        return value
    return max(multiple, (value // multiple) * multiple)


def _write_sparse2dgs_log_footer(
    log_handle,
    *,
    status: str,
    elapsed_sec: float,
    return_code: int | None = None,
) -> None:
    log_handle.write("\n[sparse2dgs] --- end combined stdout/stderr ---\n")
    log_handle.write(f"[sparse2dgs] status={status}\n")
    if return_code is not None:
        log_handle.write(f"[sparse2dgs] return_code={return_code}\n")
    log_handle.write(f"[sparse2dgs] elapsed_sec={elapsed_sec:.2f}\n")
    log_handle.flush()


def _consume_sparse2dgs_output(
    buffered: str,
    *,
    progress_callback: Callable[[int, int], None] | None,
    last_reported: list[tuple[int, int] | None],
) -> str:
    lines = re.split(r"[\r\n]+", buffered)
    if buffered and buffered[-1] not in "\r\n":
        trailing = lines.pop()
    else:
        trailing = ""

    for line in lines:
        _emit_sparse2dgs_progress(
            line,
            progress_callback=progress_callback,
            last_reported=last_reported,
        )
    return trailing


def _emit_sparse2dgs_progress(
    line: str,
    *,
    progress_callback: Callable[[int, int], None] | None,
    last_reported: list[tuple[int, int] | None],
) -> None:
    if progress_callback is None:
        return

    match = SPARSE2DGS_TRAINING_STEP_PATTERN.search(line)
    if match is None:
        return

    step = int(match.group(1))
    total = max(int(match.group(2)), 1)
    signature = (step, total)
    if last_reported[0] == signature:
        return
    last_reported[0] = signature
    progress_callback(step, total)
