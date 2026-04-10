from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any

from .context import JobContext


_TINY_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+y7XcAAAAASUVORK5CYII="
)


def _minimal_point_ply_bytes() -> bytes:
    payload = "\n".join(
        [
            "ply",
            "format ascii 1.0",
            "element vertex 4",
            "property float x",
            "property float y",
            "property float z",
            "property uchar red",
            "property uchar green",
            "property uchar blue",
            "end_header",
            "-0.05 0.00 0.00 230 230 230",
            "0.05 0.00 0.00 230 230 230",
            "0.00 0.08 0.00 255 255 255",
            "0.00 0.02 0.05 210 210 210",
            "",
        ]
    )
    return payload.encode("utf-8")


def _load_cleanup_summary(ctx: JobContext) -> dict[str, Any] | None:
    assert ctx.splat_dir is not None
    summary_path = ctx.splat_dir / "cleanup.json"
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_support_summary(ctx: JobContext) -> dict[str, Any] | None:
    assert ctx.support_dir is not None
    summary_path = ctx.support_dir / "support_plane.json"
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
        return
    except Exception:
        destination.write_bytes(base64.b64decode(_TINY_PNG_BASE64))


def ensure_default_publish_files(ctx: JobContext) -> dict[str, Path]:
    assert ctx.default_publish_dir is not None
    poster = ctx.default_publish_dir / "poster.png"
    viewer_manifest = ctx.default_publish_dir / "viewer_manifest.json"
    copied_assets = _copy_default_assets(ctx)
    _write_poster(ctx, poster)
    payload: dict[str, Path] = {
        "default_asset": copied_assets["default_asset"],
        "poster": poster,
        "viewer_manifest": viewer_manifest,
    }
    if copied_assets.get("cleaned_asset") is not None:
        payload["cleaned_asset"] = copied_assets["cleaned_asset"]
    if copied_assets.get("cleanup_compare") is not None:
        payload["cleanup_compare"] = copied_assets["cleanup_compare"]
    if copied_assets.get("default_mesh") is not None:
        payload["default_mesh"] = copied_assets["default_mesh"]
    return payload


def ensure_hq_publish_files(ctx: JobContext) -> dict[str, Path]:
    assert ctx.hq_dir is not None
    hq_splat = ctx.hq_dir / "hq.splat"
    if not hq_splat.exists():
        hq_splat.write_text("# placeholder hq splat\n", encoding="utf-8")
    return {"hq_splat": hq_splat}


def write_viewer_manifest(ctx: JobContext, *, hq_ready: bool) -> Path:
    assert ctx.default_publish_dir is not None
    cleanup_summary = _load_cleanup_summary(ctx) or {}
    support_summary = _load_support_summary(ctx) or {}
    default_asset_path = _resolve_default_publish_asset(ctx.default_publish_dir)
    default_asset_kind = cleanup_summary.get("raw_asset_kind")
    if not isinstance(default_asset_kind, str) or not default_asset_kind:
        default_asset_kind = _asset_kind_for_path(default_asset_path)

    payload: dict[str, Any] = {
        "version": "object_publish_v1",
        "default_asset": {
            "kind": default_asset_kind,
            "path": f"default/{default_asset_path.name}",
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
    cleaned_path = _resolve_cleaned_publish_asset(ctx.default_publish_dir)
    if cleaned_path is not None:
        payload["cleaned_asset"] = {
            "kind": _asset_kind_for_path(cleaned_path),
            "path": f"default/{cleaned_path.name}",
            "ready": True,
        }
    cleanup_compare_path = ctx.default_publish_dir / "cleanup_compare.json"
    if cleanup_compare_path.exists():
        payload["cleanup_compare"] = {
            "kind": "json",
            "path": f"default/{cleanup_compare_path.name}",
            "ready": True,
        }
    mesh_path = _resolve_optional_mesh_asset(ctx.default_publish_dir)
    if mesh_path is not None:
        payload["mesh_asset"] = {
            "kind": "glb" if mesh_path.suffix.lower() == ".glb" else "mesh",
            "path": f"default/{mesh_path.name}",
            "ready": True,
        }
    camera_preset = cleanup_summary.get("camera_preset") or support_summary.get("camera_preset")
    if camera_preset:
        payload["camera_preset"] = camera_preset
    support_patch_bounds = cleanup_summary.get("support_patch_bounds") or support_summary.get("support_patch_bounds")
    if support_patch_bounds:
        payload["support_patch_bounds"] = support_patch_bounds

    manifest_path = ctx.default_publish_dir / "viewer_manifest.json"
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def build_default_artifact_manifest(
    *,
    default_asset: dict[str, Any],
    preview_asset: dict[str, Any],
    viewer_manifest_asset: dict[str, Any],
    cleaned_asset: dict[str, Any] | None = None,
    cleanup_compare_asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "primary_artifact": default_asset,
        "preview": preview_asset,
        "viewer_manifest": viewer_manifest_asset,
    }
    if cleaned_asset is not None:
        payload["comparison_asset"] = cleaned_asset
    if cleanup_compare_asset is not None:
        payload["comparison_metrics"] = cleanup_compare_asset
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


def _copy_default_assets(ctx: JobContext) -> dict[str, Path]:
    assert ctx.default_publish_dir is not None
    assert ctx.splat_dir is not None

    default_asset_source = _resolve_raw_splat_asset(ctx)
    default_asset: Path
    if default_asset_source is None:
        default_asset = ctx.default_publish_dir / "default_object.ply"
        default_asset.write_bytes(_minimal_point_ply_bytes())
    else:
        default_asset = ctx.default_publish_dir / f"default_object{default_asset_source.suffix.lower()}"
        shutil.copy2(default_asset_source, default_asset)

    cleanup_summary = _load_cleanup_summary(ctx) or {}
    cleaned_asset: Path | None = None
    cleaned_asset_source = _resolve_cleaned_splat_asset(ctx, cleanup_summary)
    if cleaned_asset_source is not None:
        cleaned_asset = ctx.default_publish_dir / f"default_object_cleaned{cleaned_asset_source.suffix.lower()}"
        shutil.copy2(cleaned_asset_source, cleaned_asset)

    cleanup_compare: Path | None = None
    if cleanup_summary:
        cleanup_compare = ctx.default_publish_dir / "cleanup_compare.json"
        compare_payload = {
            "raw_asset_filename": default_asset.name,
            "cleaned_asset_filename": cleaned_asset.name if cleaned_asset is not None else None,
            **cleanup_summary,
        }
        cleanup_compare.write_text(json.dumps(compare_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    mesh_asset: Path | None = None
    if ctx.mesh_dir is not None:
        for candidate in sorted(ctx.mesh_dir.glob("default_object.*")):
            if candidate.is_file():
                mesh_asset = ctx.default_publish_dir / candidate.name
                shutil.copy2(candidate, mesh_asset)
                break
    payload: dict[str, Path] = {"default_asset": default_asset}
    if cleaned_asset is not None:
        payload["cleaned_asset"] = cleaned_asset
    if cleanup_compare is not None:
        payload["cleanup_compare"] = cleanup_compare
    if mesh_asset is not None:
        payload["default_mesh"] = mesh_asset
    return payload


def _resolve_raw_splat_asset(ctx: JobContext) -> Path | None:
    for candidate in (
        ctx.splatslam_dir / "default_object.ply" if ctx.splatslam_dir is not None else None,
        ctx.splatslam_dir / "default_object.splat" if ctx.splatslam_dir is not None else None,
        ctx.splatslam_dir / "default_object.spz" if ctx.splatslam_dir is not None else None,
    ):
        if candidate is not None and candidate.exists():
            return candidate
    if ctx.splatslam_dir is not None:
        summary_path = ctx.splatslam_dir / "splatslam.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                summary = {}
            for key in ("default_asset", "gaussian_ply", "gaussian_splat", "exported_ply", "exported_splat"):
                candidate = summary.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    path = Path(candidate).expanduser()
                    if path.exists():
                        return path
    return None


def _resolve_cleaned_splat_asset(ctx: JobContext, cleanup_summary: dict[str, Any]) -> Path | None:
    assert ctx.splat_dir is not None
    for key in ("cleaned_asset_filename", "default_asset_filename"):
        filename = cleanup_summary.get(key)
        if isinstance(filename, str) and filename.strip():
            candidate = ctx.splat_dir / filename
            if candidate.exists():
                return candidate
    for candidate in (
        ctx.splat_dir / "default_object_cleaned.ply",
        ctx.splat_dir / "default_object_cleaned.splat",
        ctx.splat_dir / "default_object_cleaned.spz",
    ):
        if candidate.exists():
            return candidate
    return None


def _resolve_default_publish_asset(default_dir: Path) -> Path:
    for name in ("default_object.ply", "default_object.splat", "default_object.spz", "default_object.glb"):
        candidate = default_dir / name
        if candidate.exists():
            return candidate
    raise RuntimeError("default_publish_asset_missing")


def _resolve_cleaned_publish_asset(default_dir: Path) -> Path | None:
    for name in ("default_object_cleaned.ply", "default_object_cleaned.splat", "default_object_cleaned.spz"):
        candidate = default_dir / name
        if candidate.exists():
            return candidate
    return None


def _resolve_optional_mesh_asset(default_dir: Path) -> Path | None:
    for candidate in sorted(default_dir.glob("default_object.*")):
        if candidate.suffix.lower() == ".glb":
            return candidate
    return None


def _asset_kind_for_path(path: Path) -> str:
    if path.suffix.lower() == ".glb":
        return "glb"
    return "splat"
