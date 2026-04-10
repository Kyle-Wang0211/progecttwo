from __future__ import annotations

import subprocess
from pathlib import Path

from ..config import config
from ..context import JobContext


def run_sam2_batch(ctx: JobContext) -> None:
    assert ctx.masks_dir is not None
    assert ctx.curated_dir is not None

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "run_sam2_auto_masks.py"
    summary_path = ctx.masks_dir / "sam2.json"
    repo_path = Path(config.sam2_repo).expanduser()
    checkpoint_path = Path(config.sam2_checkpoint).expanduser()
    if not repo_path.exists():
        raise RuntimeError(f"sam2_repo_missing:{repo_path}")
    if not checkpoint_path.exists():
        raise RuntimeError(f"sam2_checkpoint_missing:{checkpoint_path}")

    command = [
        config.sam2_python_bin,
        str(script_path),
        "--images-dir",
        str(ctx.curated_dir),
        "--output-dir",
        str(ctx.masks_dir),
        "--summary-json",
        str(summary_path),
        "--sam2-repo",
        str(repo_path),
        "--sam2-checkpoint",
        str(checkpoint_path),
        "--sam2-config",
        config.sam2_config,
        "--device",
        config.sam2_device,
        "--points-per-side",
        str(config.sam2_points_per_side),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"sam2_batch_failed:{result.stderr.strip() or result.stdout.strip()}")
