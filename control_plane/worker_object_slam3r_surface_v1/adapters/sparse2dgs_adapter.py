from __future__ import annotations

import json
import os
import re
import selectors
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from ..config import config
from ..context import JobContext

SPARSE2DGS_PAPER_URL = (
    "https://openaccess.thecvf.com/content/CVPR2025/html/"
    "Wu_Sparse2DGS_Geometry-Prioritized_Gaussian_Splatting_for_Surface_Reconstruction_from_Sparse_Views_CVPR_2025_paper.html"
)
SPARSE2DGS_TRAINING_STEP_PATTERN = re.compile(r"Training progress:.*?\|\s*(\d+)/(\d+)")


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

    log_path = ctx.sparse2dgs_dir / "sparse2dgs_train.log"
    _run_sparse2dgs_command(
        command,
        cwd=str(Path(config.sparse2dgs_repo)),
        timeout=config.sparse2dgs_stage_timeout_sec,
        log_path=log_path,
        progress_callback=progress_callback,
    )

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
        "train_log": str(log_path),
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
