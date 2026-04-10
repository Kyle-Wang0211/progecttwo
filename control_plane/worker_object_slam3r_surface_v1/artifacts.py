from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any

from .config import config
from .context import JobContext


_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y7XcAAAAASUVORK5CYII="
)

def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _first_curated_frame(ctx: JobContext) -> Path | None:
    if ctx.curated_dir is None or not ctx.curated_dir.exists():
        return None
    for candidate in sorted(ctx.curated_dir.iterdir()):
        if candidate.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            return candidate
    return None


def _write_poster(ctx: JobContext, destination: Path) -> None:
    source = _first_curated_frame(ctx)
    if source is None:
        destination.write_bytes(base64.b64decode(_TINY_PNG_BASE64))
        return

    try:
        from PIL import Image

        with Image.open(source) as image:
            image.convert("RGB").save(destination, format="PNG")
        return
    except Exception:
        pass

    try:
        shutil.copy2(source, destination)
    except Exception:
        destination.write_bytes(base64.b64decode(_TINY_PNG_BASE64))


def ensure_default_publish_files(ctx: JobContext) -> dict[str, Path]:
    assert ctx.default_publish_dir is not None
    poster = ctx.default_publish_dir / "poster.png"
    viewer_manifest = ctx.default_publish_dir / "viewer_manifest.json"
    default_asset = _copy_default_delivery_asset(ctx)
    _write_poster(ctx, poster)
    return {
        "default_asset": default_asset,
        "poster": poster,
        "viewer_manifest": viewer_manifest,
    }


def ensure_hq_publish_files(ctx: JobContext) -> dict[str, Path]:
    assert ctx.hq_dir is not None
    hq_asset_source = _resolve_hq_asset(ctx)
    if hq_asset_source is None:
        raise RuntimeError("3dhgs_asset_missing")
    hq_asset = ctx.hq_dir / f"hq_asset{hq_asset_source.suffix.lower()}"
    if hq_asset_source.resolve() != hq_asset.resolve():
        shutil.copy2(hq_asset_source, hq_asset)
    return {"hq_asset": hq_asset}


def write_viewer_manifest(ctx: JobContext, *, hq_ready: bool) -> Path:
    assert ctx.default_publish_dir is not None
    default_asset = _resolve_default_publish_asset(ctx.default_publish_dir)
    default_kind = _artifact_kind_for_path(default_asset)
    payload: dict[str, Any] = {
        "version": "object_surface_v1",
        "default_asset": {
            "kind": default_kind,
            "path": f"default/{default_asset.name}",
            "ready": True,
            "representation": "textured_mesh" if default_kind in {"glb", "mesh"} else "surface_mesh",
        },
        "poster": {
            "kind": "png",
            "path": "default/poster.png",
        },
        "pipeline_stack": {
            "reconstruction": "slam3r",
            "surface": "sparse2dgs",
            "mesh_extraction": "matcha",
            "delivery_mesh": "optimized_mesh",
            "texture_bake": "projected_vertex_color_glb" if default_kind == "glb" else "disabled",
            "rendering": "default_mesh_glb" if default_kind == "glb" else "disabled",
            "hq": "disabled",
        },
        "camera_preset": _default_camera_preset(),
    }

    hq_asset = _resolve_optional_hq_publish_asset(ctx.hq_dir) if ctx.hq_dir is not None else None
    if hq_asset is not None:
        payload["hq_asset"] = {
            "kind": "gaussian",
            "path": f"hq/{hq_asset.name}",
            "ready": hq_ready,
        }
    else:
        payload["hq_asset"] = {
            "kind": "gaussian",
            "path": "hq/hq_asset.ply",
            "ready": False,
        }

    manifest_path = ctx.default_publish_dir / "viewer_manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def build_default_artifact_manifest(
    *,
    default_asset: dict[str, Any],
    preview_asset: dict[str, Any],
    viewer_manifest_asset: dict[str, Any],
) -> dict[str, Any]:
    return {
        "primary_artifact": default_asset,
        "preview": preview_asset,
        "viewer_manifest": viewer_manifest_asset,
    }


def build_hq_artifact_manifest(
    *,
    default_manifest: dict[str, Any],
    viewer_manifest_asset: dict[str, Any],
    hq_asset: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(default_manifest)
    payload["viewer_manifest"] = viewer_manifest_asset
    payload["hq_asset"] = hq_asset
    payload["metrics"] = hq_asset
    return payload


def _copy_default_delivery_asset(ctx: JobContext) -> Path:
    assert ctx.default_publish_dir is not None
    source = _resolve_default_delivery_asset(ctx)
    if source is None:
        raise RuntimeError("default_delivery_asset_missing")

    destination_name = "default_mesh" if _artifact_kind_for_path(source) in {"glb", "mesh"} else "default_surface"
    destination = ctx.default_publish_dir / f"{destination_name}{source.suffix.lower()}"
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination


def _resolve_default_delivery_asset(ctx: JobContext) -> Path | None:
    if ctx.delivery_dir is None:
        return None
    summary = _read_json(ctx.delivery_dir / config.delivery_texture_summary_filename) or {}
    for key in ("default_asset", "glb_asset"):
        candidate = summary.get(key)
        if isinstance(candidate, str) and candidate.strip():
            path = Path(candidate).expanduser()
            if path.exists() and path.suffix.lower() == ".glb":
                return path
    for pattern in ("default_mesh.glb", "*.glb"):
        candidates = sorted(ctx.delivery_dir.glob(pattern))
        if candidates:
            return candidates[-1]
    return None


def _resolve_hq_asset(ctx: JobContext) -> Path | None:
    assert ctx.hq_dir is not None
    summary = _read_json(ctx.hq_dir / config.hgs_summary_filename) or {}
    for key in ("hq_asset", "gaussian_asset", "ply_asset", "splat_asset"):
        candidate = summary.get(key)
        if isinstance(candidate, str) and candidate.strip():
            path = Path(candidate).expanduser()
            if path.exists():
                return path
    for candidate in sorted(ctx.hq_dir.glob("hq_asset.*")):
        if candidate.is_file():
            return candidate
    return None


def _resolve_default_publish_asset(default_dir: Path) -> Path:
    for pattern in ("default_mesh.glb", "default_mesh.*"):
        for candidate in sorted(default_dir.glob(pattern)):
            if candidate.is_file():
                return candidate
    raise RuntimeError("default_mesh_glb_missing")


def _resolve_optional_hq_publish_asset(hq_dir: Path) -> Path | None:
    for candidate in sorted(hq_dir.glob("hq_asset.*")):
        if candidate.is_file():
            return candidate
    return None


def _artifact_kind_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".glb":
        return "glb"
    if suffix in {".obj", ".ply", ".fbx"}:
        return "mesh"
    if suffix in {".splat", ".spz"}:
        return "gaussian"
    return "binary"


def _default_camera_preset() -> dict[str, Any]:
    return {
        "pitch_degrees": config.default_camera_pitch_deg,
        "yaw_degrees": config.default_camera_yaw_deg,
        "distance_scale": config.default_camera_distance_scale,
        "up": [0.0, 1.0, 0.0],
    }
