from __future__ import annotations

import json
from pathlib import Path

from ..config import config
from ..context import JobContext


def maybe_run_hloc(ctx: JobContext) -> None:
    assert ctx.sfm_dir is not None
    repo_path = Path(config.hloc_repo).expanduser() if config.hloc_repo else None
    enabled = ctx.stack.get("matching_frontend", "hloc_optional") == "hloc_optional" and bool(repo_path)
    (ctx.sfm_dir / "hloc.json").write_text(
        json.dumps(
            {
                "backend": "hloc_optional",
                "enabled": enabled,
                "repo_path": str(repo_path) if repo_path else None,
                "note": "v1 uses official COLMAP CLI as the real SFM path; hloc stays optional and only activates when a repo path is configured.",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
