from __future__ import annotations

import hashlib
import mimetypes
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from .config import config

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:  # pragma: no cover - depends on runtime deps
    boto3 = None
    BotoConfig = None


class ObjectStorageClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        if boto3 is None or BotoConfig is None:
            raise RuntimeError("boto3_not_installed")
        if not config.object_storage_bucket:
            raise RuntimeError("object_storage_bucket_required")

        self.bucket = config.object_storage_bucket
        self.public_base_url = config.object_storage_public_base_url.rstrip("/")
        self.client = boto3.client(
            "s3",
            region_name=config.object_storage_region or None,
            aws_access_key_id=config.object_storage_access_key_id or None,
            aws_secret_access_key=config.object_storage_secret_access_key or None,
            aws_session_token=config.object_storage_session_token or None,
            endpoint_url=config.object_storage_endpoint_url or None,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": config.object_storage_addressing_style or "auto"},
            ),
        )

    def download_to_path(
        self,
        download_url: str,
        target_path: Path,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session.get(download_url, stream=True, timeout=(15, 600)) as response:
            response.raise_for_status()
            total_bytes: Optional[int] = None
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    total_bytes = int(content_length)
                except ValueError:
                    total_bytes = None
            bytes_written = 0
            with target_path.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    bytes_written += len(chunk)
                    if progress_callback is not None:
                        progress_callback(bytes_written, total_bytes)

    def download_chunk_stream_to_path(
        self,
        manifest_url: str,
        target_path: Path,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        next_chunk_number = 1
        bytes_written = 0

        with target_path.open("wb") as handle:
            while True:
                manifest_response = self.session.get(manifest_url, timeout=(15, 60))
                manifest_response.raise_for_status()
                manifest = manifest_response.json()
                ready_chunks = {
                    int(item["chunk_number"]): item
                    for item in (manifest.get("chunks") or [])
                    if item.get("chunk_number") is not None
                }
                total_size_bytes = manifest.get("total_size_bytes")
                total_size_int = int(total_size_bytes) if total_size_bytes else None
                total_chunks = int(manifest.get("total_chunks") or 0)
                upload_completed = bool(manifest.get("upload_completed"))

                progressed = False
                while next_chunk_number in ready_chunks:
                    chunk = ready_chunks[next_chunk_number]
                    download_url = chunk.get("download_url")
                    if not download_url:
                        raise RuntimeError(f"chunk_download_url_missing:{next_chunk_number}")
                    with self.session.get(download_url, stream=True, timeout=(15, 600)) as response:
                        response.raise_for_status()
                        for data in response.iter_content(chunk_size=4 * 1024 * 1024):
                            if not data:
                                continue
                            handle.write(data)
                            bytes_written += len(data)
                            progressed = True
                            if progress_callback is not None:
                                progress_callback(bytes_written, total_size_int)
                    next_chunk_number += 1

                if upload_completed and total_chunks > 0 and next_chunk_number > total_chunks:
                    return
                if not progressed:
                    time.sleep(max(0.5, config.scheduler_tick_interval_sec))

    def upload_file(self, *, storage_key: str, local_path: Path, artifact_type: str) -> dict[str, Any]:
        content_type, _ = mimetypes.guess_type(str(local_path))
        extra_args: dict[str, Any] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.upload_file(str(local_path), self.bucket, storage_key, ExtraArgs=extra_args or None)
        return {
            "type": artifact_type,
            "storage_key": storage_key,
            "download_url": self.build_download_url(storage_key),
            "size_bytes": local_path.stat().st_size,
            "checksum_sha256": self._sha256(local_path),
        }

    def build_download_url(self, storage_key: str) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/{storage_key.lstrip('/')}"
        return self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=config.object_storage_presign_expiry_sec,
            HttpMethod="GET",
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
