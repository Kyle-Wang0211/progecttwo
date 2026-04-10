from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


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
_DEFAULT_LOCAL_ROOT = Path(
    os.environ.get("OBJECT_SPLATSLAM_LOCAL_ROOT")
    or os.environ.get("OBJECT_FAST_PUBLISH_LOCAL_ROOT")
    or "/tmp/aether-object-splatslam"
)
_DEFAULT_RUNNER_SCRIPT = Path(__file__).resolve().parent / "scripts" / "run_official_splatslam.py"


@dataclass(frozen=True)
class WorkerConfig:
    control_plane_base_url: str = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:8080")
    control_plane_timeout_sec: float = _env_float("CONTROL_PLANE_TIMEOUT_SEC", 15.0)

    provider: str = os.environ.get("OBJECT_SPLATSLAM_PROVIDER") or os.environ.get("OBJECT_FAST_PUBLISH_PROVIDER", "unconfigured")
    region: str = os.environ.get("OBJECT_SPLATSLAM_REGION") or os.environ.get("OBJECT_FAST_PUBLISH_REGION", "us-central")
    instance_label: str = os.environ.get("OBJECT_SPLATSLAM_INSTANCE_LABEL") or os.environ.get("OBJECT_FAST_PUBLISH_INSTANCE_LABEL", "object-splatslam-v1")
    host_fingerprint: str = os.environ.get("OBJECT_SPLATSLAM_HOST_FINGERPRINT") or os.environ.get("OBJECT_FAST_PUBLISH_HOST_FINGERPRINT", "")
    gpu_model: str = os.environ.get("OBJECT_SPLATSLAM_GPU_MODEL") or os.environ.get("OBJECT_FAST_PUBLISH_GPU_MODEL", "RTX 5090")
    gpu_count: int = _env_int("OBJECT_SPLATSLAM_GPU_COUNT", _env_int("OBJECT_FAST_PUBLISH_GPU_COUNT", 1))
    vram_mb: int = _env_int("OBJECT_SPLATSLAM_VRAM_MB", _env_int("OBJECT_FAST_PUBLISH_VRAM_MB", 24576))
    cpu_cores: int = _env_int("OBJECT_SPLATSLAM_CPU_CORES", _env_int("OBJECT_FAST_PUBLISH_CPU_CORES", 16))
    ram_mb: int = _env_int("OBJECT_SPLATSLAM_RAM_MB", _env_int("OBJECT_FAST_PUBLISH_RAM_MB", 65536))

    scheduler_tick_interval_sec: float = _env_float("OBJECT_SPLATSLAM_SCHEDULER_TICK_INTERVAL_SEC", _env_float("OBJECT_FAST_PUBLISH_SCHEDULER_TICK_INTERVAL_SEC", 1.0))
    heartbeat_interval_sec: int = _env_int("OBJECT_SPLATSLAM_HEARTBEAT_INTERVAL_SEC", _env_int("OBJECT_FAST_PUBLISH_HEARTBEAT_INTERVAL_SEC", 15))

    local_root: str = os.environ.get("OBJECT_SPLATSLAM_LOCAL_ROOT") or os.environ.get("OBJECT_FAST_PUBLISH_LOCAL_ROOT", str(_DEFAULT_LOCAL_ROOT))
    local_jobs_directory: str = os.environ.get(
        "OBJECT_SPLATSLAM_LOCAL_JOBS_DIR",
        os.environ.get(
            "OBJECT_FAST_PUBLISH_LOCAL_JOBS_DIR",
        str(_DEFAULT_LOCAL_ROOT / "jobs"),
        ),
    )

    ffmpeg_bin: str = os.environ.get("OBJECT_SPLATSLAM_FFMPEG_BIN") or os.environ.get("OBJECT_FAST_PUBLISH_FFMPEG_BIN", "ffmpeg")
    extract_fps: float = _env_float("OBJECT_SPLATSLAM_EXTRACT_FPS", _env_float("OBJECT_FAST_PUBLISH_EXTRACT_FPS", 2.0))
    curated_max_frames: int = _env_int("OBJECT_SPLATSLAM_CURATED_MAX_FRAMES", _env_int("OBJECT_FAST_PUBLISH_CURATED_MAX_FRAMES", 64))
    curated_min_blur_score: float = _env_float("OBJECT_SPLATSLAM_CURATED_MIN_BLUR_SCORE", _env_float("OBJECT_FAST_PUBLISH_CURATED_MIN_BLUR_SCORE", 24.0))
    curated_dark_threshold_brightness: float = _env_float(
        "OBJECT_SPLATSLAM_CURATED_DARK_THRESHOLD_BRIGHTNESS",
        _env_float("OBJECT_FAST_PUBLISH_CURATED_DARK_THRESHOLD_BRIGHTNESS", 60.0),
    )
    curated_bright_threshold_brightness: float = _env_float(
        "OBJECT_SPLATSLAM_CURATED_BRIGHT_THRESHOLD_BRIGHTNESS",
        _env_float("OBJECT_FAST_PUBLISH_CURATED_BRIGHT_THRESHOLD_BRIGHTNESS", 200.0),
    )
    curated_max_frame_similarity: float = _env_float(
        "OBJECT_SPLATSLAM_CURATED_MAX_FRAME_SIMILARITY",
        _env_float("OBJECT_FAST_PUBLISH_CURATED_MAX_FRAME_SIMILARITY", 0.92),
    )
    curated_min_accept_interval_sec: float = _env_float(
        "OBJECT_SPLATSLAM_CURATED_MIN_ACCEPT_INTERVAL_SEC",
        _env_float("OBJECT_FAST_PUBLISH_CURATED_MIN_ACCEPT_INTERVAL_SEC", 0.28),
    )
    curated_min_slam_frames: int = _env_int(
        "OBJECT_SPLATSLAM_CURATED_MIN_SLAM_FRAMES",
        12,
    )
    support_plane_ransac_iterations: int = _env_int(
        "OBJECT_SPLATSLAM_SUPPORT_PLANE_RANSAC_ITERATIONS",
        _env_int("OBJECT_FAST_PUBLISH_SUPPORT_PLANE_RANSAC_ITERATIONS", 256),
    )
    support_plane_distance_ratio: float = _env_float(
        "OBJECT_SPLATSLAM_SUPPORT_PLANE_DISTANCE_RATIO",
        _env_float("OBJECT_FAST_PUBLISH_SUPPORT_PLANE_DISTANCE_RATIO", 0.014),
    )
    support_plane_min_distance: float = _env_float(
        "OBJECT_SPLATSLAM_SUPPORT_PLANE_MIN_DISTANCE",
        _env_float("OBJECT_FAST_PUBLISH_SUPPORT_PLANE_MIN_DISTANCE", 0.006),
    )
    support_band_height_ratio: float = _env_float(
        "OBJECT_SPLATSLAM_SUPPORT_BAND_HEIGHT_RATIO",
        _env_float("OBJECT_FAST_PUBLISH_SUPPORT_BAND_HEIGHT_RATIO", 0.065),
    )
    support_patch_padding_ratio: float = _env_float(
        "OBJECT_SPLATSLAM_SUPPORT_PATCH_PADDING_RATIO",
        _env_float("OBJECT_FAST_PUBLISH_SUPPORT_PATCH_PADDING_RATIO", 0.22),
    )
    cleanup_object_padding_ratio: float = _env_float(
        "OBJECT_SPLATSLAM_CLEANUP_OBJECT_PADDING_RATIO",
        _env_float("OBJECT_FAST_PUBLISH_CLEANUP_OBJECT_PADDING_RATIO", 0.34),
    )
    cleanup_support_padding_ratio: float = _env_float(
        "OBJECT_SPLATSLAM_CLEANUP_SUPPORT_PADDING_RATIO",
        _env_float("OBJECT_FAST_PUBLISH_CLEANUP_SUPPORT_PADDING_RATIO", 0.18),
    )
    cleanup_drop_below_plane: float = _env_float(
        "OBJECT_SPLATSLAM_CLEANUP_DROP_BELOW_PLANE",
        _env_float("OBJECT_FAST_PUBLISH_CLEANUP_DROP_BELOW_PLANE", 0.012),
    )

    splatslam_backend: str = os.environ.get("OBJECT_SPLATSLAM_BACKEND", "splat_slam")
    matching_frontend: str = os.environ.get("OBJECT_SPLATSLAM_MATCHING_FRONTEND", "sam2_mask_lite")
    mask_backend: str = os.environ.get("OBJECT_SPLATSLAM_MASK_BACKEND", "sam2")
    cleanup_backend: str = os.environ.get("OBJECT_SPLATSLAM_CLEANUP_BACKEND", "open3d")
    hq_refine_backend: str = os.environ.get("OBJECT_SPLATSLAM_HQ_REFINE_BACKEND", "graphdeco_3dgs")
    optional_mesh_export_backend: str = os.environ.get("OBJECT_SPLATSLAM_OPTIONAL_MESH_EXPORT_BACKEND", "openmvs_fallback")
    splatslam_python_bin: str = os.environ.get("OBJECT_SPLATSLAM_PYTHON_BIN", sys.executable or "python3")
    splatslam_repo: str = os.environ.get(
        "OBJECT_SPLATSLAM_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "Splat-SLAM"),
    )
    splatslam_entrypoint: str = os.environ.get("OBJECT_SPLATSLAM_ENTRYPOINT", "run.py")
    splatslam_runner_script: str = os.environ.get(
        "OBJECT_SPLATSLAM_RUNNER",
        str(_DEFAULT_RUNNER_SCRIPT),
    )
    splatslam_custom_config_dir: str = os.environ.get(
        "OBJECT_SPLATSLAM_CUSTOM_CONFIG_DIR",
        str(_DEFAULT_LOCAL_ROOT / "configs"),
    )
    splatslam_export_format: str = os.environ.get("OBJECT_SPLATSLAM_EXPORT_FORMAT", "ply")
    splatslam_primary_asset_kind: str = os.environ.get("OBJECT_SPLATSLAM_PRIMARY_ASSET_KIND", "splat")
    splatslam_assumed_horizontal_fov_degrees: float = _env_float(
        "OBJECT_SPLATSLAM_ASSUMED_HORIZONTAL_FOV_DEGREES",
        60.0,
    )
    splatslam_final_refine_iters: int = _env_int(
        "OBJECT_SPLATSLAM_FINAL_REFINE_ITERS",
        26000,
    )
    splatslam_tracking_warmup: int = _env_int(
        "OBJECT_SPLATSLAM_TRACKING_WARMUP",
        8,
    )
    splatslam_enable_online_ba: bool = os.environ.get(
        "OBJECT_SPLATSLAM_ENABLE_ONLINE_BA",
        "1",
    ).strip().lower() in {"1", "true", "yes", "on"}
    splatslam_init_iters: int = _env_int(
        "OBJECT_SPLATSLAM_INIT_ITERS",
        1050,
    )
    splatslam_mapping_iters: int = _env_int(
        "OBJECT_SPLATSLAM_MAPPING_ITERS",
        60,
    )
    splatslam_frontend_init_update_iters: int = _env_int(
        "OBJECT_SPLATSLAM_FRONTEND_INIT_UPDATE_ITERS",
        2,
    )
    splatslam_frontend_init_proximity_radius: int = _env_int(
        "OBJECT_SPLATSLAM_FRONTEND_INIT_PROXIMITY_RADIUS",
        1,
    )
    splatslam_frontend_init_proximity_nms: int = _env_int(
        "OBJECT_SPLATSLAM_FRONTEND_INIT_PROXIMITY_NMS",
        1,
    )
    splatslam_frontend_keyframe_thresh: float = _env_float(
        "OBJECT_SPLATSLAM_FRONTEND_KEYFRAME_THRESH",
        4.0,
    )
    splatslam_frontend_motion_filter_thresh: float = _env_float(
        "OBJECT_SPLATSLAM_FRONTEND_MOTION_FILTER_THRESH",
        4.0,
    )
    splatslam_frontend_window: int = _env_int(
        "OBJECT_SPLATSLAM_FRONTEND_WINDOW",
        25,
    )
    splatslam_frontend_max_factors: int = _env_int(
        "OBJECT_SPLATSLAM_FRONTEND_MAX_FACTORS",
        75,
    )
    splatslam_frontend_use_lowmem_update: bool = os.environ.get(
        "OBJECT_SPLATSLAM_FRONTEND_USE_LOWMEM_UPDATE",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}
    splatslam_frontend_lowmem_steps: int = _env_int(
        "OBJECT_SPLATSLAM_FRONTEND_LOWMEM_STEPS",
        2,
    )
    splatslam_frontend_skip_video_ba: bool = os.environ.get(
        "OBJECT_SPLATSLAM_FRONTEND_SKIP_VIDEO_BA",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}
    splatslam_output_max_edge: int = _env_int(
        "OBJECT_SPLATSLAM_OUTPUT_MAX_EDGE",
        640,
    )
    splatslam_alive_probe_interval_sec: float = _env_float(
        "OBJECT_SPLATSLAM_ALIVE_PROBE_INTERVAL_SEC",
        10.0,
    )
    splatslam_min_keyframes: int = _env_int(
        "OBJECT_SPLATSLAM_MIN_KEYFRAMES",
        4,
    )
    splatslam_apply_masks_to_rgb: bool = os.environ.get(
        "OBJECT_SPLATSLAM_APPLY_MASKS_TO_RGB",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}
    splatslam_enable_product_cleanup: bool = os.environ.get(
        "OBJECT_SPLATSLAM_ENABLE_PRODUCT_CLEANUP",
        "1",
    ).strip().lower() in {"1", "true", "yes", "on"}
    splatslam_default_camera_pitch_deg: float = _env_float("OBJECT_SPLATSLAM_DEFAULT_CAMERA_PITCH_DEG", 16.0)
    splatslam_default_camera_yaw_deg: float = _env_float("OBJECT_SPLATSLAM_DEFAULT_CAMERA_YAW_DEG", -26.0)
    splatslam_default_camera_distance_scale: float = _env_float(
        "OBJECT_SPLATSLAM_DEFAULT_CAMERA_DISTANCE_SCALE",
        2.4,
    )
    optional_mesh_export_enabled_default: bool = os.environ.get(
        "OBJECT_SPLATSLAM_OPTIONAL_MESH_EXPORT_ENABLED_DEFAULT",
        "0",
    ).strip().lower() in {"1", "true", "yes", "on"}
    colmap_bin: str = os.environ.get("OBJECT_FAST_PUBLISH_COLMAP_BIN", "colmap")
    colmap_use_gpu: bool = os.environ.get("OBJECT_FAST_PUBLISH_COLMAP_USE_GPU", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    colmap_camera_model: str = os.environ.get("OBJECT_FAST_PUBLISH_COLMAP_CAMERA_MODEL", "PINHOLE")
    colmap_single_camera: bool = os.environ.get(
        "OBJECT_FAST_PUBLISH_COLMAP_SINGLE_CAMERA", "1"
    ).strip().lower() in {"1", "true", "yes", "on"}

    hloc_python_bin: str = os.environ.get("OBJECT_FAST_PUBLISH_HLOC_PYTHON_BIN", "python3")
    hloc_repo: str = os.environ.get("OBJECT_FAST_PUBLISH_HLOC_REPO", "")

    sam2_python_bin: str = (
        os.environ.get("OBJECT_SPLATSLAM_SAM2_PYTHON_BIN")
        or os.environ.get("OBJECT_FAST_PUBLISH_SAM2_PYTHON_BIN")
        or os.environ.get("OBJECT_SPLATSLAM_PYTHON_BIN")
        or sys.executable
        or "python3"
    )
    sam2_repo: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_SAM2_REPO",
        str(_DEFAULT_REPO_ROOT / "third_party" / "sam2"),
    )
    sam2_checkpoint: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_SAM2_CHECKPOINT",
        str(_DEFAULT_REPO_ROOT / "third_party" / "sam2" / "checkpoints" / "sam2.1_hiera_large.pt"),
    )
    sam2_config: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_SAM2_CONFIG",
        "configs/sam2.1/sam2.1_hiera_l.yaml",
    )
    sam2_device: str = os.environ.get("OBJECT_FAST_PUBLISH_SAM2_DEVICE", "cuda")
    sam2_points_per_side: int = _env_int("OBJECT_FAST_PUBLISH_SAM2_POINTS_PER_SIDE", 16)

    openmvs_bin_dir: str = os.environ.get("OBJECT_FAST_PUBLISH_OPENMVS_BIN_DIR", "")
    openmvs_interface_colmap_bin: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_OPENMVS_INTERFACE_COLMAP_BIN", "InterfaceCOLMAP"
    )
    openmvs_densify_bin: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_OPENMVS_DENSIFY_BIN", "DensifyPointCloud"
    )
    openmvs_reconstruct_bin: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_OPENMVS_RECONSTRUCT_BIN", "ReconstructMesh"
    )
    openmvs_texture_bin: str = os.environ.get(
        "OBJECT_FAST_PUBLISH_OPENMVS_TEXTURE_BIN", "TextureMesh"
    )

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
