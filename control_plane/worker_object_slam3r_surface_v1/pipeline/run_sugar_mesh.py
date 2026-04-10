from __future__ import annotations

from ..adapters.sugar_adapter import run_sugar_mesh_export
from ..context import JobContext


def run_sugar_mesh(ctx: JobContext) -> None:
    ctx.current_stage = "sugar_mesh"
    run_sugar_mesh_export(ctx)
