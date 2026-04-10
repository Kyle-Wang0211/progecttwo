from __future__ import annotations

from ..artifacts import (
    build_default_artifact_manifest,
    ensure_default_publish_files,
    write_viewer_manifest,
)
from ..context import JobContext
from ..runtime import ControlPlaneClient
from ..storage_client import ObjectStorageClient


def publish_default_mesh(
    ctx: JobContext,
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
) -> dict[str, dict[str, object]]:
    ctx.current_stage = "publish_default_mesh"
    files = ensure_default_publish_files(ctx)
    write_viewer_manifest(ctx, hq_ready=False)

    default_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['default_asset'].name}",
        local_path=files["default_asset"],
        artifact_type=f"default_mesh_{files['default_asset'].suffix.lower().lstrip('.')}",
    )
    preview_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['poster'].name}",
        local_path=files["poster"],
        artifact_type="poster_png",
    )
    viewer_manifest_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['viewer_manifest'].name}",
        local_path=files["viewer_manifest"],
        artifact_type="viewer_manifest_json",
    )
    return build_default_artifact_manifest(
        default_asset=default_asset,
        preview_asset=preview_asset,
        viewer_manifest_asset=viewer_manifest_asset,
    )
