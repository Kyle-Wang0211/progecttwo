from __future__ import annotations

from ..adapters.sam2_adapter import run_sam2_batch
from ..context import JobContext


def run_masks_lite(ctx: JobContext) -> None:
    ctx.current_stage = "object_mask"
    run_sam2_batch(ctx)
