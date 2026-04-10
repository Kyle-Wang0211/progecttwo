from __future__ import annotations

from ..adapters.matcha_adapter import run_matcha_mesh_extraction
from ..context import JobContext


def run_matcha_mesh(ctx: JobContext) -> None:
    ctx.current_stage = "matcha_mesh_extract"
    run_matcha_mesh_extraction(ctx)
