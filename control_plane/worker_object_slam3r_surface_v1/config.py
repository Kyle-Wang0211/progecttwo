from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _env_flag(name: str, default: bool) -> bool:
    fallback = "1" if default else "0"
    return os.environ.get(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


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


def _default_matcha_command_template() -> str:
    script_path = (
        _DEFAULT_REPO_ROOT
        / "control_plane"
        / "worker_object_slam3r_surface_v1"
        / "scripts"
        / "run_matcha_official.sh"
    )
    return f"bash {script_path} {{repo_dir}} {{scene_dir}} {{sparse2dgs_dir}} {{output_dir}}"


_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOCAL_ROOT = Path(
    os.environ.get("OBJECT_SLAM3R_SURFACE_LOCAL_ROOT")
    or os.environ.get("OBJECT_FAST_PUBLISH_LOCAL_ROOT")
    or "/tmp/aether-object-slam3r-surface"
)


@dataclass(frozen=True)
class WorkerConfig:
    control_plane_base_url: str = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8080")
    control_plane_timeout_sec: float = _env_float("CONTROL_PLANE_TIMEOUT_SEC", 15.0)

    provider: str = os.environ.get("OBJECT_SLAM3R_SURFACE_PROVIDER") or os.environ.get("OBJECT_FAST_PUBLISH_PROVIDER", "unconfigured")
    region: str = os.environ.get("OBJECT_SLAM3R_SURFACE_REGION") or os.environ.get("OBJECT_FAST_PUBLISH_REGION", "us-central")
    instance_label: str = os.environ.get("OBJECT_SLAM3R_SURFACE_INSTANCE_LABEL", "object-slam3r-surface-v1")
    host_fingerprint: str = os.environ.get("OBJECT_SLAM3R_SURFACE_HOST_FINGERPRINT", "")
    gpu_model: str = os.environ.get("OBJECT_SLAM3R_SURFACE_GPU_MODEL") or os.environ.get("OBJECT_FAST_PUBLISH_GPU_MODEL", "RTX 5090")
    gpu_count: int = _env_int("OBJECT_SLAM3R_SURFACE_GPU_COUNT", _env_int("OBJECT_FAST_PUBLISH_GPU_COUNT", 1))
    vram_mb: int = _env_int("OBJECT_SLAM3R_SURFACE_VRAM_MB", _env_int("OBJECT_FAST_PUBLISH_VRAM_MB", 24576))
    cpu_cores: int = _env_int("OBJECT_SLAM3R_SURFACE_CPU_CORES", _env_int("OBJECT_FAST_PUBLISH_CPU_CORES", 16))
    ram_mb: int = _env_int("OBJECT_SLAM3R_SURFACE_RAM_MB", _env_int("OBJECT_FAST_PUBLISH_RAM_MB", 65536))
    scheduler_tick_interval_sec: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_SCHEDULER_TICK_INTERVAL_SEC",
        _env_float("OBJECT_FAST_PUBLISH_SCHEDULER_TICK_INTERVAL_SEC", 1.0),
    )
    heartbeat_interval_sec: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_HEARTBEAT_INTERVAL_SEC",
        _env_int("OBJECT_FAST_PUBLISH_HEARTBEAT_INTERVAL_SEC", 15),
    )
    claim_enabled: bool = _env_flag("OBJECT_SLAM3R_SURFACE_CLAIM_ENABLED", False)
    standby_note: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_STANDBY_NOTE",
        "official repos are pinned, but native data contracts are not validated yet",
    )

    local_root: str = os.environ.get("OBJECT_SLAM3R_SURFACE_LOCAL_ROOT", str(_DEFAULT_LOCAL_ROOT))
    local_jobs_directory: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_LOCAL_JOBS_DIR",
        str(_DEFAULT_LOCAL_ROOT / "jobs"),
    )

    ffmpeg_bin: str = os.environ.get("OBJECT_SLAM3R_SURFACE_FFMPEG_BIN") or os.environ.get("OBJECT_FAST_PUBLISH_FFMPEG_BIN", "ffmpeg")
    extract_fps: float = _env_float("OBJECT_SLAM3R_SURFACE_EXTRACT_FPS", _env_float("OBJECT_FAST_PUBLISH_EXTRACT_FPS", 2.0))
    curated_max_frames: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MAX_FRAMES", 64)
    curated_min_blur_score: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_BLUR_SCORE", 24.0)
    curated_dark_threshold_brightness: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_DARK_THRESHOLD_BRIGHTNESS", 60.0)
    curated_bright_threshold_brightness: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_BRIGHT_THRESHOLD_BRIGHTNESS", 200.0)
    curated_max_frame_similarity: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MAX_FRAME_SIMILARITY", 0.92)
    curated_min_accept_interval_sec: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_ACCEPT_INTERVAL_SEC", 0.28)
    curated_min_orb_features: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MIN_ORB_FEATURES", 500)
    curated_warn_orb_features: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_WARN_ORB_FEATURES", 800)
    curated_min_target_signal: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_TARGET_SIGNAL", 0.10)
    curated_warn_target_signal: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_WARN_TARGET_SIGNAL", 0.16)
    curated_min_global_variance: float = _env_float("OBJECT_SLAM3R_SURFACE_CURATED_MIN_GLOBAL_VARIANCE", 10.0)
    curated_min_slam_frames: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_CURATED_MIN_SLAM_FRAMES",
        _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MIN_SURFACE_FRAMES", 12),
    )
    curated_min_surface_frames: int = _env_int("OBJECT_SLAM3R_SURFACE_CURATED_MIN_SURFACE_FRAMES", 12)

    slam3r_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SLAM3R_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "SLAM3R"),
    )
    slam3r_command_template: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SLAM3R_COMMAND", "")
    slam3r_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SLAM3R_SUMMARY_FILENAME", "slam3r.json")
    slam3r_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_SLAM3R_TIMEOUT_SEC", 7200)

    sparse2dgs_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "Sparse2DGS"),
    )
    sparse2dgs_command_template: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_COMMAND", "")
    sparse2dgs_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_SUMMARY_FILENAME", "sparse2dgs_surface.json")
    sparse2dgs_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_TIMEOUT_SEC", 7200)
    sparse2dgs_target_views: int = _env_int("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_TARGET_VIEWS", 24)
    sparse2dgs_contract_max_image_size: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_SPARSE2DGS_CONTRACT_MAX_IMAGE_SIZE",
        1536,
    )

    matcha_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_MATCHA_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "MAtCha"),
    )
    matcha_command_template: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_MATCHA_COMMAND",
        _default_matcha_command_template(),
    )
    matcha_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_MATCHA_SUMMARY_FILENAME", "matcha_mesh.json")
    matcha_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_MATCHA_TIMEOUT_SEC", 7200)
    delivery_mesh_summary_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_MESH_SUMMARY_FILENAME",
        "delivery_mesh.json",
    )
    delivery_texture_summary_filename: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_SUMMARY_FILENAME",
        "delivery_texture.json",
    )
    delivery_preserve_geometry_default: bool = _env_flag(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_PRESERVE_GEOMETRY",
        True,
    )
    delivery_target_face_count: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TARGET_FACE_COUNT", 180000)
    delivery_hard_max_face_count: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_HARD_MAX_FACE_COUNT", 240000)
    delivery_component_min_faces: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_COMPONENT_MIN_FACES", 128)
    delivery_component_ratio_floor: float = _env_float("OBJECT_SLAM3R_SURFACE_DELIVERY_COMPONENT_RATIO_FLOOR", 0.01)
    delivery_texture_max_views: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_MAX_VIEWS", 24)
    delivery_texture_atlas_size: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_ATLAS_SIZE", 4096)
    delivery_projection_image_size: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_PROJECTION_IMAGE_SIZE", 2048)
    delivery_texture_fill_kernel: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_FILL_KERNEL", 5)
    delivery_texture_min_view_cosine: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_MIN_VIEW_COSINE",
        -0.15,
    )
    delivery_texture_view_consistency_margin: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_VIEW_CONSISTENCY_MARGIN",
        0.12,
    )
    delivery_texture_view_smoothing_rounds: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_TEXTURE_VIEW_SMOOTHING_ROUNDS",
        2,
    )
    delivery_hole_fill_iterations: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_HOLE_FILL_ITERATIONS", 3)
    delivery_small_hole_max_edges: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_SMALL_HOLE_MAX_EDGES", 96)
    delivery_small_hole_max_perimeter_ratio: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_SMALL_HOLE_MAX_PERIMETER_RATIO",
        0.06,
    )
    delivery_aggressive_repair_face_cap: int = _env_int(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_AGGRESSIVE_REPAIR_FACE_CAP",
        250000,
    )
    delivery_taubin_iterations: int = _env_int("OBJECT_SLAM3R_SURFACE_DELIVERY_TAUBIN_ITERATIONS", 8)
    delivery_taubin_lambda: float = _env_float("OBJECT_SLAM3R_SURFACE_DELIVERY_TAUBIN_LAMBDA", 0.45)
    delivery_taubin_nu: float = _env_float("OBJECT_SLAM3R_SURFACE_DELIVERY_TAUBIN_NU", -0.5)
    delivery_feature_preserve_angle_deg: float = _env_float(
        "OBJECT_SLAM3R_SURFACE_DELIVERY_FEATURE_PRESERVE_ANGLE_DEG",
        28.0,
    )

    sugar_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_SUGAR_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "SuGaR"),
    )
    sugar_command_template: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SUGAR_COMMAND", "")
    sugar_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_SUGAR_SUMMARY_FILENAME", "sugar_mesh.json")
    sugar_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_SUGAR_TIMEOUT_SEC", 7200)

    hgs_repo: str = os.environ.get(
        "OBJECT_SLAM3R_SURFACE_HGS_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "3D-HGS"),
    )
    hgs_command_template: str = os.environ.get("OBJECT_SLAM3R_SURFACE_HGS_COMMAND", "")
    hgs_summary_filename: str = os.environ.get("OBJECT_SLAM3R_SURFACE_HGS_SUMMARY_FILENAME", "3dhgs.json")
    hgs_stage_timeout_sec: int = _env_int("OBJECT_SLAM3R_SURFACE_HGS_TIMEOUT_SEC", 7200)
    enable_hgs_refine_default: bool = _env_flag("OBJECT_SLAM3R_SURFACE_ENABLE_HGS_REFINE_DEFAULT", False)

    default_camera_pitch_deg: float = _env_float("OBJECT_SLAM3R_SURFACE_DEFAULT_CAMERA_PITCH_DEG", 16.0)
    default_camera_yaw_deg: float = _env_float("OBJECT_SLAM3R_SURFACE_DEFAULT_CAMERA_YAW_DEG", -26.0)
    default_camera_distance_scale: float = _env_float("OBJECT_SLAM3R_SURFACE_DEFAULT_CAMERA_DISTANCE_SCALE", 2.4)

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
