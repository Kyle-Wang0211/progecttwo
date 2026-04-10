from __future__ import annotations

import json

from ..context import JobContext


def run_graphdeco_hq_refine(ctx: JobContext) -> None:
    assert ctx.hq_dir is not None
    (ctx.hq_dir / "graphdeco_3dgs.todo.json").write_text(
        json.dumps(
            {
                "backend": "graphdeco_3dgs",
                "note": "TODO: run official graphdeco-inria gaussian-splatting refine step",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
