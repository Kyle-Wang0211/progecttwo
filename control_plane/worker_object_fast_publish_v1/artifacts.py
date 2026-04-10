from __future__ import annotations

import base64
import json
import shutil
import struct
from pathlib import Path
from typing import Any

from .context import JobContext


_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y7XcAAAAASUVORK5CYII="
)


def _minimal_glb_bytes() -> bytes:
    positions = struct.pack(
        "<9f",
        -0.5,
        0.0,
        0.0,
        0.5,
        0.0,
        0.0,
        0.0,
        0.85,
        0.0,
    )
    indices = struct.pack("<3H", 0, 1, 2)
    index_padding = (4 - (len(indices) % 4)) % 4
    binary_payload = positions + indices + (b"\x00" * index_padding)
    json_payload = json.dumps(
        {
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": len(binary_payload)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
                {
                    "buffer": 0,
                    "byteOffset": len(positions),
                    "byteLength": len(indices),
                    "target": 34963,
                },
            ],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": 3,
                    "type": "VEC3",
                    "min": [-0.5, 0.0, 0.0],
                    "max": [0.5, 0.85, 0.0],
                },
                {
                    "bufferView": 1,
                    "componentType": 5123,
                    "count": 3,
                    "type": "SCALAR",
                    "min": [0],
                    "max": [2],
                },
            ],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0},
                            "indices": 1,
                        }
                    ]
                }
            ],
            "nodes": [{"mesh": 0}],
            "scene": 0,
            "scenes": [{"nodes": [0]}],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    json_padding = (4 - (len(json_payload) % 4)) % 4
    json_chunk = json_payload + (b" " * json_padding)
    binary_padding = (4 - (len(binary_payload) % 4)) % 4
    bin_chunk = binary_payload + (b"\x00" * binary_padding)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return (
        b"glTF"
        + struct.pack("<I", 2)
        + struct.pack("<I", total_length)
        + struct.pack("<I", len(json_chunk))
        + b"JSON"
        + json_chunk
        + struct.pack("<I", len(bin_chunk))
        + b"BIN\x00"
        + bin_chunk
    )


def _load_openmvs_summary(ctx: JobContext) -> dict[str, Any] | None:
    assert ctx.surface_dir is not None
    summary_path = ctx.surface_dir / "openmvs.json"
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_cleanup_summary(ctx: JobContext) -> dict[str, Any] | None:
    assert ctx.surface_dir is not None
    summary_path = ctx.surface_dir / "cleanup.json"
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _first_curated_frame(ctx: JobContext) -> Path | None:
    if ctx.curated_dir is None or not ctx.curated_dir.exists():
        return None
    for candidate in sorted(ctx.curated_dir.iterdir()):
        if candidate.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            return candidate
    for candidate in sorted(ctx.curated_dir.glob("*.png")):
        return candidate
    return None


def _write_real_glb_from_surface(ctx: JobContext, destination: Path) -> bool:
    assert ctx.surface_dir is not None
    cleaned_glb = ctx.surface_dir / "scene_cleaned.glb"
    if cleaned_glb.exists():
        shutil.copy2(cleaned_glb, destination)
        return True

    canonical_glb = ctx.surface_dir / "scene_canonical.glb"
    if canonical_glb.exists():
        shutil.copy2(canonical_glb, destination)
        return True

    summary = _load_openmvs_summary(ctx)
    if not summary:
        return False

    source_candidates = []
    textured_obj = summary.get("textured_obj")
    mesh_ply = summary.get("mesh_ply")
    if textured_obj:
        source_candidates.append(Path(str(textured_obj)))
    if mesh_ply:
        source_candidates.append(Path(str(mesh_ply)))

    mesh_source = next((candidate for candidate in source_candidates if candidate.exists()), None)
    if mesh_source is None:
        return False

    try:
        import trimesh
    except ImportError:
        return False

    try:
        scene_or_mesh = trimesh.load(mesh_source, force="scene")
        glb_bytes = scene_or_mesh.export(file_type="glb")
        if isinstance(glb_bytes, str):
            glb_bytes = glb_bytes.encode("utf-8")
        destination.write_bytes(glb_bytes)
        return True
    except Exception:
        return False


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
        return
    except Exception:
        destination.write_bytes(base64.b64decode(_TINY_PNG_BASE64))


def ensure_default_publish_files(ctx: JobContext) -> dict[str, Path]:
    assert ctx.default_publish_dir is not None
    default_glb = ctx.default_publish_dir / "default_object.glb"
    poster = ctx.default_publish_dir / "poster.png"
    viewer_manifest = ctx.default_publish_dir / "viewer_manifest.json"

    if not _write_real_glb_from_surface(ctx, default_glb):
        default_glb.write_bytes(_minimal_glb_bytes())
    _write_poster(ctx, poster)
    return {
        "default_glb": default_glb,
        "poster": poster,
        "viewer_manifest": viewer_manifest,
    }


def ensure_hq_publish_files(ctx: JobContext) -> dict[str, Path]:
    assert ctx.hq_dir is not None
    hq_splat = ctx.hq_dir / "hq.splat"
    hq_splat.write_text("# placeholder hq splat\n", encoding="utf-8")
    return {"hq_splat": hq_splat}


def write_viewer_manifest(ctx: JobContext, *, hq_ready: bool) -> Path:
    assert ctx.default_publish_dir is not None
    cleanup_summary = _load_cleanup_summary(ctx) or {}
    payload = {
        "version": "object_publish_v1",
        "default_asset": {
            "kind": "glb",
            "path": "default/default_object.glb",
            "ready": True,
        },
        "poster": {
            "kind": "png",
            "path": "default/poster.png",
        },
        "hq_asset": {
            "kind": "splat",
            "path": "hq/hq.splat",
            "ready": hq_ready,
        },
    }
    if cleanup_summary.get("camera_preset"):
        payload["camera_preset"] = cleanup_summary["camera_preset"]
    if cleanup_summary.get("support_patch_bounds"):
        payload["support_patch_bounds"] = cleanup_summary["support_patch_bounds"]
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
    default_asset: dict[str, Any],
    preview_asset: dict[str, Any],
    viewer_manifest_asset: dict[str, Any],
    hq_asset: dict[str, Any],
) -> dict[str, Any]:
    return {
        "primary_artifact": default_asset,
        "preview": preview_asset,
        "viewer_manifest": viewer_manifest_asset,
        "metrics": hq_asset,
    }
