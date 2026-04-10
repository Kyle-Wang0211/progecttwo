from __future__ import annotations

from ..adapters.mesh_export_adapter import export_optional_mesh
from ..context import JobContext


def run_optional_mesh_export(ctx: JobContext) -> None:
    ctx.current_stage = "optional_mesh_export"
    export_optional_mesh(ctx)
