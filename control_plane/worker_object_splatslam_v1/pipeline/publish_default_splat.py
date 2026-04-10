from __future__ import annotations

from ..artifacts import (
    build_default_artifact_manifest,
    ensure_default_publish_files,
    write_viewer_manifest,
)
from ..context import JobContext
from ..runtime import ControlPlaneClient
from ..storage_client import ObjectStorageClient


def publish_default_splat(
    ctx: JobContext,
    client: ControlPlaneClient,
    storage: ObjectStorageClient,
) -> dict[str, dict[str, object]]:
    ctx.current_stage = "publish_default_splat"
    files = ensure_default_publish_files(ctx)
    write_viewer_manifest(ctx, hq_ready=False)

    default_asset = storage.upload_file(
        storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{files['default_asset'].name}",
        local_path=files["default_asset"],
        artifact_type=f"default_object_{files['default_asset'].suffix.lower().lstrip('.')}",
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
    cleaned_asset: dict[str, object] | None = None
    cleaned_asset_path = files.get("cleaned_asset")
    if cleaned_asset_path is not None:
        cleaned_asset = storage.upload_file(
            storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{cleaned_asset_path.name}",
            local_path=cleaned_asset_path,
            artifact_type=f"cleaned_object_{cleaned_asset_path.suffix.lower().lstrip('.')}",
        )
    cleanup_compare_asset: dict[str, object] | None = None
    cleanup_compare_path = files.get("cleanup_compare")
    if cleanup_compare_path is not None:
        cleanup_compare_asset = storage.upload_file(
            storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{cleanup_compare_path.name}",
            local_path=cleanup_compare_path,
            artifact_type="cleanup_compare_json",
        )

    optional_mesh_path = files.get("default_mesh")
    if optional_mesh_path is not None:
        storage.upload_file(
            storage_key=f"{ctx.output_prefix.rstrip('/')}/default/{optional_mesh_path.name}",
            local_path=optional_mesh_path,
            artifact_type=f"optional_mesh_{optional_mesh_path.suffix.lower().lstrip('.')}",
        )

    return build_default_artifact_manifest(
        default_asset=default_asset,
        preview_asset=preview_asset,
        viewer_manifest_asset=viewer_manifest_asset,
        cleaned_asset=cleaned_asset,
        cleanup_compare_asset=cleanup_compare_asset,
    )
