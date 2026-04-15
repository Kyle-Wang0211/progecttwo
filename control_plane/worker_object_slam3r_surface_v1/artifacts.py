from __future__ import annotations

import base64
import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from .config import config
from .context import JobContext
from .quality_gate import quality_report_path


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
        import cv2

        image = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if image is not None and cv2.imwrite(str(destination), image):
            return
    except Exception:
        pass

    if source.suffix.lower() == ".png":
        try:
            shutil.copy2(source, destination)
            return
        except Exception:
            pass

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


def write_viewer_manifest(
    ctx: JobContext,
    *,
    hq_ready: bool,
    publish_allowed: bool,
    failed_cards: list[str] | None = None,
) -> Path:
    assert ctx.default_publish_dir is not None
    default_asset = _resolve_default_publish_asset(ctx.default_publish_dir)
    default_kind = _artifact_kind_for_path(default_asset)
    failed_cards = [str(card) for card in (failed_cards or [])]
    payload: dict[str, Any] = {
        "version": "object_surface_v1",
        "product_mode": "hq_only",
        "primary_product": "hq_mesh_glb",
        "inspection_only": not publish_allowed,
        "hq_passed": publish_allowed,
        "failed_cards": failed_cards,
        "default_asset": {
            "kind": default_kind,
            "path": f"default/{default_asset.name}",
            "ready": True,
            "representation": "hq_textured_mesh" if default_kind in {"glb", "mesh"} else "hq_surface_mesh",
        },
        "poster": {
            "kind": "png",
            "path": "default/poster.png",
        },
        "pipeline_stack": {
            "reconstruction": "slam3r",
            "surface": "sparse2dgs",
            "mesh_extraction": "matcha",
            "delivery_mesh": "hq_optimized_open_surface",
            "texture_bake": "hq_visible_photo_projection_glb" if default_kind == "glb" else "disabled",
            "rendering": "hq_mesh_glb" if default_kind == "glb" else "disabled",
            "hq": "disabled",
        },
        "camera_preset": _default_camera_preset(ctx),
    }
    report_path = quality_report_path(ctx)
    if report_path.exists():
        payload["quality_report"] = {
            "kind": "json",
            "path": f"default/{report_path.name}",
            "ready": True,
        }

    hq_asset = _resolve_optional_hq_publish_asset(ctx.hq_dir) if ctx.hq_dir is not None else None
    if hq_asset is not None:
        payload["hq_asset"] = {
            "kind": "gaussian",
            "path": f"hq/{hq_asset.name}",
            "ready": hq_ready,
        }

    manifest_path = ctx.default_publish_dir / "viewer_manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def build_default_artifact_manifest(
    *,
    default_asset: dict[str, Any],
    preview_asset: dict[str, Any],
    viewer_manifest_asset: dict[str, Any],
    quality_report_asset: dict[str, Any] | None = None,
    publish_allowed: bool,
    failed_cards: list[str] | None = None,
) -> dict[str, Any]:
    failed_cards = [str(card) for card in (failed_cards or [])]
    payload = {
        "primary_artifact": default_asset,
        "preview": preview_asset,
        "viewer_manifest": viewer_manifest_asset,
        "product_mode": "hq_only",
        "inspection_only": not publish_allowed,
        "hq_passed": publish_allowed,
        "failed_cards": failed_cards,
    }
    if quality_report_asset is not None:
        payload["quality_report"] = quality_report_asset
    return payload


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


def _default_camera_preset(ctx: JobContext) -> dict[str, Any]:
    payload = {
        "pitch_degrees": config.default_camera_pitch_deg,
        "yaw_degrees": config.default_camera_yaw_deg,
        "distance_scale": config.default_camera_distance_scale,
        "up": _resolve_capture_up_vector(ctx),
    }
    source = ctx.pipeline_string("capture_gravity_source", "").strip()
    if source:
        payload["up_source"] = source
    confidence = ctx.pipeline_float("capture_gravity_confidence", -1.0)
    if confidence >= 0.0:
        payload["up_confidence"] = max(0.0, min(1.0, float(confidence)))
    return payload


def _resolve_capture_up_vector(ctx: JobContext) -> list[float]:
    vector = np.asarray(
        [
            ctx.pipeline_float("capture_gravity_up_x", 0.0),
            ctx.pipeline_float("capture_gravity_up_y", 1.0),
            ctx.pipeline_float("capture_gravity_up_z", 0.0),
        ],
        dtype=np.float64,
    )
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-6:
        return [0.0, 1.0, 0.0]
    normalized = vector / norm
    return [float(normalized[0]), float(normalized[1]), float(normalized[2])]
