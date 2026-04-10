from __future__ import annotations

from ..adapters.default_delivery_adapter import bake_default_texture_delivery
from ..context import JobContext


def run_bake_default_texture(ctx: JobContext) -> None:
    ctx.current_stage = "bake_default_texture"
    bake_default_texture_delivery(ctx)
