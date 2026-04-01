from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

from .config import settings

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:  # pragma: no cover - optional until runtime deps are installed
    boto3 = None
    BotoConfig = None


@dataclass(frozen=True)
class UploadTarget:
    storage_key: str
    method: str
    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class MultipartUploadPartTarget:
    part_number: int
    method: str
    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class MultipartUploadTarget:
    storage_key: str
    upload_id: str
    part_size_bytes: int
    max_concurrency: int
    parts: list[MultipartUploadPartTarget]


@dataclass(frozen=True)
class ChunkedUploadTarget:
    storage_key: str
    upload_id: str
    part_size_bytes: int
    max_concurrency: int
    parts: list[MultipartUploadPartTarget]


def chunk_storage_key(base_storage_key: str, part_number: int) -> str:
    return f"{base_storage_key}.chunks/part-{part_number:05d}"


def _safe_file_name(file_name: str) -> str:
    candidate = os.path.basename(file_name or "").strip()
    return candidate or "upload.bin"


def _join_storage_key(*parts: str) -> str:
    return str(PurePosixPath(*[part.strip("/") for part in parts if part]))


class FakeObjectStorageSigner:
    """
    Dev fallback for local bring-up before a real bucket is provisioned.
    """

    provider_name = "fake"

    def create_upload_target(self, *, tenant_id: str, job_id: str, file_name: str, content_type: str) -> UploadTarget:
        safe_name = _safe_file_name(file_name)
        storage_key = _join_storage_key(settings.upload_bucket_prefix, tenant_id, job_id, safe_name)
        return UploadTarget(
            storage_key=storage_key,
            method="PUT",
            url=f"{settings.public_base_url.rstrip('/')}/fake-object-storage/{quote(storage_key)}",
            headers={"Content-Type": content_type},
        )

    def create_multipart_upload_target(
        self,
        *,
        tenant_id: str,
        job_id: str,
        file_name: str,
        content_type: str,
        file_size_bytes: int,
    ) -> MultipartUploadTarget:
        raise RuntimeError("multipart_not_supported")

    def complete_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> str | None:
        raise RuntimeError("multipart_not_supported")

    def create_chunked_upload_target(
        self,
        *,
        tenant_id: str,
        job_id: str,
        file_name: str,
        content_type: str,
        file_size_bytes: int,
    ) -> ChunkedUploadTarget:
        safe_name = _safe_file_name(file_name)
        storage_key = _join_storage_key(settings.upload_bucket_prefix, tenant_id, job_id, safe_name)
        part_size_bytes = max(5 * 1024 * 1024, settings.object_storage_chunked_ingest_chunk_size_bytes)
        max_concurrency = max(1, settings.object_storage_chunked_ingest_max_concurrency)
        part_count = max(1, (file_size_bytes + part_size_bytes - 1) // part_size_bytes)
        upload_id = f"chunked-{job_id}"
        parts = [
            MultipartUploadPartTarget(
                part_number=part_number,
                method="PUT",
                url=f"{settings.public_base_url.rstrip('/')}/fake-object-storage/{quote(chunk_storage_key(storage_key, part_number))}",
                headers={},
            )
            for part_number in range(1, part_count + 1)
        ]
        return ChunkedUploadTarget(
            storage_key=storage_key,
            upload_id=upload_id,
            part_size_bytes=part_size_bytes,
            max_concurrency=max_concurrency,
            parts=parts,
        )

    def abort_multipart_upload(self, *, storage_key: str, upload_id: str) -> None:
        raise RuntimeError("multipart_not_supported")

    def abort_chunked_upload(self, *, storage_key: str) -> None:
        self.delete_prefix(f"{storage_key}.chunks/")

    def delete_object(self, storage_key: str) -> None:
        target = Path("/tmp/aether-control-fake-object-storage") / storage_key
        try:
            target.unlink()
        except FileNotFoundError:
            return

    def delete_prefix(self, prefix: str) -> None:
        target = Path("/tmp/aether-control-fake-object-storage") / prefix
        shutil.rmtree(target, ignore_errors=True)

    def has_active_multipart_upload(self, storage_key: str) -> bool:
        return False

    def get_active_multipart_upload_id(self, storage_key: str) -> str | None:
        return None

    def list_uploaded_multipart_parts(self, *, storage_key: str, upload_id: str) -> list[dict[str, Any]]:
        return []

    def build_download_url(self, storage_key: str, *, expires_in: Optional[int] = None) -> str:
        return f"{settings.public_base_url.rstrip('/')}/fake-object-storage/{quote(storage_key)}"

    def probe_object(self, storage_key: str) -> tuple[bool, Optional[int]]:
        return False, None

    def upload_local_file(self, *, storage_key: str, local_path: Path, content_type: Optional[str] = None) -> None:
        destination = Path("/tmp/aether-control-fake-object-storage") / storage_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, destination)

    def open_download_stream(self, storage_key: str) -> tuple[Any, Optional[str], Optional[int]]:
        source = Path("/tmp/aether-control-fake-object-storage") / storage_key
        if not source.exists():
            raise FileNotFoundError(storage_key)
        return source.open("rb"), None, source.stat().st_size


class S3CompatibleObjectStorageSigner:
    provider_name = settings.object_storage_provider or "s3_compatible"

    def __init__(self) -> None:
        if boto3 is None or BotoConfig is None:  # pragma: no cover - depends on runtime deps
            raise RuntimeError("boto3_not_installed")
        if not settings.object_storage_bucket:
            raise RuntimeError("object_storage_bucket_required")

        client_kwargs = {
            "service_name": "s3",
            "region_name": settings.object_storage_region or None,
            "aws_access_key_id": settings.object_storage_access_key_id or None,
            "aws_secret_access_key": settings.object_storage_secret_access_key or None,
            "aws_session_token": settings.object_storage_session_token or None,
            "endpoint_url": settings.object_storage_endpoint_url or None,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": settings.object_storage_addressing_style or "auto"},
            ),
        }
        self.bucket = settings.object_storage_bucket
        self.public_base_url = settings.object_storage_public_base_url.rstrip("/")
        self.presign_expiry_sec = settings.object_storage_presign_expiry_sec
        self.client = boto3.client(**client_kwargs)

    def _rewrite_public_url(self, url: str) -> str:
        if not self.public_base_url:
            return url

        public_parts = urlsplit(self.public_base_url)
        signed_parts = urlsplit(url)
        if not public_parts.scheme or not public_parts.netloc:
            return url

        # Presigned S3-style URLs sign the Host header. If the configured
        # public base URL changes hosts, keep the original signed URL intact so
        # uploads/downloads do not fail with signature mismatches.
        signed_query = (signed_parts.query or "").lower()
        is_presigned = "x-amz-signature=" in signed_query or "awsaccesskeyid=" in signed_query
        if is_presigned and public_parts.netloc != signed_parts.netloc:
            return url

        path = signed_parts.path or "/"
        public_path = public_parts.path.rstrip("/")
        bucket_prefix = f"/{self.bucket}"
        bucket_host = public_parts.netloc.split(":", 1)[0].lower().startswith(f"{self.bucket.lower()}.")
        if path == bucket_prefix:
            suffix = "/"
        elif path.startswith(bucket_prefix + "/"):
            suffix = path[len(bucket_prefix) :]
        else:
            suffix = None

        if suffix is not None:
            if public_path:
                path = public_path if suffix == "/" else public_path + suffix
            elif bucket_host:
                path = suffix or "/"

        return urlunsplit(
            (
                public_parts.scheme,
                public_parts.netloc,
                path,
                signed_parts.query,
                signed_parts.fragment,
            )
        )

    def create_upload_target(self, *, tenant_id: str, job_id: str, file_name: str, content_type: str) -> UploadTarget:
        safe_name = _safe_file_name(file_name)
        storage_key = _join_storage_key(settings.upload_bucket_prefix, tenant_id, job_id, safe_name)
        url = self.client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self.bucket,
                "Key": storage_key,
                "ContentType": content_type,
            },
            ExpiresIn=self.presign_expiry_sec,
            HttpMethod="PUT",
        )
        return UploadTarget(
            storage_key=storage_key,
            method="PUT",
            url=self._rewrite_public_url(url),
            headers={"Content-Type": content_type},
        )

    def create_multipart_upload_target(
        self,
        *,
        tenant_id: str,
        job_id: str,
        file_name: str,
        content_type: str,
        file_size_bytes: int,
    ) -> MultipartUploadTarget:
        safe_name = _safe_file_name(file_name)
        storage_key = _join_storage_key(settings.upload_bucket_prefix, tenant_id, job_id, safe_name)
        part_size_bytes = max(5 * 1024 * 1024, settings.object_storage_multipart_part_size_bytes)
        max_concurrency = max(1, settings.object_storage_multipart_max_concurrency)

        response = self.client.create_multipart_upload(
            Bucket=self.bucket,
            Key=storage_key,
            ContentType=content_type,
        )
        upload_id = response["UploadId"]
        part_count = max(1, (file_size_bytes + part_size_bytes - 1) // part_size_bytes)
        parts: list[MultipartUploadPartTarget] = []
        for part_number in range(1, part_count + 1):
            url = self.client.generate_presigned_url(
                ClientMethod="upload_part",
                Params={
                    "Bucket": self.bucket,
                    "Key": storage_key,
                    "UploadId": upload_id,
                    "PartNumber": part_number,
                },
                ExpiresIn=self.presign_expiry_sec,
                HttpMethod="PUT",
            )
            parts.append(
                MultipartUploadPartTarget(
                    part_number=part_number,
                    method="PUT",
                    url=self._rewrite_public_url(url),
                    headers={},
                )
            )

        return MultipartUploadTarget(
            storage_key=storage_key,
            upload_id=upload_id,
            part_size_bytes=part_size_bytes,
            max_concurrency=max_concurrency,
            parts=parts,
        )

    def create_chunked_upload_target(
        self,
        *,
        tenant_id: str,
        job_id: str,
        file_name: str,
        content_type: str,
        file_size_bytes: int,
    ) -> ChunkedUploadTarget:
        safe_name = _safe_file_name(file_name)
        storage_key = _join_storage_key(settings.upload_bucket_prefix, tenant_id, job_id, safe_name)
        part_size_bytes = max(5 * 1024 * 1024, settings.object_storage_chunked_ingest_chunk_size_bytes)
        max_concurrency = max(1, settings.object_storage_chunked_ingest_max_concurrency)
        part_count = max(1, (file_size_bytes + part_size_bytes - 1) // part_size_bytes)
        parts: list[MultipartUploadPartTarget] = []
        for part_number in range(1, part_count + 1):
            key = chunk_storage_key(storage_key, part_number)
            url = self.client.generate_presigned_url(
                ClientMethod="put_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": key,
                },
                ExpiresIn=self.presign_expiry_sec,
                HttpMethod="PUT",
            )
            parts.append(
                MultipartUploadPartTarget(
                    part_number=part_number,
                    method="PUT",
                    url=self._rewrite_public_url(url),
                    headers={},
                )
            )

        return ChunkedUploadTarget(
            storage_key=storage_key,
            upload_id=f"chunked-{job_id}",
            part_size_bytes=part_size_bytes,
            max_concurrency=max_concurrency,
            parts=parts,
        )

    def complete_multipart_upload(
        self,
        *,
        storage_key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> str | None:
        normalized_parts = [
            {
                "PartNumber": int(part["partNumber"]),
                "ETag": str(part["etag"]),
            }
            for part in sorted(parts, key=lambda item: int(item["partNumber"]))
        ]
        response = self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=storage_key,
            UploadId=upload_id,
            MultipartUpload={"Parts": normalized_parts},
        )
        return response.get("ETag")

    def abort_multipart_upload(self, *, storage_key: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=storage_key,
            UploadId=upload_id,
        )

    def abort_chunked_upload(self, *, storage_key: str) -> None:
        self.delete_prefix(f"{storage_key}.chunks/")

    def delete_object(self, storage_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=storage_key)

    def delete_prefix(self, prefix: str) -> None:
        continuation_token: str | None = None

        while True:
            params: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": prefix,
            }
            if continuation_token:
                params["ContinuationToken"] = continuation_token
            response = self.client.list_objects_v2(**params)
            contents = response.get("Contents") or []
            if contents:
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={
                        "Objects": [{"Key": str(item["Key"])} for item in contents if item.get("Key")],
                        "Quiet": True,
                    },
                )

            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")

    def has_active_multipart_upload(self, storage_key: str) -> bool:
        return self.get_active_multipart_upload_id(storage_key) is not None

    def get_active_multipart_upload_id(self, storage_key: str) -> str | None:
        key_marker: str | None = None
        upload_id_marker: str | None = None

        while True:
            params: dict[str, Any] = {
                "Bucket": self.bucket,
                "Prefix": storage_key,
            }
            if key_marker:
                params["KeyMarker"] = key_marker
            if upload_id_marker:
                params["UploadIdMarker"] = upload_id_marker

            response = self.client.list_multipart_uploads(**params)
            for upload in response.get("Uploads") or []:
                if upload.get("Key") == storage_key:
                    return str(upload["UploadId"])

            if not response.get("IsTruncated"):
                return None

            key_marker = response.get("NextKeyMarker")
            upload_id_marker = response.get("NextUploadIdMarker")

    def list_uploaded_multipart_parts(self, *, storage_key: str, upload_id: str) -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        part_number_marker = 0

        while True:
            response = self.client.list_parts(
                Bucket=self.bucket,
                Key=storage_key,
                UploadId=upload_id,
                PartNumberMarker=part_number_marker,
            )
            for part in response.get("Parts") or []:
                part_number = part.get("PartNumber")
                etag = part.get("ETag")
                if part_number is None or not etag:
                    continue
                parts.append(
                    {
                        "partNumber": int(part_number),
                        "etag": str(etag),
                    }
                )

            if not response.get("IsTruncated"):
                break
            part_number_marker = int(response.get("NextPartNumberMarker") or 0)

        return parts

    def build_download_url(self, storage_key: str, *, expires_in: Optional[int] = None) -> str:
        url = self.client.generate_presigned_url(
            ClientMethod="get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=expires_in or self.presign_expiry_sec,
            HttpMethod="GET",
        )
        return self._rewrite_public_url(url)

    def probe_object(self, storage_key: str) -> tuple[bool, Optional[int]]:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=storage_key)
        except Exception:
            return False, None
        size = response.get("ContentLength")
        return True, int(size) if size is not None else None

    def upload_local_file(self, *, storage_key: str, local_path: Path, content_type: Optional[str] = None) -> None:
        extra_args: dict[str, str] = {}
        if content_type:
            extra_args["ContentType"] = content_type
        self.client.upload_file(
            str(local_path),
            self.bucket,
            storage_key,
            ExtraArgs=extra_args or None,
        )

    def open_download_stream(self, storage_key: str) -> tuple[Any, Optional[str], Optional[int]]:
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        body = response["Body"]
        return body, response.get("ContentType"), response.get("ContentLength")
