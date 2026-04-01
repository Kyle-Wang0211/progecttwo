from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv_tuple(name: str, default: str) -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    values = [item.strip() for item in raw.split(",")]
    return tuple(item for item in values if item)


def _default_storage_provider() -> str:
    explicit = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_PROVIDER")
    if explicit:
        return explicit
    if os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_BUCKET"):
        return "s3_compatible"
    return "fake"


def _default_storage_region() -> str:
    explicit = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_REGION")
    if explicit:
        return explicit

    for env_name in ("CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL", "CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL"):
        raw_url = os.environ.get(env_name, "").strip()
        if not raw_url:
            continue
        hostname = urlsplit(raw_url).hostname or raw_url
        hostname = hostname.strip().lower()
        if hostname.endswith(".digitaloceanspaces.com"):
            parts = hostname.split(".")
            if len(parts) >= 3:
                if parts[-3] == "digitaloceanspaces":
                    continue
                if len(parts) == 3:
                    return parts[0]
                return parts[1]
    return "auto"


def _default_ingress_mode() -> str:
    explicit = os.environ.get("CONTROL_PLANE_API_INGRESS_MODE")
    if explicit:
        return explicit
    base_url = os.environ.get("CONTROL_PLANE_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    if base_url.startswith("https://"):
        return "direct_tls"
    if "cloudflare" in base_url or ".lhr.life" in base_url:
        return "cloudflare_tunnel"
    return "direct_http"


@dataclass(frozen=True)
class Settings:
    environment: str = os.environ.get("CONTROL_PLANE_ENVIRONMENT", "development")
    service_name: str = os.environ.get("CONTROL_PLANE_SERVICE_NAME", "aether-control-plane")
    public_base_url: str = os.environ.get("CONTROL_PLANE_PUBLIC_BASE_URL", "http://127.0.0.1:8080")
    api_ingress_mode: str = _default_ingress_mode()
    database_url: str = os.environ.get("CONTROL_PLANE_DATABASE_URL", "")
    object_storage_provider: str = _default_storage_provider()
    object_storage_endpoint_url: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL", "")
    object_storage_region: str = _default_storage_region()
    object_storage_bucket: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_BUCKET", "")
    object_storage_access_key_id: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID", "")
    object_storage_secret_access_key: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY", "")
    object_storage_session_token: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_SESSION_TOKEN", "")
    object_storage_public_base_url: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL", "")
    object_storage_addressing_style: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE", "auto")
    object_storage_presign_expiry_sec: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_PRESIGN_EXPIRY_SEC", "3600")
    )
    object_storage_multipart_threshold_bytes: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_MULTIPART_THRESHOLD_BYTES", str(5 * 1024 * 1024))
    )
    object_storage_multipart_part_size_bytes: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_MULTIPART_PART_SIZE_BYTES", str(16 * 1024 * 1024))
    )
    object_storage_multipart_max_concurrency: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_MULTIPART_MAX_CONCURRENCY", "20")
    )
    object_storage_chunked_ingest_enabled: bool = _env_bool(
        "CONTROL_PLANE_OBJECT_STORAGE_CHUNKED_INGEST_ENABLED", True
    )
    object_storage_chunked_ingest_threshold_bytes: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_CHUNKED_INGEST_THRESHOLD_BYTES", str(5 * 1024 * 1024))
    )
    object_storage_chunked_ingest_chunk_size_bytes: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_CHUNKED_INGEST_CHUNK_SIZE_BYTES", str(8 * 1024 * 1024))
    )
    object_storage_chunked_ingest_max_concurrency: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_CHUNKED_INGEST_MAX_CONCURRENCY", "20")
    )
    object_storage_chunked_ingest_min_ready_bytes: int = int(
        os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_CHUNKED_INGEST_MIN_READY_BYTES", str(32 * 1024 * 1024))
    )
    object_storage_multipart_complete_grace_sec: int = _env_int(
        "CONTROL_PLANE_OBJECT_STORAGE_MULTIPART_COMPLETE_GRACE_SEC", 120
    )
    upload_bucket_prefix: str = os.environ.get("CONTROL_PLANE_UPLOAD_PREFIX", "uploads")
    artifact_bucket_prefix: str = os.environ.get("CONTROL_PLANE_ARTIFACT_PREFIX", "artifacts")
    heartbeat_interval_sec: int = int(os.environ.get("CONTROL_PLANE_HEARTBEAT_INTERVAL_SEC", "15"))
    pull_interval_sec: int = int(os.environ.get("CONTROL_PLANE_PULL_INTERVAL_SEC", "1"))
    worker_lease_ttl_sec: int = int(os.environ.get("CONTROL_PLANE_WORKER_LEASE_TTL_SEC", "120"))
    state_refresh_on_get_timeout_sec: float = float(
        os.environ.get("CONTROL_PLANE_STATE_REFRESH_ON_GET_TIMEOUT_SEC", "2.5")
    )
    worker_reservation_stale_sec: int = int(
        os.environ.get("CONTROL_PLANE_WORKER_RESERVATION_STALE_SEC", "45")
    )
    scheduler_min_worker_gpu_count: int = _env_int("CONTROL_PLANE_SCHEDULER_MIN_WORKER_GPU_COUNT", 1)
    scheduler_min_worker_vram_mb: int = _env_int("CONTROL_PLANE_SCHEDULER_MIN_WORKER_VRAM_MB", 16384)
    scheduler_min_worker_disk_free_mb: int = _env_int("CONTROL_PLANE_SCHEDULER_MIN_WORKER_DISK_FREE_MB", 50000)
    scheduler_required_worker_capabilities: tuple[str, ...] = _env_csv_tuple(
        "CONTROL_PLANE_SCHEDULER_REQUIRED_WORKER_CAPABILITIES",
        "hislam2,3dgs,worker_pull",
    )


settings = Settings()
