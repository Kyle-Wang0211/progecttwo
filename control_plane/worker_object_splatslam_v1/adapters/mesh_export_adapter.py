from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ..context import JobContext


def export_optional_mesh(ctx: JobContext) -> dict[str, Any]:
    assert ctx.mesh_dir is not None
    assert ctx.splatslam_dir is not None

    summary = _read_json(ctx.splatslam_dir / "splatslam.json")
    mesh_source = _resolve_mesh_asset(summary)
    output_path = ctx.mesh_dir / "mesh_export.json"
    if mesh_source is None:
        payload = {
            "backend": "mesh_export_adapter",
            "ready": False,
            "note": "No proxy mesh exported by Splat-SLAM yet.",
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    destination = ctx.mesh_dir / f"default_object{mesh_source.suffix.lower()}"
    shutil.copy2(mesh_source, destination)
    payload = {
        "backend": "mesh_export_adapter",
        "ready": True,
        "mesh_path": str(destination),
        "mesh_kind": destination.suffix.lower().lstrip("."),
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _resolve_mesh_asset(summary: dict[str, Any]) -> Path | None:
    for key in ("proxy_mesh_glb", "proxy_mesh_obj", "mesh_glb", "mesh_obj"):
        candidate = summary.get(key)
        if isinstance(candidate, str) and candidate.strip():
            path = Path(candidate).expanduser()
            if path.exists():
                return path
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
