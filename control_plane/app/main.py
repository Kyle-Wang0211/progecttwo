from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from .config import settings
from .models import (
    ArtifactManifestRequest,
    CancelAckRequest,
    ClaimNextRequest,
    ClaimNextResponse,
    ClientLifecycleEventRequest,
    CompleteJobRequest,
    CreateJobRequest,
    CreateJobResponse,
    FailJobRequest,
    JobAssignment,
    JobInputDescriptor,
    JobState,
    JobStatusResponse,
    JobTimelineOverlap,
    JobTimelinePhase,
    JobTimelineSummary,
    RegisterWorkerRequest,
    RegisterWorkerResponse,
    RuntimeUpdateRequest,
    UploadCompleteRequest,
    UploadInitResponse,
    WorkerHeartbeatRequest,
    WorkerHeartbeatResponse,
    WorkerState,
)
from .repository import InMemoryRepository, JobRow, PostgresRepository
from .storage import FakeObjectStorageSigner, S3CompatibleObjectStorageSigner, chunk_storage_key

app = FastAPI(title="Aether Control Plane", version="0.1.0")
logger = logging.getLogger(__name__)


def create_repository():
    if settings.database_url:
        return PostgresRepository(settings.database_url)
    return InMemoryRepository()


def create_storage():
    provider = (settings.object_storage_provider or "").strip().lower()
    if provider in {"fake", "dev_fake", "local_fake"}:
        return FakeObjectStorageSigner()
    if provider in {"s3", "s3_compatible", "aws_s3", "r2", "b2", "managed"}:
        return S3CompatibleObjectStorageSigner()
    raise RuntimeError(f"unsupported_object_storage_provider:{provider}")


repo = create_repository()
storage = create_storage()

TIMELINE_PHASE_LABELS = {
    "prepare": "整理",
    "upload": "上传",
    "preprocess": "预处理",
    "train": "训练",
    "export": "导出",
    "return": "回传",
}

CLIENT_PREP_EVENT_TYPES = {
    "mobile_prepare_started",
    "mobile_prepare_first_bytes_written",
    "mobile_prepare_streaming_ready",
    "mobile_prepare_completed",
}

CLIENT_RETURN_EVENT_TYPES = {
    "mobile_download_started",
    "mobile_download_completed",
    "mobile_artifact_path_written",
    "mobile_viewer_opened",
}

UPLOAD_EVENT_TYPES = {
    "upload_window_opened",
    "upload_started",
    "chunked_upload_initialized",
    "multipart_part_ready",
    "chunk_part_ready",
    "upload_completed",
    "multipart_upload_completed",
    "chunked_upload_completed",
}

FRAME_SAMPLING_PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "full": {
        "label": "full",
        "title": "官方 HI-SLAM",
        "tier_name": "official_default",
        "max_frames": 0,
        "frame_fraction": 1.0,
    },
}

LEGACY_FRAME_SAMPLING_TIER_ALIASES: dict[str, str] = {}

DEFAULT_PIPELINE_PROFILE: dict[str, Any] = {
    "strategy": "autofallback",
    "max_total_minutes": 40,
    **FRAME_SAMPLING_PROFILE_PRESETS["full"],
}


def _event_created_at(event: dict[str, Any]) -> datetime | None:
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("event_at", "client_at", "reported_at"):
            value = payload.get(key)
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    continue
    created_at = event.get("created_at")
    if isinstance(created_at, datetime):
        return created_at
    if isinstance(created_at, str):
        try:
            return datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _normalize_frame_sampling_profile(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in FRAME_SAMPLING_PROFILE_PRESETS else "full"


def _sampling_profile_from_pipeline_payload(payload: Any) -> str:
    candidate = payload if isinstance(payload, dict) else {}

    direct_profile = _normalize_frame_sampling_profile(
        candidate.get("frame_sampling_profile")
        or candidate.get("sampling_profile")
        or candidate.get("collection_profile")
        or candidate.get("capture_density_profile")
    )
    if direct_profile != "full":
        return direct_profile

    label = _normalize_frame_sampling_profile(candidate.get("label"))
    if label != "full":
        return label

    tier_name = str(candidate.get("tier_name") or "").strip().lower()
    if tier_name in LEGACY_FRAME_SAMPLING_TIER_ALIASES:
        return LEGACY_FRAME_SAMPLING_TIER_ALIASES[tier_name]
    for profile_name, preset in FRAME_SAMPLING_PROFILE_PRESETS.items():
        if str(preset.get("tier_name") or "").strip().lower() == tier_name:
            return profile_name

    title = str(candidate.get("requested_preset_title") or candidate.get("title") or "").strip()
    for profile_name, preset in FRAME_SAMPLING_PROFILE_PRESETS.items():
        if str(preset.get("title") or "").strip() == title:
            return profile_name

    max_frames_value = candidate.get("max_frames")
    try:
        max_frames = int(max_frames_value)
    except (TypeError, ValueError):
        max_frames = None
    if max_frames is not None:
        for profile_name, preset in FRAME_SAMPLING_PROFILE_PRESETS.items():
            if int(preset.get("max_frames") or 0) == max_frames:
                return profile_name

    frame_fraction_value = candidate.get("requested_frame_fraction")
    if frame_fraction_value in (None, ""):
        frame_fraction_value = candidate.get("frame_fraction")
    try:
        frame_fraction = float(frame_fraction_value)
    except (TypeError, ValueError):
        frame_fraction = None
    if frame_fraction is not None:
        for profile_name, preset in FRAME_SAMPLING_PROFILE_PRESETS.items():
            preset_fraction = float(preset.get("frame_fraction") or 0.0)
            if abs(preset_fraction - frame_fraction) <= 0.02:
                return profile_name

    return "full"


def _requested_pipeline_profile_payload(payload: Any) -> dict[str, Any]:
    candidate = payload if isinstance(payload, dict) else {}
    sampling_profile = _sampling_profile_from_pipeline_payload(candidate)
    preset = FRAME_SAMPLING_PROFILE_PRESETS[sampling_profile]
    requested_title = str(
        candidate.get("requested_preset_title")
        or candidate.get("title")
        or preset.get("title")
        or ""
    ).strip() or str(preset.get("title") or "")
    requested_fraction = candidate.get("requested_frame_fraction")
    if requested_fraction in (None, ""):
        requested_fraction = candidate.get("frame_fraction")
    if requested_fraction in (None, ""):
        requested_fraction = preset.get("frame_fraction")
    return {
        "frame_sampling_profile": sampling_profile,
        "requested_preset_title": requested_title,
        "requested_frame_fraction": str(requested_fraction),
        **DEFAULT_PIPELINE_PROFILE,
        **preset,
    }


def _pipeline_profile_for_job(job_id: str) -> dict[str, Any]:
    profile = dict(DEFAULT_PIPELINE_PROFILE)
    events = repo.list_job_events(job_id, event_type="pipeline_profile_requested")
    if events:
        payload = events[-1].get("payload")
        profile.update(_requested_pipeline_profile_payload(payload))
    return profile


def _append_once_job_event(job_id: str, *, event_type: str, payload: dict[str, Any]) -> None:
    if repo.list_job_events(job_id, event_type=event_type):
        return
    repo.append_job_event(job_id, event_type=event_type, payload=payload)


def _runtime_stage_key(payload: dict[str, Any]) -> str:
    return str(payload.get("stage") or "").strip().lower()


def _runtime_phase_key(payload: dict[str, Any]) -> str:
    return str(payload.get("phase_name") or "").strip().lower()


def _runtime_state_key(payload: dict[str, Any]) -> str:
    return str(payload.get("state") or "").strip().lower()


def _runtime_basis_key(payload: dict[str, Any]) -> str:
    return str(payload.get("progress_basis") or "").strip().lower()


def _is_preprocess_runtime_payload(payload: dict[str, Any]) -> bool:
    stage = _runtime_stage_key(payload)
    phase = _runtime_phase_key(payload)
    state = _runtime_state_key(payload)
    if stage.startswith("train") or stage.startswith("export") or stage == "completed":
        return False
    if state in {JobState.ASSIGNED.value, JobState.RECONSTRUCTING.value}:
        return True
    if stage.startswith(("sfm", "prep", "stream", "reconstruct")):
        return True
    if phase.startswith(("matcher", "mapper", "audit", "extract", "feature", "seed_select", "live_sfm", "download")):
        return True
    return False


def _is_train_runtime_payload(payload: dict[str, Any]) -> bool:
    stage = _runtime_stage_key(payload)
    phase = _runtime_phase_key(payload)
    state = _runtime_state_key(payload)
    if state in {JobState.TRAINING_PROBE.value, JobState.TRAINING_FULL.value}:
        return True
    if stage.startswith("train"):
        return True
    return phase in {"seed_booting", "seed_full", "probe", "full"}


def _is_export_runtime_payload(payload: dict[str, Any]) -> bool:
    stage = _runtime_stage_key(payload)
    phase = _runtime_phase_key(payload)
    basis = _runtime_basis_key(payload)
    return (
        stage.startswith("export")
        or phase == "artifact"
        or basis == "artifact_manifest"
    )


def _is_return_runtime_payload(payload: dict[str, Any]) -> bool:
    stage = _runtime_stage_key(payload)
    basis = _runtime_basis_key(payload)
    return (
        stage == "completed"
        or basis == "completed"
    )


def _phase_status_for_row(
    row: JobRow,
    *,
    started_at: datetime | None,
    completed_at: datetime | None,
) -> str:
    if completed_at:
        return "completed"
    if started_at:
        if row.state == JobState.CANCELLED:
            return "cancelled"
        if row.state == JobState.FAILED:
            return "failed"
        return "active"
    return "pending"


def _phase_overlap_seconds(
    *,
    earlier_start: datetime | None,
    earlier_end: datetime | None,
    later_start: datetime | None,
    later_end: datetime | None,
    now: datetime,
) -> int:
    if not earlier_start or not later_start:
        return 0
    earlier_effective_end = earlier_end or now
    later_effective_end = later_end or now
    overlap_start = max(earlier_start, later_start)
    overlap_end = min(earlier_effective_end, later_effective_end)
    if overlap_end <= overlap_start:
        return 0
    return int((overlap_end - overlap_start).total_seconds())


def _timeline_for_row(row: JobRow) -> JobTimelineSummary:
    row = _resolved_job_row(row)
    now = datetime.now(timezone.utc)
    events = repo.list_job_events(row.job_id)
    runtime_events = [event for event in events if event.get("event_type") == "runtime_update"]

    def first_event_time(event_types: set[str]) -> datetime | None:
        for event in events:
            if event.get("event_type") in event_types:
                return _event_created_at(event)
        return None

    def last_event_time(event_types: set[str]) -> datetime | None:
        for event in reversed(events):
            if event.get("event_type") in event_types:
                return _event_created_at(event)
        return None

    def first_runtime_time(predicate) -> datetime | None:
        for event in runtime_events:
            payload = event.get("payload") or {}
            if isinstance(payload, dict) and predicate(payload):
                return _event_created_at(event)
        return None

    def last_runtime_event(predicate) -> dict[str, Any] | None:
        for event in reversed(runtime_events):
            payload = event.get("payload") or {}
            if isinstance(payload, dict) and predicate(payload):
                return event
        return None

    prepare_started = first_event_time(CLIENT_PREP_EVENT_TYPES)
    prepare_latest = last_event_time(CLIENT_PREP_EVENT_TYPES)
    prepare_completed = first_event_time({"mobile_prepare_completed", "mobile_prepare_finalized"})

    upload_started = first_event_time(UPLOAD_EVENT_TYPES) or row.created_at
    upload_latest = last_event_time(UPLOAD_EVENT_TYPES) or row.upload_completed_at or (
        row.updated_at if row.state == JobState.UPLOADING else None
    )
    upload_completed = row.upload_completed_at or first_event_time(
        {"upload_completed", "multipart_upload_completed", "chunked_upload_completed"}
    )

    preprocess_started = first_event_time({"preprocess_started"}) or first_runtime_time(_is_preprocess_runtime_payload)
    preprocess_latest_event = last_runtime_event(_is_preprocess_runtime_payload)
    preprocess_latest = _event_created_at(preprocess_latest_event) if preprocess_latest_event else None

    train_started = (
        first_event_time({"seed_train_started", "full_train_started"})
        or first_runtime_time(_is_train_runtime_payload)
    )
    train_latest_event = last_runtime_event(_is_train_runtime_payload)
    train_latest = _event_created_at(train_latest_event) if train_latest_event else None

    export_started = (
        first_event_time({"export_started", "artifact_stage_started", "artifact_manifest_uploaded"})
        or first_runtime_time(_is_export_runtime_payload)
    )
    export_latest = last_event_time({"export_started", "artifact_stage_started", "artifact_manifest_uploaded"})
    if export_latest is None:
        export_latest_event = last_runtime_event(_is_export_runtime_payload)
        export_latest = _event_created_at(export_latest_event) if export_latest_event else None
    export_completed = first_event_time({"artifact_manifest_uploaded"})
    if export_completed is None and row.state == JobState.COMPLETED and row.artifact:
        export_completed = row.completed_at

    return_started = (
        first_event_time(CLIENT_RETURN_EVENT_TYPES)
        or first_runtime_time(_is_return_runtime_payload)
    )
    return_latest = last_event_time(CLIENT_RETURN_EVENT_TYPES)
    if return_latest is None:
        return_latest_event = last_runtime_event(_is_return_runtime_payload)
        return_latest = _event_created_at(return_latest_event) if return_latest_event else None
    return_completed = first_event_time({"mobile_artifact_path_written", "mobile_viewer_opened"})

    preprocess_completed = None
    if row.state in {
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.COMPLETED,
        JobState.EXPORTING,
    }:
        preprocess_completed = preprocess_latest or row.updated_at
    elif (
        preprocess_latest
        and train_started
        and preprocess_latest <= train_started
    ):
        preprocess_completed = preprocess_latest
    elif (
        preprocess_latest
        and return_started
        and preprocess_latest <= return_started
    ):
        preprocess_completed = preprocess_latest

    train_completed = None
    if row.state == JobState.COMPLETED:
        train_completed = train_latest or row.completed_at
    elif row.state in {JobState.FAILED, JobState.CANCELLED, JobState.EXPORTING}:
        train_completed = train_latest or row.updated_at
    elif (
        train_latest
        and return_started
        and train_latest <= return_started
    ):
        train_completed = train_latest

    if export_latest is None and row.state in {JobState.EXPORTING, JobState.COMPLETED}:
        export_latest = row.updated_at
    if return_latest is None and row.state == JobState.COMPLETED and row.artifact:
        return_latest = row.completed_at

    if return_completed is None and row.state == JobState.COMPLETED and row.artifact:
        return_completed = row.completed_at

    preprocess_payload = (preprocess_latest_event or {}).get("payload") if preprocess_latest_event else {}
    train_payload = (train_latest_event or {}).get("payload") if train_latest_event else {}

    phases = [
        JobTimelinePhase(
            key="prepare",
            label=TIMELINE_PHASE_LABELS["prepare"],
            status=_phase_status_for_row(row, started_at=prepare_started, completed_at=prepare_completed),
            started_at=prepare_started,
            latest_activity_at=prepare_latest,
            completed_at=prepare_completed,
            detail="手机本地整理与重封装" if prepare_started else "等待手机开始本地整理埋点",
        ),
        JobTimelinePhase(
            key="upload",
            label=TIMELINE_PHASE_LABELS["upload"],
            status=_phase_status_for_row(row, started_at=upload_started, completed_at=upload_completed),
            started_at=upload_started,
            latest_activity_at=upload_latest,
            completed_at=upload_completed,
            progress_basis="upload_bytes",
            detail="手机上传到对象存储",
            metrics=_chunk_progress_metrics(row),
        ),
        JobTimelinePhase(
            key="preprocess",
            label=TIMELINE_PHASE_LABELS["preprocess"],
            status=_phase_status_for_row(row, started_at=preprocess_started, completed_at=preprocess_completed),
            started_at=preprocess_started,
            latest_activity_at=preprocess_latest,
            completed_at=preprocess_completed,
            progress_basis=str(preprocess_payload.get("progress_basis") or "") or None,
            detail=str(preprocess_payload.get("detail") or row.detail if row.stage and str(row.stage).startswith(("sfm", "prep")) else preprocess_payload.get("detail") or ""),
            metrics=_sanitize_runtime_metrics(preprocess_payload.get("metrics") or {}),
        ),
        JobTimelinePhase(
            key="train",
            label=TIMELINE_PHASE_LABELS["train"],
            status=_phase_status_for_row(row, started_at=train_started, completed_at=train_completed),
            started_at=train_started,
            latest_activity_at=train_latest,
            completed_at=train_completed,
            progress_basis=str(train_payload.get("progress_basis") or "") or None,
            detail=str(train_payload.get("detail") or row.detail if row.stage == "train" else train_payload.get("detail") or ""),
            metrics=_sanitize_runtime_metrics(train_payload.get("metrics") or {}),
        ),
        JobTimelinePhase(
            key="export",
            label=TIMELINE_PHASE_LABELS["export"],
            status=_phase_status_for_row(row, started_at=export_started, completed_at=export_completed),
            started_at=export_started,
            latest_activity_at=export_latest,
            completed_at=export_completed,
            progress_basis="artifact_manifest" if export_started else None,
            detail=(
                "结果正在导出与整理"
                if export_started and not export_completed
                else "等待结果导出"
            ),
        ),
        JobTimelinePhase(
            key="return",
            label=TIMELINE_PHASE_LABELS["return"],
            status=_phase_status_for_row(row, started_at=return_started, completed_at=return_completed),
            started_at=return_started,
            latest_activity_at=return_latest,
            completed_at=return_completed,
            progress_basis="artifact_manifest" if return_started else None,
            detail=(
                "结果已开始回传到手机"
                if return_started and not return_completed
                else "等待结果回到手机"
            ),
        ),
    ]

    phase_index = {phase.key: phase for phase in phases}
    overlaps: list[JobTimelineOverlap] = []
    for earlier_key, later_key in [
        ("prepare", "upload"),
        ("upload", "preprocess"),
        ("preprocess", "train"),
        ("train", "export"),
        ("export", "return"),
    ]:
        earlier = phase_index[earlier_key]
        later = phase_index[later_key]
        overlap_seconds = _phase_overlap_seconds(
            earlier_start=earlier.started_at,
            earlier_end=earlier.completed_at,
            later_start=later.started_at,
            later_end=later.completed_at,
            now=now,
        )
        overlaps.append(
            JobTimelineOverlap(
                earlier_key=earlier_key,
                later_key=later_key,
                overlap_seconds=overlap_seconds,
            )
        )

    return JobTimelineSummary(generated_at=now, phases=phases, overlaps=overlaps)


def _mobile_upload_proxy_url(job_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/upload"


def _mobile_upload_target(target) -> dict[str, Any]:
    return {
        "kind": "single",
        "method": target.method,
        "url": target.url,
        "headers": target.headers,
        "storageKey": target.storage_key,
    }


def _upload_spec_target(target) -> dict[str, Any]:
    return {
        "kind": "single",
        "method": target.method,
        "url": target.url,
        "headers": target.headers,
        "storage_key": target.storage_key,
    }


def _mobile_multipart_upload_target(job_id: str, target) -> dict[str, Any]:
    return {
        "kind": "multipart",
        "headers": {},
        "storageKey": target.storage_key,
        "uploadId": target.upload_id,
        "partSizeBytes": target.part_size_bytes,
        "maxConcurrency": target.max_concurrency,
        "partReadyURL": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/multipart-part-ready",
        "parts": [
            {
                "partNumber": part.part_number,
                "method": part.method,
                "url": part.url,
                "headers": part.headers,
            }
            for part in target.parts
        ],
        "completeURL": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/multipart-complete",
        "abortURL": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/multipart-abort",
    }


def _mobile_chunked_upload_target(job_id: str, target) -> dict[str, Any]:
    return {
        "kind": "chunked",
        "headers": {},
        "storageKey": target.storage_key,
        "uploadId": target.upload_id,
        "partSizeBytes": target.part_size_bytes,
        "maxConcurrency": target.max_concurrency,
        "partReadyURL": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/chunk-part-ready",
        "parts": [
            {
                "partNumber": part.part_number,
                "method": part.method,
                "url": part.url,
                "headers": part.headers,
            }
            for part in target.parts
        ],
        "completeURL": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/chunked-complete",
        "abortURL": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/chunked-abort",
    }


def _multipart_upload_spec_target(job_id: str, target) -> dict[str, Any]:
    return {
        "kind": "multipart",
        "headers": {},
        "storage_key": target.storage_key,
        "upload_id": target.upload_id,
        "part_size_bytes": target.part_size_bytes,
        "max_concurrency": target.max_concurrency,
        "part_ready_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/multipart-part-ready",
        "parts": [
            {
                "part_number": part.part_number,
                "method": part.method,
                "url": part.url,
                "headers": part.headers,
            }
            for part in target.parts
        ],
        "complete_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/multipart-complete",
        "abort_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/multipart-abort",
    }


def _chunked_upload_spec_target(job_id: str, target) -> dict[str, Any]:
    return {
        "kind": "chunked",
        "headers": {},
        "storage_key": target.storage_key,
        "upload_id": target.upload_id,
        "part_size_bytes": target.part_size_bytes,
        "max_concurrency": target.max_concurrency,
        "part_ready_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/chunk-part-ready",
        "parts": [
            {
                "part_number": part.part_number,
                "method": part.method,
                "url": part.url,
                "headers": part.headers,
            }
            for part in target.parts
        ],
        "complete_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/chunked-complete",
        "abort_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{job_id}/chunked-abort",
    }


def _should_use_multipart_upload(file_size_bytes: int) -> bool:
    if file_size_bytes < settings.object_storage_multipart_threshold_bytes:
        return False
    return callable(getattr(storage, "create_multipart_upload_target", None))


def _should_use_chunked_ingest(file_size_bytes: int, *, mobile: bool) -> bool:
    if not mobile:
        return False
    if not settings.object_storage_chunked_ingest_enabled:
        return False
    if file_size_bytes < settings.object_storage_chunked_ingest_threshold_bytes:
        return False
    return callable(getattr(storage, "create_chunked_upload_target", None))


def _artifact_prefix_for_job(job_id: str) -> str:
    return f"{settings.artifact_bucket_prefix.rstrip('/')}/{job_id}/"


def _cleanup_terminal_job_storage(row: JobRow, *, remove_artifacts: bool) -> dict[str, bool]:
    result = {
        "input_deleted": True,
        "artifacts_deleted": True,
    }

    if row.input_storage_key:
        try:
            storage.delete_object(row.input_storage_key)
        except Exception:
            logger.exception("failed to delete input object for job %s", row.job_id)
            result["input_deleted"] = False
        try:
            storage.delete_prefix(f"{row.input_storage_key}.chunks/")
        except Exception:
            logger.exception("failed to delete chunk prefix for job %s", row.job_id)
            result["input_deleted"] = False

    if remove_artifacts:
        try:
            storage.delete_prefix(_artifact_prefix_for_job(row.job_id))
        except Exception:
            logger.exception("failed to delete artifact prefix for job %s", row.job_id)
            result["artifacts_deleted"] = False

    return result


def _has_live_worker_claim(row: JobRow) -> bool:
    if not row.assigned_worker_id:
        return False
    worker = repo.get_worker(row.assigned_worker_id)
    if worker is None or worker.current_job_id != row.job_id:
        return False
    heartbeat_age = (datetime.now(timezone.utc) - worker.last_heartbeat_at).total_seconds()
    return heartbeat_age <= max(20, settings.heartbeat_interval_sec * 3)


def _processing_orphan_timeout_sec() -> int:
    return max(settings.worker_lease_ttl_sec, settings.heartbeat_interval_sec * 8, 180)


def _maybe_fail_orphaned_processing_job(row: JobRow) -> JobRow:
    active_processing_states = {
        JobState.ASSIGNED,
        JobState.RECONSTRUCTING,
        JobState.TRAINING_PROBE,
        JobState.TRAINING_FULL,
        JobState.EXPORTING,
    }
    if row.state not in active_processing_states or not row.assigned_worker_id:
        return row

    now = datetime.now(timezone.utc)
    stale_after_sec = _processing_orphan_timeout_sec()
    row_age_sec = (now - row.updated_at).total_seconds()
    if row_age_sec <= stale_after_sec:
        return row

    worker = repo.get_worker(row.assigned_worker_id)
    worker_heartbeat_age_sec: float | None = None
    worker_current_job_id: str | None = None
    if worker is not None:
        worker_current_job_id = worker.current_job_id
        worker_heartbeat_age_sec = (now - worker.last_heartbeat_at).total_seconds()
        if worker.current_job_id == row.job_id and worker_heartbeat_age_sec <= max(20, settings.heartbeat_interval_sec * 3):
            return row

    if worker is None:
        detail = "控制平面发现承接这条任务的 worker 记录已经不存在，而且这条任务长时间没有新进展，系统已停止这次任务。"
        failure_reason = "worker_orphaned_assignment"
    elif worker.current_job_id and worker.current_job_id != row.job_id:
        detail = "控制平面发现这条任务仍挂在旧 worker 上，但那台 worker 当前已经不再承接这条 job，系统已停止这次任务。"
        failure_reason = "worker_orphaned_assignment"
    elif worker.current_job_id is None:
        detail = "控制平面发现承接这条任务的 worker 已经空闲，但这条任务长时间没有新进展，系统已停止这次任务。"
        failure_reason = "worker_orphaned_assignment"
    else:
        detail = "控制平面发现承接这条任务的 worker 心跳已经过期，而且这条任务长时间没有新进展，系统已停止这次任务。"
        failure_reason = "worker_stalled_or_runtime_stale"

    original_assigned_worker_id = row.assigned_worker_id
    row = repo.update_job(
        row.job_id,
        state=JobState.FAILED,
        title="远端生成失败",
        detail=detail,
        progress_basis="worker_orphaned_assignment",
        failure_reason=failure_reason,
        failure_detail=detail,
        assigned_worker_id=None,
        estimated_remaining_seconds=0,
        completed_at=now,
    )
    repo.append_job_event(
        row.job_id,
        event_type="system_reconciled",
        payload={
            "reason": failure_reason,
            "detail": detail,
            "stale_after_sec": stale_after_sec,
            "row_age_sec": int(row_age_sec),
            "assigned_worker_id": original_assigned_worker_id,
            "worker_current_job_id": worker_current_job_id,
            "worker_heartbeat_age_sec": int(worker_heartbeat_age_sec) if worker_heartbeat_age_sec is not None else None,
            "reported_at": now.isoformat(),
        },
    )
    return row


def _mark_upload_aborted(row: JobRow, *, failure_reason: str, detail: str) -> JobRow:
    worker_active = _has_live_worker_claim(row)
    cleanup = {"input_deleted": False, "artifacts_deleted": False}
    now = datetime.now(timezone.utc)

    if not worker_active:
        cleanup = _cleanup_terminal_job_storage(row, remove_artifacts=True)

    return repo.update_job(
        row.job_id,
        state=JobState.CANCELLED,
        title="上传已中断，正在停止远端任务" if worker_active else "上传已中断",
        detail=detail,
        stage="cancel_requested" if worker_active else "upload_aborted",
        phase_name="upload_aborted",
        progress_basis="upload_aborted",
        failure_reason=failure_reason,
        failure_detail=detail,
        assigned_worker_id=row.assigned_worker_id if worker_active else None,
        cancelled_at=now,
        estimated_remaining_seconds=0,
        completed_at=row.completed_at if worker_active else now,
        input_storage_key=None if not worker_active and cleanup["input_deleted"] else row.input_storage_key,
        input_upload_etag=None if not worker_active and cleanup["input_deleted"] else row.input_upload_etag,
        artifact=None if not worker_active and cleanup["artifacts_deleted"] else row.artifact,
    )


def _create_upload_contract(row: JobRow, *, mobile: bool) -> tuple[dict[str, Any], str]:
    if _should_use_chunked_ingest(row.input_size_bytes, mobile=mobile):
        target = storage.create_chunked_upload_target(
            tenant_id=row.tenant_id,
            job_id=row.job_id,
            file_name=row.input_file_name,
            content_type=row.input_content_type,
            file_size_bytes=row.input_size_bytes,
        )
        payload = (
            _mobile_chunked_upload_target(row.job_id, target)
            if mobile
            else _chunked_upload_spec_target(row.job_id, target)
        )
        return payload, target.storage_key

    if _should_use_multipart_upload(row.input_size_bytes):
        try:
            target = storage.create_multipart_upload_target(
                tenant_id=row.tenant_id,
                job_id=row.job_id,
                file_name=row.input_file_name,
                content_type=row.input_content_type,
                file_size_bytes=row.input_size_bytes,
            )
            payload = (
                _mobile_multipart_upload_target(row.job_id, target)
                if mobile
                else _multipart_upload_spec_target(row.job_id, target)
            )
            return payload, target.storage_key
        except Exception:
            pass

    target = storage.create_upload_target(
        tenant_id=row.tenant_id,
        job_id=row.job_id,
        file_name=row.input_file_name,
        content_type=row.input_content_type,
    )
    payload = _mobile_upload_target(target) if mobile else _upload_spec_target(target)
    return payload, target.storage_key


def _reserve_idle_worker_for_upload(job_id: str) -> str | None:
    try:
        worker = repo.reserve_idle_worker_for_job(job_id)
    except Exception:
        return None
    if not worker:
        return None
    return worker.worker_id


def _release_stale_worker_reservation(row: JobRow) -> JobRow:
    if not row.assigned_worker_id:
        return row
    worker = repo.get_worker(row.assigned_worker_id)
    if worker is None:
        return repo.update_job(row.job_id, assigned_worker_id=None)
    heartbeat_age = (datetime.now(timezone.utc) - worker.last_heartbeat_at).total_seconds()
    if heartbeat_age > settings.worker_reservation_stale_sec:
        return repo.update_job(row.job_id, assigned_worker_id=None)
    if worker.current_job_id and worker.current_job_id != row.job_id:
        return repo.update_job(row.job_id, assigned_worker_id=None)
    return row


def _queue_ready_detail(row: JobRow) -> str:
    if row.assigned_worker_id:
        return "视频已经上传完成，预留 GPU worker 正在立刻开始处理。"
    return "视频已经到达对象存储，正在等待可用 GPU。"


def _upload_progress_fraction(uploaded_bytes: int, total_bytes: int) -> float:
    if total_bytes <= 0:
        return 0.0
    raw_fraction = min(max(float(uploaded_bytes) / float(total_bytes), 0.0), 1.0)
    return min(0.14, raw_fraction * 0.14)


def _uploading_detail(
    row: JobRow,
    *,
    uploaded_bytes: int | None = None,
    completed_parts: int | None = None,
    total_parts: int | None = None,
    stream_signal_seen: bool = False,
) -> str:
    detail = "手机正在把视频上传到对象存储。"
    if completed_parts and total_parts:
        detail = f"手机正在把视频上传到对象存储。已完成 {completed_parts}/{total_parts} 个分片。"
    if uploaded_bytes and row.input_size_bytes > 0:
        percent = int(min(100, round((uploaded_bytes / max(row.input_size_bytes, 1)) * 100)))
        detail = f"{detail.rstrip('。')} 已上传约 {percent}%。"
    if stream_signal_seen:
        detail = f"{detail.rstrip('。')} 控制平面已收到分片到达信号，可用于后续流式 ingest。"
    if row.assigned_worker_id:
        detail = f"{detail.rstrip('。')} 可用 GPU worker 已预留，上传完成后会立刻开始。"
    return detail


def _chunk_init_event(row: JobRow) -> dict[str, Any] | None:
    events = repo.list_job_events(row.job_id, event_type="chunked_upload_initialized")
    if not events:
        return None
    payload = events[-1].get("payload") or {}
    return payload if isinstance(payload, dict) else None


def _chunk_complete_event(row: JobRow) -> dict[str, Any] | None:
    events = repo.list_job_events(row.job_id, event_type="chunked_upload_completed")
    if not events:
        return None
    payload = events[-1].get("payload") or {}
    return payload if isinstance(payload, dict) else None


def _chunk_manifest_state(row: JobRow) -> dict[str, Any] | None:
    init_payload = _chunk_init_event(row)
    if not init_payload:
        return None

    try:
        chunk_size_bytes = int(init_payload.get("part_size_bytes") or settings.object_storage_chunked_ingest_chunk_size_bytes)
        total_chunks = int(init_payload.get("total_chunks") or 0)
    except (TypeError, ValueError):
        chunk_size_bytes = settings.object_storage_chunked_ingest_chunk_size_bytes
        total_chunks = max(1, (row.input_size_bytes + chunk_size_bytes - 1) // chunk_size_bytes)

    ready_events = repo.list_job_events(row.job_id, event_type="chunk_part_ready")
    ready_parts: dict[int, dict[str, Any]] = {}
    visible_bytes = 0
    for event in ready_events:
        payload = event.get("payload") or {}
        try:
            part_number = int(payload.get("part_number") or 0)
        except (TypeError, ValueError):
            continue
        if part_number <= 0:
            continue
        ready_parts[part_number] = payload
        try:
            visible_bytes = max(visible_bytes, int(payload.get("uploaded_bytes") or 0))
        except (TypeError, ValueError):
            pass

    completed_payload = _chunk_complete_event(row) or {}
    completed_parts = completed_payload.get("completed_parts") or []
    for item in completed_parts:
        if not isinstance(item, dict):
            continue
        try:
            part_number = int(item.get("partNumber") or item.get("part_number") or 0)
        except (TypeError, ValueError):
            continue
        if part_number <= 0:
            continue
        ready_parts.setdefault(part_number, {})
        etag = item.get("etag")
        if etag and not ready_parts[part_number].get("etag"):
            ready_parts[part_number]["etag"] = etag

    upload_completed = bool(completed_payload) or row.input_upload_etag == "chunked_complete"
    if upload_completed:
        completed_size = completed_payload.get("size_bytes")
        try:
            completed_size_int = int(completed_size or row.input_size_bytes or 0)
        except (TypeError, ValueError):
            completed_size_int = int(row.input_size_bytes or 0)
        visible_bytes = max(visible_bytes, completed_size_int)
        completed_part_count = len(ready_parts)
        if completed_part_count > 0:
            total_chunks = completed_part_count
        elif completed_size_int > 0:
            total_chunks = max(1, (completed_size_int + chunk_size_bytes - 1) // chunk_size_bytes)

    chunks: list[dict[str, Any]] = []
    for part_number in sorted(ready_parts):
        chunk_key = chunk_storage_key(row.input_storage_key or "", part_number)
        part_offset = (part_number - 1) * chunk_size_bytes
        remaining = max((row.input_size_bytes or 0) - part_offset, 0)
        size_bytes = min(chunk_size_bytes, remaining) if remaining else chunk_size_bytes
        payload = ready_parts[part_number]
        chunks.append(
            {
                "chunk_number": part_number,
                "storage_key": chunk_key,
                "download_url": storage.build_download_url(chunk_key),
                "size_bytes": size_bytes,
                "etag": payload.get("etag"),
            }
        )

    return {
        "chunk_size_bytes": chunk_size_bytes,
        "total_chunks": total_chunks,
        "visible_bytes": visible_bytes,
        "upload_completed": upload_completed,
        "chunks": chunks,
    }


def _chunk_progress_metrics(row: JobRow) -> dict[str, Any]:
    init_payload = _chunk_init_event(row)
    if not init_payload:
        return {}

    total_chunks = int(init_payload.get("total_chunks") or 0)
    visible_bytes = 0
    completed_part_count = 0
    visible_chunk_numbers: set[int] = set()

    for event in repo.list_job_events(row.job_id, event_type="chunk_part_ready"):
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        try:
            visible_bytes = max(visible_bytes, int(payload.get("uploaded_bytes") or 0))
        except (TypeError, ValueError):
            pass
        try:
            completed_part_count = max(completed_part_count, int(payload.get("completed_part_count") or 0))
        except (TypeError, ValueError):
            pass
        try:
            part_number = int(payload.get("part_number") or 0)
        except (TypeError, ValueError):
            part_number = 0
        if part_number > 0:
            visible_chunk_numbers.add(part_number)

    completed_payload = _chunk_complete_event(row) or {}
    completed_parts = completed_payload.get("completed_parts") or []
    for item in completed_parts:
        if not isinstance(item, dict):
            continue
        try:
            part_number = int(item.get("partNumber") or item.get("part_number") or 0)
        except (TypeError, ValueError):
            continue
        if part_number > 0:
            visible_chunk_numbers.add(part_number)

    upload_completed = bool(completed_payload) or row.input_upload_etag == "chunked_complete"
    if upload_completed:
        completed_part_count = max(completed_part_count, len(visible_chunk_numbers))
        try:
            visible_bytes = max(visible_bytes, int(completed_payload.get("size_bytes") or row.input_size_bytes or 0))
        except (TypeError, ValueError):
            visible_bytes = max(visible_bytes, int(row.input_size_bytes or 0))
        if completed_part_count > 0:
            total_chunks = completed_part_count

    return {
        "upload_completed": upload_completed,
        "visible_bytes": visible_bytes,
        "visible_chunk_count": len(visible_chunk_numbers),
        "completed_part_count": completed_part_count,
        "total_chunks": total_chunks,
    }


def _sanitize_runtime_metrics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        normalized_key = key.strip()
        if not normalized_key:
            continue
        if isinstance(value, bool):
            sanitized[normalized_key] = value
        elif isinstance(value, (int, float, str)):
            sanitized[normalized_key] = value
    return sanitized


def _runtime_metrics_for_row(row: JobRow) -> dict[str, Any]:
    metrics: dict[str, Any] = {}

    chunk_metrics = _chunk_progress_metrics(row)
    if chunk_metrics:
        metrics.update(chunk_metrics)

    runtime_events = repo.list_job_events(row.job_id, event_type="runtime_update")
    row_stage = str(row.stage or "").strip().lower()
    row_phase = str(row.phase_name or "").strip().lower()
    row_basis = str(row.progress_basis or "").strip().lower()
    fallback_metrics: dict[str, Any] = {}

    for event in reversed(runtime_events):
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        payload_metrics = _sanitize_runtime_metrics(payload.get("metrics"))
        if not payload_metrics:
            continue

        payload_stage = str(payload.get("stage") or "").strip().lower()
        payload_phase = str(payload.get("phase_name") or "").strip().lower()
        payload_basis = str(payload.get("progress_basis") or "").strip().lower()

        if not fallback_metrics:
            fallback_metrics = payload_metrics

        if row_basis and payload_basis == row_basis:
            metrics.update(payload_metrics)
            return metrics
        if row_stage and payload_stage == row_stage and row_phase and payload_phase == row_phase:
            metrics.update(payload_metrics)
            return metrics
        if row_stage and payload_stage == row_stage and not row_phase:
            metrics.update(payload_metrics)
            return metrics

    if fallback_metrics:
        metrics.update(fallback_metrics)
    return metrics


def _status_from_row(row: JobRow) -> JobStatusResponse:
    row = _resolved_job_row(row)
    return JobStatusResponse(
        job_id=row.job_id,
        state=row.state,
        stage=row.stage,
        phase_name=row.phase_name,
        current_tier=row.current_tier,
        title=row.title,
        detail=row.detail,
        progress_fraction=row.progress_fraction,
        progress_basis=row.progress_basis,
        elapsed_seconds=row.elapsed_seconds,
        estimated_remaining_seconds=row.estimated_remaining_seconds,
        failure_reason=row.failure_reason,
        failure_detail=row.failure_detail,
        upload_completed=row.upload_completed_at is not None,
        metrics=_runtime_metrics_for_row(row),
        artifact=row.artifact,
        timeline=_timeline_for_row(row),
        assigned_worker_id=row.assigned_worker_id,
        updated_at=row.updated_at,
    )


def _mobile_state_for(row: JobRow) -> str:
    if row.state in {JobState.CREATED, JobState.UPLOADING, JobState.UPLOADED}:
        return "uploading"
    if row.state == JobState.QUEUED:
        return "queued"
    if row.state == JobState.ASSIGNED:
        return "reconstructing"
    if row.state == JobState.RECONSTRUCTING:
        return "reconstructing"
    if row.state in {JobState.TRAINING_PROBE, JobState.TRAINING_FULL}:
        return "training"
    if row.state == JobState.EXPORTING:
        return "packaging"
    if row.state == JobState.COMPLETED:
        return "completed"
    if row.state == JobState.FAILED:
        return "failed"
    if row.state == JobState.CANCELLED:
        return "cancelled"
    return row.state.value


def _artifact_format_for(row: JobRow) -> str | None:
    artifact = row.artifact
    if not artifact or not artifact.primary_artifact:
        return None
    key = (artifact.primary_artifact.storage_key or "").lower()
    if key.endswith(".spz"):
        return "spz"
    if key.endswith(".splat"):
        return "splat"
    return "ply"


def _mobile_artifact_payload(row: JobRow) -> dict[str, Any] | None:
    artifact = row.artifact
    if not artifact or not artifact.primary_artifact or not artifact.primary_artifact.storage_key:
        return None
    return {
        "download_url": f"{settings.public_base_url.rstrip('/')}/v1/mobile-jobs/{row.job_id}/artifact-download",
        "format": _artifact_format_for(row) or "ply",
    }


def _stream_object_response(storage_key: str) -> StreamingResponse:
    try:
        body, content_type, content_length = storage.open_download_stream(storage_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="artifact_not_found")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"artifact_stream_failed:{exc}") from exc

    filename = Path(storage_key).name
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)

    def iterator():
        try:
            iter_chunks = getattr(body, "iter_chunks", None)
            if callable(iter_chunks):
                for chunk in iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        yield chunk
                return
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    return StreamingResponse(iterator(), media_type=content_type or "application/octet-stream", headers=headers)


def _maybe_promote_uploaded(row: JobRow) -> JobRow:
    if row.state != JobState.UPLOADING or not row.input_storage_key:
        return row

    chunk_state = _chunk_manifest_state(row)
    if chunk_state is not None:
        if chunk_state["upload_completed"]:
            return repo.update_job(
                row.job_id,
                input_upload_etag=row.input_upload_etag or "chunked_complete",
                state=JobState.QUEUED,
                title="视频已上传",
                detail=_queue_ready_detail(row),
                progress_fraction=max(row.progress_fraction or 0.0, 0.15),
                progress_basis="chunked_upload_complete",
                upload_completed_at=row.upload_completed_at or datetime.now(timezone.utc),
            )
        return row

    now = datetime.now(timezone.utc)
    row = _release_stale_worker_reservation(row)
    exists, object_size = storage.probe_object(row.input_storage_key)
    if not exists:
        multipart_expected = (row.input_size_bytes or 0) >= settings.object_storage_multipart_threshold_bytes
        upload_age_seconds = (now - row.updated_at).total_seconds()
        if multipart_expected:
            active_upload_id = storage.get_active_multipart_upload_id(row.input_storage_key)
            if active_upload_id:
                expected_part_count = max(
                    1,
                    ((row.input_size_bytes or 0) + settings.object_storage_multipart_part_size_bytes - 1)
                    // settings.object_storage_multipart_part_size_bytes,
                )
                uploaded_parts = storage.list_uploaded_multipart_parts(
                    storage_key=row.input_storage_key,
                    upload_id=active_upload_id,
                )
                if len(uploaded_parts) >= expected_part_count:
                    try:
                        etag = storage.complete_multipart_upload(
                            storage_key=row.input_storage_key,
                            upload_id=active_upload_id,
                            parts=uploaded_parts,
                        )
                        exists_after_complete, object_size_after_complete = storage.probe_object(row.input_storage_key)
                        if exists_after_complete:
                            return repo.update_job(
                                row.job_id,
                                input_upload_etag=etag,
                                input_size_bytes=object_size_after_complete or row.input_size_bytes,
                                state=JobState.QUEUED,
                                title="视频已上传",
                                detail=_queue_ready_detail(row),
                                progress_fraction=max(row.progress_fraction or 0.0, 0.15),
                                progress_basis="multipart_auto_completed",
                                upload_completed_at=row.upload_completed_at or now,
                            )
                    except Exception as error:
                        exists_after_error, object_size_after_error = storage.probe_object(row.input_storage_key)
                        if exists_after_error:
                            return repo.update_job(
                                row.job_id,
                                input_upload_etag=row.input_upload_etag or "multipart_already_completed",
                                input_size_bytes=object_size_after_error or row.input_size_bytes,
                                state=JobState.QUEUED,
                                title="视频已上传",
                                detail=_queue_ready_detail(row),
                                progress_fraction=max(row.progress_fraction or 0.0, 0.15),
                                progress_basis="multipart_auto_completed_after_probe",
                                upload_completed_at=row.upload_completed_at or now,
                            )
                        return repo.update_job(
                            row.job_id,
                            state=JobState.FAILED,
                            title="上传确认失败",
                            detail="所有分片都已上传，但服务器确认合并时失败了。请重新发送。",
                            failure_reason="multipart_complete_failed",
                            failure_detail=str(error),
                            estimated_remaining_seconds=0,
                            completed_at=now,
                        )
                return row
        if multipart_expected and upload_age_seconds >= settings.object_storage_multipart_complete_grace_sec:
            return repo.update_job(
                row.job_id,
                state=JobState.FAILED,
                title="上传没有完成",
                detail="所有分片都已发送，但服务器在确认最终合并时超时。请重新发送。",
                failure_reason="multipart_upload_incomplete",
                failure_detail="multipart upload no longer active before final completion confirmation",
                estimated_remaining_seconds=0,
                completed_at=now,
            )
        return row
    return repo.update_job(
        row.job_id,
        state=JobState.QUEUED,
        title="视频已上传",
        detail=_queue_ready_detail(row),
        progress_fraction=max(row.progress_fraction or 0.0, 0.15),
        progress_basis="object_storage_visible",
        input_size_bytes=object_size or row.input_size_bytes,
        upload_completed_at=row.upload_completed_at or now,
    )


def _terminal_runtime_failure_reason(row: JobRow) -> str | None:
    if row.failure_reason:
        return row.failure_reason
    basis = (row.progress_basis or "").strip().lower()
    if basis == "all_tiers_failed":
        return "all_tiers_failed"
    if basis != "runtime_failure":
        return None
    detail = row.detail or ""
    if "所有回退方案" in detail or "all tiers failed" in detail.lower():
        return "all_tiers_failed"
    return "runtime_failure"


def _maybe_finalize_terminal_runtime(row: JobRow) -> JobRow:
    if row.state in {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}:
        return row

    reason = _terminal_runtime_failure_reason(row)
    if not reason:
        return row

    if row.estimated_remaining_seconds not in (0, None):
        return row

    return repo.update_job(
        row.job_id,
        state=JobState.FAILED,
        title="远端生成失败",
        detail=row.detail or "远端任务已经结束，但没有生成可用结果。",
        failure_reason=reason,
        failure_detail=row.failure_detail or row.detail or reason,
        completed_at=row.completed_at or datetime.now(timezone.utc),
    )


def _resolved_job_row(row: JobRow) -> JobRow:
    row = _maybe_promote_uploaded(row)
    row = _maybe_fail_orphaned_processing_job(row)
    row = _maybe_finalize_terminal_runtime(row)
    return row


def _coerce_runtime_failure(request: RuntimeUpdateRequest) -> tuple[JobState, str | None, str | None]:
    if request.state == JobState.FAILED:
        return (
            JobState.FAILED,
            _terminal_runtime_failure_reason(
                JobRow(
                    job_id="",
                    tenant_id="",
                    user_id=None,
                    client_record_id=None,
                    capture_origin="",
                    input_file_name="",
                    input_content_type="",
                    input_size_bytes=0,
                    input_storage_key=None,
                    input_upload_etag=None,
                    state=JobState.FAILED,
                    stage=request.stage,
                    phase_name=request.phase_name,
                    current_tier=request.current_tier,
                    title=request.title,
                    detail=request.detail,
                    progress_fraction=request.progress_fraction,
                    progress_basis=request.progress_basis,
                    elapsed_seconds=request.elapsed_seconds,
                    estimated_remaining_seconds=request.estimated_remaining_seconds,
                    assigned_worker_id=request.worker_id,
                    artifact=None,
                    failure_reason=None,
                    failure_detail=None,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            or "runtime_failure",
            request.detail,
        )

    basis = (request.progress_basis or "").strip().lower()
    if basis not in {"runtime_failure", "all_tiers_failed"}:
        return request.state, None, None

    detail = request.detail or ""
    if basis == "all_tiers_failed" or "所有回退方案" in detail or "all tiers failed" in detail.lower():
        reason = "all_tiers_failed"
    else:
        reason = "runtime_failure"
    return JobState.FAILED, reason, request.detail


def _record_runtime_timeline_milestones(job_id: str, request: RuntimeUpdateRequest) -> None:
    payload = {
        "state": request.state.value,
        "stage": request.stage,
        "phase_name": request.phase_name,
        "progress_basis": request.progress_basis,
        "reported_at": datetime.now(timezone.utc).isoformat(),
    }
    if _is_preprocess_runtime_payload(payload):
        _append_once_job_event(job_id, event_type="preprocess_started", payload=payload)
    if _is_train_runtime_payload(payload):
        phase_key = str(request.phase_name or "").strip().lower()
        upload_completed_metric = bool((request.metrics or {}).get("upload_completed"))
        if phase_key.startswith("seed"):
            # Defensive guard: conservative mode should never surface pre-upload seed training
            # as a real train milestone, even if a stale worker-side status file leaked through.
            if upload_completed_metric:
                _append_once_job_event(job_id, event_type="seed_train_started", payload=payload)
        else:
            _append_once_job_event(job_id, event_type="full_train_started", payload=payload)
    if _is_export_runtime_payload(payload):
        _append_once_job_event(job_id, event_type="export_started", payload=payload)
    if _is_return_runtime_payload(payload):
        _append_once_job_event(job_id, event_type="artifact_stage_started", payload=payload)


def _mobile_status_from_row(row: JobRow) -> dict[str, Any]:
    row = _resolved_job_row(row)
    cancel_acknowledged = row.state == JobState.CANCELLED and not row.assigned_worker_id
    return {
        "job_id": row.job_id,
        "state": _mobile_state_for(row),
        "stage": row.stage,
        "phase_name": row.phase_name,
        "current_tier": row.current_tier,
        "title": row.title,
        "detail": row.detail,
        "progress_fraction": row.progress_fraction,
        "elapsed_seconds": row.elapsed_seconds,
        "estimated_remaining_seconds": row.estimated_remaining_seconds,
        "progress_basis": row.progress_basis,
        "metrics": _runtime_metrics_for_row(row),
        "artifact": _mobile_artifact_payload(row),
        "timeline": _timeline_for_row(row).model_dump(mode="json"),
        "failure_reason": row.failure_reason if row.state in {JobState.FAILED, JobState.CANCELLED} else None,
        "assigned_worker_id": row.assigned_worker_id,
        "cancel_acknowledged": cancel_acknowledged,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": settings.service_name,
        "environment": settings.environment,
        "api_ingress_mode": settings.api_ingress_mode,
        "repository": type(repo).__name__,
        "storage": getattr(storage, "provider_name", type(storage).__name__),
    }


@app.post("/v1/jobs", response_model=CreateJobResponse)
def create_job(request: CreateJobRequest) -> CreateJobResponse:
    requested_pipeline_profile = _requested_pipeline_profile_payload(request.pipeline_profile)
    row = repo.create_job(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        client_record_id=request.client_record_id,
        capture_origin=request.capture_origin,
        file_name=request.file_name,
        content_type=request.content_type,
        size_bytes=request.file_size_bytes,
    )
    repo.append_job_event(
        row.job_id,
        event_type="pipeline_profile_requested",
        payload=requested_pipeline_profile,
    )
    return CreateJobResponse(
        job_id=row.job_id,
        state=row.state,
        upload_init_path=f"/v1/jobs/{row.job_id}/upload-init",
        poll_path=f"/v1/jobs/{row.job_id}",
        cancel_path=f"/v1/jobs/{row.job_id}",
    )


@app.post("/v1/mobile-jobs")
def create_mobile_job(payload: dict[str, Any]) -> dict[str, Any]:
    file_name = payload.get("fileName") or payload.get("file_name")
    file_size_bytes = payload.get("fileSizeBytes") or payload.get("file_size_bytes")
    content_type = payload.get("contentType") or payload.get("content_type")
    capture_origin = payload.get("captureOrigin") or payload.get("capture_origin")
    client_record_id = payload.get("clientRecordId") or payload.get("client_record_id")
    user_id = payload.get("userId") or payload.get("user_id")
    tenant_id = payload.get("tenantId") or payload.get("tenant_id") or "tenant_demo"
    requested_pipeline_profile = _requested_pipeline_profile_payload(
        payload.get("pipelineProfile") or payload.get("pipeline_profile")
    )

    if not file_name or file_size_bytes is None or not content_type or not capture_origin:
        raise HTTPException(status_code=400, detail="invalid_mobile_job_request")

    row = repo.create_job(
        tenant_id=tenant_id,
        user_id=user_id,
        client_record_id=client_record_id,
        capture_origin=capture_origin,
        file_name=file_name,
        content_type=content_type,
        size_bytes=int(file_size_bytes),
    )
    repo.append_job_event(
        row.job_id,
        event_type="pipeline_profile_requested",
        payload=requested_pipeline_profile,
    )
    repo.cancel_superseded_jobs(
        tenant_id=tenant_id,
        user_id=user_id,
        client_record_id=client_record_id,
        capture_origin=capture_origin,
        file_name=file_name,
        content_type=content_type,
        size_bytes=int(file_size_bytes),
        keep_job_id=row.job_id,
        failure_reason="superseded_by_newer_upload",
        detail="同一素材的新上传已经创建，旧任务已自动取消，避免继续占用队列或 worker。",
    )
    upload_contract, storage_key = _create_upload_contract(row, mobile=True)
    reserved_worker_id = _reserve_idle_worker_for_upload(row.job_id)
    upload_kind = str(upload_contract.get("kind") or "single")
    _append_once_job_event(
        row.job_id,
        event_type="upload_window_opened",
        payload={
            "capture_origin": capture_origin,
            "file_name": file_name,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if upload_kind == "chunked":
        repo.append_job_event(
            row.job_id,
            event_type="chunked_upload_initialized",
            payload={
                "storage_key": storage_key,
                "part_size_bytes": int(upload_contract.get("partSizeBytes") or 0),
                "max_concurrency": int(upload_contract.get("maxConcurrency") or 0),
                "total_chunks": len(upload_contract.get("parts") or []),
            },
        )
    upload_detail = _uploading_detail(
        JobRow(
            **{
                **row.__dict__,
                "assigned_worker_id": reserved_worker_id,
            }
        )
    )
    repo.update_job(
        row.job_id,
        input_storage_key=storage_key,
        state=JobState.UPLOADING,
        title="正在上传到后台对象存储",
        detail=upload_detail,
        progress_fraction=0.0,
        progress_basis="chunked_upload_bytes" if upload_kind == "chunked" else "upload_bytes",
    )
    return {
        "jobId": row.job_id,
        "upload": upload_contract,
        "pollPath": f"/v1/mobile-jobs/{row.job_id}",
        "cancelPath": f"/v1/mobile-jobs/{row.job_id}",
    }


@app.post("/v1/jobs/{job_id}/upload-init", response_model=UploadInitResponse)
def upload_init(job_id: str) -> UploadInitResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    upload_contract, storage_key = _create_upload_contract(row, mobile=False)
    row = _release_stale_worker_reservation(row)
    _append_once_job_event(
        row.job_id,
        event_type="upload_window_opened",
        payload={"reported_at": datetime.now(timezone.utc).isoformat()},
    )
    reserved_worker_id = row.assigned_worker_id or _reserve_idle_worker_for_upload(job_id)
    upload_kind = str(upload_contract.get("kind") or "single")
    if upload_kind == "chunked":
        repo.append_job_event(
            row.job_id,
            event_type="chunked_upload_initialized",
            payload={
                "storage_key": storage_key,
                "part_size_bytes": int(upload_contract.get("part_size_bytes") or 0),
                "max_concurrency": int(upload_contract.get("max_concurrency") or 0),
                "total_chunks": len(upload_contract.get("parts") or []),
            },
        )
    upload_detail = _uploading_detail(
        JobRow(
            **{
                **row.__dict__,
                "assigned_worker_id": reserved_worker_id,
            }
        )
    )
    repo.update_job(
        job_id,
        input_storage_key=storage_key,
        state=JobState.UPLOADING,
        title="正在上传视频",
        detail=upload_detail,
        progress_basis="chunked_upload_bytes" if upload_kind == "chunked" else "upload_bytes",
    )
    return UploadInitResponse(upload=upload_contract)


@app.put("/v1/mobile-jobs/{job_id}/upload")
async def upload_mobile_job_payload(job_id: str, request: Request) -> Response:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if not row.input_storage_key:
        raise HTTPException(status_code=409, detail="upload_not_initialized")

    file_suffix = os.path.splitext(row.input_file_name or "upload.bin")[1]
    temp_file = tempfile.NamedTemporaryFile(prefix=f"{job_id}_", suffix=file_suffix, delete=False)
    temp_path = temp_file.name
    bytes_written = 0
    try:
        with temp_file:
            async for chunk in request.stream():
                if not chunk:
                    continue
                temp_file.write(chunk)
                bytes_written += len(chunk)
        storage.upload_local_file(
            storage_key=row.input_storage_key,
            local_path=Path(temp_path),
            content_type=request.headers.get("content-type") or row.input_content_type,
        )
        repo.update_job(
            job_id,
            input_size_bytes=bytes_written or row.input_size_bytes,
            state=JobState.UPLOADING,
            title="视频已到达控制平面",
            detail="控制平面已经收到视频，等待客户端确认上传完成。",
            progress_fraction=max(row.progress_fraction or 0.0, 0.98),
            progress_basis="control_plane_upload_complete",
        )
    except Exception as error:
        repo.update_job(
            job_id,
            state=JobState.FAILED,
            title="远端生成失败",
            detail=f"控制平面接收视频时失败：{error}",
            failure_reason="control_plane_upload_failed",
            failure_detail=str(error),
            estimated_remaining_seconds=0,
            completed_at=datetime.now(timezone.utc),
        )
        raise HTTPException(status_code=500, detail=f"control_plane_upload_failed:{error}") from error
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass

    return Response(status_code=204)


@app.post("/v1/jobs/{job_id}/upload-complete", response_model=JobStatusResponse)
def upload_complete(job_id: str, request: UploadCompleteRequest) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    row = _release_stale_worker_reservation(row)
    _append_once_job_event(
        job_id,
        event_type="upload_completed",
        payload={
            "size_bytes": request.size_bytes,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    row = repo.update_job(
        job_id,
        input_upload_etag=request.etag,
        input_size_bytes=request.size_bytes,
        state=JobState.QUEUED,
        title="视频已上传",
        detail=_queue_ready_detail(row),
        progress_fraction=0.15,
        progress_basis="upload_complete",
        upload_completed_at=datetime.now(timezone.utc),
    )
    return _status_from_row(row)


@app.post("/v1/mobile-jobs/{job_id}/multipart-complete")
def complete_mobile_job_multipart(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if row.state in {
        JobState.QUEUED,
        JobState.ASSIGNED,
        JobState.RECONSTRUCTING,
        JobState.TRAINING_PROBE,
        JobState.TRAINING_FULL,
        JobState.COMPLETED,
    }:
        return _mobile_status_from_row(row)
    if not row.input_storage_key:
        raise HTTPException(status_code=409, detail="upload_not_initialized")

    upload_id = payload.get("uploadId") or payload.get("upload_id")
    storage_key = payload.get("storageKey") or payload.get("storage_key") or row.input_storage_key
    parts = payload.get("parts") or []
    size_bytes = payload.get("sizeBytes") or payload.get("size_bytes") or row.input_size_bytes

    if not upload_id or not isinstance(parts, list) or not parts:
        raise HTTPException(status_code=400, detail="invalid_multipart_complete_request")
    if storage_key != row.input_storage_key:
        raise HTTPException(status_code=409, detail="multipart_storage_key_mismatch")

    try:
        etag = storage.complete_multipart_upload(
            storage_key=row.input_storage_key,
            upload_id=str(upload_id),
            parts=parts,
        )
        exists, object_size = storage.probe_object(row.input_storage_key)
        if not exists:
            raise RuntimeError("multipart_object_not_visible_after_complete")
    except Exception as error:
        exists, object_size = storage.probe_object(row.input_storage_key)
        if exists:
            etag = row.input_upload_etag or "multipart_already_completed"
        else:
            raise HTTPException(status_code=500, detail=f"multipart_complete_failed:{error}") from error

    row = _release_stale_worker_reservation(row)
    _append_once_job_event(
        job_id,
        event_type="multipart_upload_completed",
        payload={"reported_at": datetime.now(timezone.utc).isoformat()},
    )
    row = repo.update_job(
        job_id,
        input_upload_etag=etag,
        input_size_bytes=int(object_size or size_bytes or row.input_size_bytes),
        state=JobState.QUEUED,
        title="视频已上传",
        detail=_queue_ready_detail(row),
        progress_fraction=0.15,
        progress_basis="multipart_upload_complete",
        upload_completed_at=datetime.now(timezone.utc),
    )
    return _mobile_status_from_row(row)


@app.post("/v1/mobile-jobs/{job_id}/chunked-complete")
def complete_mobile_job_chunked(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if not row.input_storage_key:
        raise HTTPException(status_code=409, detail="upload_not_initialized")

    upload_id = payload.get("uploadId") or payload.get("upload_id")
    storage_key = payload.get("storageKey") or payload.get("storage_key") or row.input_storage_key
    parts = payload.get("parts") or []
    size_bytes = payload.get("sizeBytes") or payload.get("size_bytes") or row.input_size_bytes
    if not upload_id or storage_key != row.input_storage_key:
        raise HTTPException(status_code=400, detail="invalid_chunked_complete_request")

    repo.append_job_event(
        job_id,
        event_type="chunked_upload_completed",
        payload={
            "upload_id": str(upload_id),
            "storage_key": storage_key,
            "completed_parts": parts,
            "size_bytes": int(size_bytes or row.input_size_bytes),
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _append_once_job_event(
        job_id,
        event_type="upload_completed",
        payload={"reported_at": datetime.now(timezone.utc).isoformat()},
    )

    next_state = row.state
    next_title = row.title
    next_detail = row.detail
    next_progress_fraction = row.progress_fraction
    next_progress_basis = "chunked_upload_complete"
    if row.state == JobState.UPLOADING:
        next_state = JobState.QUEUED
        next_title = "视频已上传"
        next_detail = _queue_ready_detail(row)
        next_progress_fraction = max(row.progress_fraction or 0.0, 0.15)

    row = repo.update_job(
        job_id,
        input_upload_etag="chunked_complete",
        input_size_bytes=int(size_bytes or row.input_size_bytes),
        state=next_state,
        title=next_title,
        detail=next_detail,
        progress_fraction=next_progress_fraction,
        progress_basis=next_progress_basis,
        upload_completed_at=datetime.now(timezone.utc),
    )
    return _mobile_status_from_row(row)


def _record_upload_part_ready(job_id: str, payload: dict[str, Any], *, event_type: str) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if row.state not in {JobState.UPLOADING, JobState.CREATED, JobState.ASSIGNED}:
        return _mobile_status_from_row(row)
    if not row.input_storage_key:
        raise HTTPException(status_code=409, detail="upload_not_initialized")

    upload_id = payload.get("uploadId") or payload.get("upload_id")
    storage_key = payload.get("storageKey") or payload.get("storage_key") or row.input_storage_key
    part_number = payload.get("partNumber") or payload.get("part_number")
    completed_part_count = payload.get("completedPartCount") or payload.get("completed_part_count")
    total_part_count = payload.get("totalPartCount") or payload.get("total_part_count")
    uploaded_bytes = payload.get("uploadedBytes") or payload.get("uploaded_bytes")
    etag = payload.get("etag")

    if not upload_id or storage_key != row.input_storage_key or not part_number or not etag:
        raise HTTPException(status_code=400, detail="invalid_multipart_part_ready")

    try:
        uploaded_bytes_int = int(uploaded_bytes or 0)
        completed_part_count_int = int(completed_part_count or 0)
        total_part_count_int = int(total_part_count or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid_multipart_part_ready_counts") from exc

    repo.append_job_event(
        job_id,
        event_type=event_type,
        payload={
            "upload_id": str(upload_id),
            "storage_key": storage_key,
            "part_number": int(part_number),
            "etag": str(etag),
            "uploaded_bytes": uploaded_bytes_int,
            "completed_part_count": completed_part_count_int,
            "total_part_count": total_part_count_int,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _append_once_job_event(
        job_id,
        event_type="upload_started",
        payload={
            "reported_at": datetime.now(timezone.utc).isoformat(),
            "storage_key": storage_key,
        },
    )

    row = _release_stale_worker_reservation(row)
    next_title = "远端正在边传边接收输入" if row.state == JobState.ASSIGNED else "正在上传到后台对象存储"
    row = repo.update_job(
        job_id,
        state=row.state if row.state == JobState.ASSIGNED else JobState.UPLOADING,
        title=next_title,
        detail=_uploading_detail(
            row,
            uploaded_bytes=uploaded_bytes_int,
            completed_parts=completed_part_count_int,
            total_parts=total_part_count_int,
            stream_signal_seen=True,
        ),
        progress_fraction=max(
            row.progress_fraction or 0.0,
            _upload_progress_fraction(uploaded_bytes_int, row.input_size_bytes),
        ),
        progress_basis="chunk_part_uploaded" if event_type == "chunk_part_ready" else "multipart_part_uploaded",
    )
    return _mobile_status_from_row(row)


@app.post("/v1/mobile-jobs/{job_id}/multipart-part-ready")
def multipart_part_ready(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _record_upload_part_ready(job_id, payload, event_type="multipart_part_ready")


@app.post("/v1/mobile-jobs/{job_id}/chunk-part-ready")
def chunk_part_ready(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _record_upload_part_ready(job_id, payload, event_type="chunk_part_ready")


@app.post("/v1/mobile-jobs/{job_id}/multipart-abort")
def abort_mobile_job_multipart(job_id: str, payload: dict[str, Any]) -> Response:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if not row.input_storage_key:
        raise HTTPException(status_code=409, detail="upload_not_initialized")

    upload_id = payload.get("uploadId") or payload.get("upload_id")
    storage_key = payload.get("storageKey") or payload.get("storage_key") or row.input_storage_key
    if not upload_id:
        raise HTTPException(status_code=400, detail="invalid_multipart_abort_request")
    if storage_key != row.input_storage_key:
        raise HTTPException(status_code=409, detail="multipart_storage_key_mismatch")

    try:
        storage.abort_multipart_upload(storage_key=row.input_storage_key, upload_id=str(upload_id))
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"multipart_abort_failed:{error}") from error
    repo.append_job_event(
        job_id,
        event_type="multipart_upload_aborted",
        payload={
            "storage_key": row.input_storage_key,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _mark_upload_aborted(
        row,
        failure_reason="upload_failed",
        detail=(
            "后台上传在分片阶段中断。控制平面已停止这次任务；"
            "如果远端 worker 已经开始预处理，也会继续收到取消并自动清理临时数据。"
        ),
    )
    return Response(status_code=204)


@app.post("/v1/mobile-jobs/{job_id}/chunked-abort")
def abort_mobile_job_chunked(job_id: str, payload: dict[str, Any]) -> Response:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if not row.input_storage_key:
        raise HTTPException(status_code=409, detail="upload_not_initialized")

    storage_key = payload.get("storageKey") or payload.get("storage_key") or row.input_storage_key
    if storage_key != row.input_storage_key:
        raise HTTPException(status_code=409, detail="chunked_storage_key_mismatch")

    try:
        storage.abort_chunked_upload(storage_key=row.input_storage_key)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"chunked_abort_failed:{error}") from error

    repo.append_job_event(
        job_id,
        event_type="chunked_upload_aborted",
        payload={
            "storage_key": row.input_storage_key,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _mark_upload_aborted(
        row,
        failure_reason="upload_failed",
        detail=(
            "后台上传在流式分片阶段中断。控制平面已停止这次任务；"
            "如果远端 worker 已经开始边上传边预处理，也会继续收到取消并自动清理临时数据。"
        ),
    )
    return Response(status_code=204)


def _finalize_cancel_if_worker_released(row: JobRow) -> JobRow:
    if row.state != JobState.CANCELLED or not row.assigned_worker_id:
        return row
    worker = repo.get_worker(row.assigned_worker_id)
    if worker:
        heartbeat_age = (datetime.now(timezone.utc) - worker.last_heartbeat_at).total_seconds()
        if worker.current_job_id == row.job_id and heartbeat_age <= 20:
            return row
    cleanup = _cleanup_terminal_job_storage(row, remove_artifacts=True)
    preserved_reason = row.failure_reason or "cancelled_by_user"
    detail = (
        "远端 worker 已确认停止，上传中断产生的临时数据已经清理。"
        if preserved_reason == "upload_failed"
        else "远端 worker 已确认停止，本地处理已经释放。"
    )
    return repo.update_job(
        row.job_id,
        title="上传已中断" if preserved_reason == "upload_failed" else "任务已取消",
        detail=detail,
        assigned_worker_id=None,
        stage="cancelled",
        phase_name="cancelled",
        progress_basis="cancelled_acknowledged",
        failure_reason=preserved_reason,
        estimated_remaining_seconds=0,
        input_storage_key=None if cleanup["input_deleted"] else row.input_storage_key,
        input_upload_etag=None if cleanup["input_deleted"] else row.input_upload_etag,
        artifact=None if cleanup["artifacts_deleted"] else row.artifact,
    )


@app.get("/v1/mobile-jobs/{job_id}")
def get_mobile_job(job_id: str) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    row = _finalize_cancel_if_worker_released(row)
    return _mobile_status_from_row(row)


@app.get("/v1/mobile-jobs/{job_id}/timeline", response_model=JobTimelineSummary)
def get_mobile_job_timeline(job_id: str) -> JobTimelineSummary:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    row = _finalize_cancel_if_worker_released(row)
    return _timeline_for_row(row)


@app.post("/v1/mobile-jobs/{job_id}/client-event")
def append_mobile_client_event(job_id: str, request: ClientLifecycleEventRequest) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    event_type = str(request.event_type or "").strip()
    if not event_type:
        raise HTTPException(status_code=400, detail="invalid_client_event_type")
    payload = {
        **request.payload,
        "reported_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.append_job_event(job_id, event_type=event_type, payload=payload)
    row = _finalize_cancel_if_worker_released(row)
    return _mobile_status_from_row(row)


@app.get("/v1/jobs/{job_id}/chunk-manifest")
def get_job_chunk_manifest(job_id: str) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    chunk_state = _chunk_manifest_state(row)
    if chunk_state is None:
        raise HTTPException(status_code=404, detail="chunk_manifest_not_ready")
    return {
        "job_id": row.job_id,
        "storage_key": row.input_storage_key,
        "content_type": row.input_content_type,
        "total_size_bytes": row.input_size_bytes,
        "chunk_size_bytes": chunk_state["chunk_size_bytes"],
        "total_chunks": chunk_state["total_chunks"],
        "visible_bytes": chunk_state["visible_bytes"],
        "upload_completed": chunk_state["upload_completed"],
        "chunks": chunk_state["chunks"],
    }


@app.get("/v1/mobile-jobs/{job_id}/artifact-download")
def get_mobile_job_artifact(job_id: str) -> StreamingResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    artifact = row.artifact
    if not artifact or not artifact.primary_artifact or not artifact.primary_artifact.storage_key:
        raise HTTPException(status_code=404, detail="artifact_not_ready")
    return _stream_object_response(artifact.primary_artifact.storage_key)


@app.get("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    row = _finalize_cancel_if_worker_released(row)
    return _status_from_row(row)


@app.get("/v1/jobs/{job_id}/timeline", response_model=JobTimelineSummary)
def get_job_timeline(job_id: str) -> JobTimelineSummary:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    row = _finalize_cancel_if_worker_released(row)
    return _timeline_for_row(row)


@app.delete("/v1/mobile-jobs/{job_id}")
def cancel_mobile_job(job_id: str) -> dict[str, Any]:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    assigned_worker_id = row.assigned_worker_id
    detail = (
        "远端 worker 已收到取消请求，正在停止本地处理并释放设备。通常几秒内完成。"
        if assigned_worker_id
        else "控制平面已停止这次任务；如果当时还没真正开始远端处理，就不会再继续。"
    )
    row = repo.update_job(
        job_id,
        state=JobState.CANCELLED,
        title="正在停止远端任务" if assigned_worker_id else "任务已取消",
        detail=detail,
        stage="cancel_requested",
        phase_name="cancel_requested",
        progress_basis="cancel_requested",
        failure_reason="cancelled_by_user",
        cancelled_at=datetime.now(timezone.utc),
        estimated_remaining_seconds=0,
    )
    return _mobile_status_from_row(row)


@app.delete("/v1/jobs/{job_id}", response_model=JobStatusResponse)
def cancel_job(job_id: str) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    assigned_worker_id = row.assigned_worker_id
    detail = (
        "远端 worker 已收到取消请求，正在停止本地处理并释放设备。通常几秒内完成。"
        if assigned_worker_id
        else "控制平面已停止这次任务；如果当时还没真正开始远端处理，就不会再继续。"
    )
    row = repo.update_job(
        job_id,
        state=JobState.CANCELLED,
        title="正在停止远端任务" if assigned_worker_id else "任务已取消",
        detail=detail,
        stage="cancel_requested",
        phase_name="cancel_requested",
        progress_basis="cancel_requested",
        failure_reason="cancelled_by_user",
        cancelled_at=datetime.now(timezone.utc),
        estimated_remaining_seconds=0,
    )
    return _status_from_row(row)


@app.post("/v1/workers/register", response_model=RegisterWorkerResponse)
def register_worker(request: RegisterWorkerRequest) -> RegisterWorkerResponse:
    row = repo.register_worker(**request.model_dump())
    return RegisterWorkerResponse(
        worker_id=row.worker_id,
        heartbeat_interval_sec=settings.heartbeat_interval_sec,
        pull_interval_sec=settings.pull_interval_sec,
    )


@app.post("/v1/workers/{worker_id}/heartbeat", response_model=WorkerHeartbeatResponse)
def worker_heartbeat(worker_id: str, request: WorkerHeartbeatRequest) -> WorkerHeartbeatResponse:
    if repo.get_worker(worker_id) is None:
        raise HTTPException(status_code=404, detail="worker_not_found")
    repo.heartbeat_worker(worker_id, **request.model_dump())
    return WorkerHeartbeatResponse(ok=True, drain=False)


@app.post("/v1/workers/{worker_id}/claim-next", response_model=ClaimNextResponse)
def claim_next(worker_id: str, request: ClaimNextRequest) -> ClaimNextResponse:
    if repo.get_worker(worker_id) is None:
        raise HTTPException(status_code=404, detail="worker_not_found")
    job = repo.claim_next_job(
        worker_id,
        min_chunk_ready_bytes=settings.object_storage_chunked_ingest_min_ready_bytes,
        gpu_count=request.gpu_count,
        vram_mb=request.vram_mb,
        disk_free_mb=request.disk_free_mb,
        capability_flags=request.capability_flags,
    )
    if not job or not job.input_storage_key:
        return ClaimNextResponse(assignment=None)
    chunk_state = _chunk_manifest_state(job)
    input_mode = "object_storage"
    download_url = storage.build_download_url(job.input_storage_key)
    manifest_url = None
    upload_completed = True
    detail = "远端机器已经接单，正在把视频下载到工作目录，接着会开始拆帧和重建。"
    progress_basis = "worker_assigned_preparing_input"
    progress_fraction = 0.24
    phase_name = "download_input"
    if chunk_state is not None:
        input_mode = "chunked_stream"
        download_url = None
        manifest_url = f"{settings.public_base_url.rstrip('/')}/v1/jobs/{job.job_id}/chunk-manifest"
        upload_completed = bool(chunk_state["upload_completed"])
        detail = "远端机器已经接单，正在边等后续 chunk、边把已到达的输入拉到工作目录。"
        progress_basis = "worker_assigned_streaming_input"
        progress_fraction = max(job.progress_fraction or 0.0, 0.12)
        phase_name = "streaming_input"
    repo.update_job(
        job.job_id,
        state=JobState.ASSIGNED,
        stage="preparing_input",
        phase_name=phase_name,
        title="任务已分配",
        detail=detail,
        progress_fraction=progress_fraction,
        progress_basis=progress_basis,
    )
    assignment = JobAssignment(
        job_id=job.job_id,
        lease_ttl_sec=settings.worker_lease_ttl_sec,
        input=JobInputDescriptor(
            storage_key=job.input_storage_key,
            download_url=download_url,
            content_type=job.input_content_type,
            size_bytes=job.input_size_bytes,
            mode=input_mode,
            manifest_url=manifest_url,
            upload_completed=upload_completed,
        ),
        output_prefix=f"{settings.artifact_bucket_prefix}/{job.job_id}/",
        pipeline_profile=_pipeline_profile_for_job(job.job_id),
    )
    return ClaimNextResponse(assignment=assignment)


@app.post("/v1/jobs/{job_id}/runtime", response_model=JobStatusResponse)
def update_runtime(job_id: str, request: RuntimeUpdateRequest) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if row.state in {JobState.CANCELLED, JobState.COMPLETED}:
        return _status_from_row(row)
    resolved_state, failure_reason, failure_detail = _coerce_runtime_failure(request)
    runtime_metrics = _sanitize_runtime_metrics(request.metrics)
    row = repo.update_job(
        job_id,
        state=resolved_state,
        stage=request.stage,
        phase_name=request.phase_name,
        current_tier=request.current_tier,
        title="远端生成失败" if resolved_state == JobState.FAILED else request.title,
        detail=request.detail,
        progress_fraction=request.progress_fraction,
        progress_basis=request.progress_basis,
        elapsed_seconds=request.elapsed_seconds,
        estimated_remaining_seconds=request.estimated_remaining_seconds,
        assigned_worker_id=request.worker_id,
        started_at=row.started_at or datetime.now(timezone.utc),
        failure_reason=failure_reason,
        failure_detail=failure_detail,
        completed_at=datetime.now(timezone.utc) if resolved_state == JobState.FAILED else row.completed_at,
    )
    repo.append_job_event(
        job_id,
        event_type="runtime_update",
        payload={
            "state": resolved_state.value,
            "stage": request.stage,
            "phase_name": request.phase_name,
            "progress_basis": request.progress_basis,
            "progress_fraction": request.progress_fraction,
            "elapsed_seconds": request.elapsed_seconds,
            "estimated_remaining_seconds": request.estimated_remaining_seconds,
            "metrics": runtime_metrics,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    _record_runtime_timeline_milestones(job_id, request)
    repo.heartbeat_worker(worker_id=request.worker_id, state=WorkerState.BUSY, current_job_id=job_id)
    return _status_from_row(row)


@app.post("/v1/jobs/{job_id}/artifact-manifest", response_model=JobStatusResponse)
def artifact_manifest(job_id: str, request: ArtifactManifestRequest) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if row.state in {JobState.CANCELLED, JobState.COMPLETED}:
        return _status_from_row(row)

    manifest = request.manifest.model_copy(deep=True)
    if manifest.primary_artifact and manifest.primary_artifact.storage_key:
        manifest.primary_artifact.download_url = storage.build_download_url(manifest.primary_artifact.storage_key)
    if manifest.preview and manifest.preview.storage_key:
        manifest.preview.download_url = storage.build_download_url(manifest.preview.storage_key)
    if manifest.metrics and manifest.metrics.storage_key:
        manifest.metrics.download_url = storage.build_download_url(manifest.metrics.storage_key)
    if manifest.viewer_manifest and manifest.viewer_manifest.storage_key:
        manifest.viewer_manifest.download_url = storage.build_download_url(manifest.viewer_manifest.storage_key)

    row = repo.update_job(
        job_id,
        artifact=manifest,
        stage="export",
        phase_name="artifact",
        title="结果已上传",
        detail="worker 已把产物上传到对象存储，等待完成确认。",
        state=JobState.EXPORTING,
        progress_fraction=max(row.progress_fraction or 0.0, 0.92),
        progress_basis="artifact_manifest",
        failure_reason=None,
        failure_detail=None,
    )
    _append_once_job_event(
        job_id,
        event_type="artifact_manifest_uploaded",
        payload={
            "worker_id": request.worker_id,
            "reported_at": datetime.now(timezone.utc).isoformat(),
            "primary_storage_key": (
                manifest.primary_artifact.storage_key if manifest.primary_artifact else None
            ),
        },
    )
    return _status_from_row(row)


@app.post("/v1/jobs/{job_id}/complete", response_model=JobStatusResponse)
def complete_job(job_id: str, request: CompleteJobRequest) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if row.state in {JobState.CANCELLED, JobState.COMPLETED}:
        return _status_from_row(row)
    cleanup = _cleanup_terminal_job_storage(row, remove_artifacts=False)
    row = repo.update_job(
        job_id,
        state=JobState.COMPLETED,
        stage="completed",
        phase_name="artifact",
        title=request.title,
        detail=request.detail,
        progress_fraction=1.0,
        progress_basis="completed",
        estimated_remaining_seconds=0,
        failure_reason=None,
        failure_detail=None,
        completed_at=datetime.now(timezone.utc),
        input_storage_key=None if cleanup["input_deleted"] else row.input_storage_key,
        input_upload_etag=None if cleanup["input_deleted"] else row.input_upload_etag,
    )
    _append_once_job_event(
        job_id,
        event_type="job_completed",
        payload={
            "worker_id": request.worker_id,
            "reported_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    repo.heartbeat_worker(worker_id=request.worker_id, state=WorkerState.IDLE, current_job_id=None)
    return _status_from_row(row)


@app.post("/v1/jobs/{job_id}/fail", response_model=JobStatusResponse)
def fail_job(job_id: str, request: FailJobRequest) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    if row.state in {JobState.CANCELLED, JobState.COMPLETED}:
        return _status_from_row(row)
    cleanup = _cleanup_terminal_job_storage(row, remove_artifacts=True)
    row = repo.update_job(
        job_id,
        state=JobState.FAILED,
        title="远端生成失败",
        detail=request.failure_detail,
        failure_reason=request.failure_reason,
        failure_detail=request.failure_detail,
        stage=request.stage,
        phase_name=request.phase_name,
        estimated_remaining_seconds=0,
        completed_at=datetime.now(timezone.utc),
        input_storage_key=None if cleanup["input_deleted"] else row.input_storage_key,
        input_upload_etag=None if cleanup["input_deleted"] else row.input_upload_etag,
        artifact=None if cleanup["artifacts_deleted"] else row.artifact,
    )
    repo.heartbeat_worker(worker_id=request.worker_id, state=WorkerState.IDLE, current_job_id=None)
    return _status_from_row(row)


@app.post("/v1/jobs/{job_id}/cancel-ack", response_model=JobStatusResponse)
def cancel_ack(job_id: str, request: CancelAckRequest) -> JobStatusResponse:
    row = repo.get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job_not_found")
    cleanup = _cleanup_terminal_job_storage(row, remove_artifacts=True)
    preserved_reason = row.failure_reason or "cancelled_by_user"
    detail = (
        "worker 已确认本地停止，上传中断产生的临时数据已经清理。"
        if preserved_reason == "upload_failed"
        else "worker 已确认本地停止。"
    )
    row = repo.update_job(
        job_id,
        state=JobState.CANCELLED,
        title="上传已中断" if preserved_reason == "upload_failed" else "任务已取消",
        detail=detail,
        failure_reason=preserved_reason,
        assigned_worker_id=None,
        cancelled_at=datetime.now(timezone.utc),
        estimated_remaining_seconds=0,
        input_storage_key=None if cleanup["input_deleted"] else row.input_storage_key,
        input_upload_etag=None if cleanup["input_deleted"] else row.input_upload_etag,
        artifact=None if cleanup["artifacts_deleted"] else row.artifact,
    )
    repo.heartbeat_worker(worker_id=request.worker_id, state=WorkerState.IDLE, current_job_id=None)
    return _status_from_row(row)
