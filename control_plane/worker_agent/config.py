from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
                if len(parts) == 3:
                    return parts[0]
                return parts[1]
    return "auto"


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOCAL_ROOT = Path(os.environ.get("WORKER_LOCAL_ROOT", "/tmp/aether-control-worker"))


@dataclass(frozen=True)
class WorkerConfig:
    control_plane_base_url: str = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8080")
    control_plane_timeout_sec: float = _env_float("CONTROL_PLANE_TIMEOUT_SEC", 15.0)

    provider: str = os.environ.get("WORKER_PROVIDER", "vast")
    region: str = os.environ.get("WORKER_REGION", "us-central")
    instance_label: str = os.environ.get("WORKER_INSTANCE_LABEL", "vast-unknown")
    host_fingerprint: str = os.environ.get("WORKER_HOST_FINGERPRINT", "sha256:unknown")
    gpu_model: str = os.environ.get("WORKER_GPU_MODEL", "RTX 5090")
    gpu_count: int = _env_int("WORKER_GPU_COUNT", 1)
    vram_mb: int = _env_int("WORKER_VRAM_MB", 32607)
    cpu_cores: int = _env_int("WORKER_CPU_CORES", 32)
    ram_mb: int = _env_int("WORKER_RAM_MB", 131072)
    disk_free_mb: int = _env_int("WORKER_DISK_FREE_MB", 200000)
    software_version: str = os.environ.get("WORKER_SOFTWARE_VERSION", "worker-0.2.0")

    heartbeat_interval_override_sec: int = _env_int("WORKER_HEARTBEAT_INTERVAL_OVERRIDE_SEC", 0)
    pull_interval_override_sec: int = _env_int("WORKER_PULL_INTERVAL_OVERRIDE_SEC", 0)
    runtime_poll_interval_sec: float = _env_float("WORKER_RUNTIME_POLL_INTERVAL_SEC", 5.0)
    scheduler_tick_interval_sec: float = _env_float("WORKER_SCHEDULER_TICK_INTERVAL_SEC", 0.5)
    chunk_manifest_poll_interval_sec: float = _env_float("WORKER_CHUNK_MANIFEST_POLL_INTERVAL_SEC", 2.0)
    control_plane_cancel_poll_every: int = _env_int("WORKER_CONTROL_PLANE_CANCEL_POLL_EVERY", 1)
    runtime_stale_timeout_sec: int = _env_int("WORKER_RUNTIME_STALE_TIMEOUT_SEC", 150)
    max_parallel_prep_jobs: int = _env_int("WORKER_MAX_PARALLEL_PREP_JOBS", 1)
    max_parallel_gpu_jobs: int = _env_int("WORKER_MAX_PARALLEL_GPU_JOBS", 1)
    max_active_jobs: int = _env_int("WORKER_MAX_ACTIVE_JOBS", 1)
    autofallback_policy: str = os.environ.get("AETHER_AUTOFALLBACK_POLICY", "official_default")

    donor_root: str = os.environ.get("WORKER_DONOR_ROOT", "/root/donor_whitebox")
    donor_output_directory: str = os.environ.get("WORKER_DONOR_OUTPUT_DIR", "/root/donor_whitebox/outputs")
    donor_retained_artifact_directory: str = os.environ.get(
        "WORKER_DONOR_RETAINED_ARTIFACT_DIR",
        "/root/donor_whitebox/outputs/_retained_final_3dgs",
    )
    retain_local_primary_artifact: bool = _env_bool("WORKER_RETAIN_LOCAL_PRIMARY_ARTIFACT", False)
    upload_auxiliary_artifacts: bool = _env_bool("WORKER_UPLOAD_AUXILIARY_ARTIFACTS", False)
    donor_logs_directory: str = os.environ.get("WORKER_DONOR_LOGS_DIR", "/root/donor_whitebox/logs")
    donor_start_script: str = os.environ.get(
        "WORKER_DONOR_START_SCRIPT",
        "/root/donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh",
    )
    donor_prep_script: str = os.environ.get(
        "WORKER_DONOR_PREP_SCRIPT",
        "/root/donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
    )
    donor_prep_python: str = os.environ.get("WORKER_DONOR_PREP_PYTHON", "/venv/hislam2/bin/python")
    donor_train_script: str = os.environ.get(
        "WORKER_DONOR_TRAIN_SCRIPT",
        "/root/donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
    )
    donor_audit_python: str = os.environ.get("WORKER_DONOR_AUDIT_PYTHON", "/venv/unik3d/bin/python")
    stream_prewarm_enabled: bool = _env_bool("WORKER_STREAM_PREWARM_ENABLED", False)
    stream_prewarm_poll_interval_sec: float = _env_float("WORKER_STREAM_PREWARM_POLL_INTERVAL_SEC", 1.0)
    stream_live_sfm_enabled: bool = _env_bool("WORKER_STREAM_LIVE_SFM_ENABLED", False)
    stream_live_sfm_min_frames: int = _env_int("WORKER_STREAM_LIVE_SFM_MIN_FRAMES", 8)
    stream_live_sfm_min_new_frames: int = _env_int("WORKER_STREAM_LIVE_SFM_MIN_NEW_FRAMES", 4)
    stream_live_sfm_max_frames_cap: int = _env_int("WORKER_STREAM_LIVE_SFM_MAX_FRAMES_CAP", 48)
    stream_live_sfm_finalize_grace_sec: float = _env_float("WORKER_STREAM_LIVE_SFM_FINALIZE_GRACE_SEC", 20.0)
    stream_promote_live_sfm_after_upload: bool = _env_bool("WORKER_STREAM_PROMOTE_LIVE_SFM_AFTER_UPLOAD", False)
    stream_promote_live_sfm_min_selected_frames: int = _env_int("WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_SELECTED_FRAMES", 20)
    stream_promote_live_sfm_min_registered_images: int = _env_int("WORKER_STREAM_PROMOTE_LIVE_SFM_MIN_REGISTERED_IMAGES", 8)
    stream_train_seed_enabled: bool = _env_bool("WORKER_STREAM_TRAIN_SEED_ENABLED", False)
    stream_train_seed_poll_interval_sec: float = _env_float("WORKER_STREAM_TRAIN_SEED_POLL_INTERVAL_SEC", 1.0)
    stream_train_seed_min_selected_frames: int = _env_int("WORKER_STREAM_TRAIN_SEED_MIN_SELECTED_FRAMES", 8)
    stream_train_seed_min_registered_images: int = _env_int("WORKER_STREAM_TRAIN_SEED_MIN_REGISTERED_IMAGES", 4)
    stream_train_seed_finalize_grace_sec: float = _env_float("WORKER_STREAM_TRAIN_SEED_FINALIZE_GRACE_SEC", 2.0)
    stream_train_seed_skip_tsdf: bool = _env_bool("WORKER_STREAM_TRAIN_SEED_SKIP_TSDF", True)
    stream_artifact_stage_enabled: bool = _env_bool("WORKER_STREAM_ARTIFACT_STAGE_ENABLED", False)
    stream_artifact_stage_min_stable_polls: int = _env_int("WORKER_STREAM_ARTIFACT_STAGE_MIN_STABLE_POLLS", 2)
    stream_artifact_publish_early: bool = _env_bool("WORKER_STREAM_ARTIFACT_PUBLISH_EARLY", False)
    skip_training_probe_gate: bool = True

    local_root: str = os.environ.get("WORKER_LOCAL_ROOT", str(_DEFAULT_LOCAL_ROOT))
    local_input_directory: str = os.environ.get(
        "WORKER_LOCAL_INPUT_DIR",
        str(_DEFAULT_LOCAL_ROOT / "inputs"),
    )
    local_artifact_directory: str = os.environ.get(
        "WORKER_LOCAL_ARTIFACT_DIR",
        str(_DEFAULT_LOCAL_ROOT / "artifacts"),
    )
    runtime_directory: str = os.environ.get("WORKER_RUNTIME_DIR", "/root/control_plane/runtime")
    liveness_heartbeat_file: str = os.environ.get(
        "WORKER_LIVENESS_HEARTBEAT_FILE",
        "/root/control_plane/runtime/worker_agent.liveness.json",
    )
    liveness_stale_restart_sec: int = _env_int("WORKER_LIVENESS_STALE_RESTART_SEC", 180)

    object_storage_endpoint_url: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL", "")
    object_storage_region: str = _default_storage_region()
    object_storage_bucket: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_BUCKET", "")
    artifact_bucket_prefix: str = os.environ.get("CONTROL_PLANE_ARTIFACT_PREFIX", "artifacts")
    object_storage_access_key_id: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID", "")
    object_storage_secret_access_key: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY", "")
    object_storage_session_token: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_SESSION_TOKEN", "")
    object_storage_public_base_url: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_PUBLIC_BASE_URL", "")
    object_storage_addressing_style: str = os.environ.get("CONTROL_PLANE_OBJECT_STORAGE_ADDRESSING_STYLE", "auto")
    object_storage_presign_expiry_sec: int = _env_int("CONTROL_PLANE_OBJECT_STORAGE_PRESIGN_EXPIRY_SEC", 3600)

    @property
    def repo_root(self) -> Path:
        return Path(_DEFAULT_REPO_ROOT)


config = WorkerConfig()
