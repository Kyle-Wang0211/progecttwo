from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class JobState(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RECONSTRUCTING = "reconstructing"
    TRAINING_PROBE = "training_probe"
    TRAINING_FULL = "training_full"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerState(str, Enum):
    REGISTERING = "registering"
    IDLE = "idle"
    BUSY = "busy"
    DRAINING = "draining"
    OFFLINE = "offline"


class CreateJobRequest(BaseModel):
    tenant_id: str = Field(default="tenant_demo")
    user_id: Optional[str] = None
    file_name: str
    file_size_bytes: int
    content_type: str
    capture_origin: str
    client_record_id: Optional[str] = None
    pipeline_profile: Dict[str, Any] = Field(default_factory=dict)


class UploadSpec(BaseModel):
    kind: str = "single"
    method: Optional[str] = None
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    storage_key: str
    upload_id: Optional[str] = None
    part_size_bytes: Optional[int] = None
    max_concurrency: Optional[int] = None
    part_ready_url: Optional[str] = None
    parts: list[Dict[str, Any]] = Field(default_factory=list)
    complete_url: Optional[str] = None
    abort_url: Optional[str] = None


class CreateJobResponse(BaseModel):
    job_id: str
    state: JobState
    upload_init_path: str
    poll_path: str
    cancel_path: str


class UploadInitResponse(BaseModel):
    upload: UploadSpec


class UploadCompleteRequest(BaseModel):
    etag: Optional[str] = None
    size_bytes: int


class ArtifactDescriptor(BaseModel):
    type: str
    storage_key: str
    download_url: Optional[str] = None
    size_bytes: Optional[int] = None
    checksum_sha256: Optional[str] = None


class ArtifactManifest(BaseModel):
    primary_artifact: Optional[ArtifactDescriptor] = None
    preview: Optional[ArtifactDescriptor] = None
    metrics: Optional[ArtifactDescriptor] = None
    viewer_manifest: Optional[ArtifactDescriptor] = None


class JobTimelinePhase(BaseModel):
    key: str
    label: str
    status: str
    started_at: Optional[datetime] = None
    latest_activity_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    progress_basis: Optional[str] = None
    detail: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)


class JobTimelineOverlap(BaseModel):
    earlier_key: str
    later_key: str
    overlap_seconds: int


class JobTimelineSummary(BaseModel):
    generated_at: datetime
    phases: list[JobTimelinePhase] = Field(default_factory=list)
    overlaps: list[JobTimelineOverlap] = Field(default_factory=list)


class JobStatusResponse(BaseModel):
    job_id: str
    state: JobState
    stage: Optional[str] = None
    phase_name: Optional[str] = None
    current_tier: Optional[str] = None
    title: str
    detail: str
    progress_fraction: Optional[float] = None
    progress_basis: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    estimated_remaining_seconds: Optional[int] = None
    failure_reason: Optional[str] = None
    failure_detail: Optional[str] = None
    upload_completed: bool = False
    metrics: Dict[str, Any] = Field(default_factory=dict)
    artifact: Optional[ArtifactManifest] = None
    timeline: Optional[JobTimelineSummary] = None
    assigned_worker_id: Optional[str] = None
    updated_at: datetime


class RegisterWorkerRequest(BaseModel):
    worker_id: Optional[str] = None
    provider: str
    region: Optional[str] = None
    instance_label: str
    host_fingerprint: str
    gpu_model: str
    gpu_count: int
    vram_mb: int
    cpu_cores: Optional[int] = None
    ram_mb: Optional[int] = None
    disk_free_mb: Optional[int] = None
    software_version: Optional[str] = None
    capability_flags: Dict[str, Any] = Field(default_factory=dict)


class RegisterWorkerResponse(BaseModel):
    worker_id: str
    heartbeat_interval_sec: int
    pull_interval_sec: int


class WorkerHeartbeatRequest(BaseModel):
    state: WorkerState
    gpu_util: Optional[float] = None
    gpu_mem_used_mb: Optional[int] = None
    cpu_util: Optional[float] = None
    disk_free_mb: Optional[int] = None
    current_job_id: Optional[str] = None


class WorkerHeartbeatResponse(BaseModel):
    ok: bool = True
    drain: bool = False


class ClaimNextRequest(BaseModel):
    max_concurrent_jobs: int = 1
    gpu_model: str
    gpu_count: Optional[int] = None
    vram_mb: Optional[int] = None
    disk_free_mb: Optional[int] = None
    capability_flags: Dict[str, Any] = Field(default_factory=dict)


class JobInputDescriptor(BaseModel):
    storage_key: str
    download_url: Optional[str] = None
    content_type: str
    size_bytes: int
    mode: Optional[str] = None
    manifest_url: Optional[str] = None
    upload_completed: Optional[bool] = None


class JobAssignment(BaseModel):
    job_id: str
    lease_ttl_sec: int
    input: JobInputDescriptor
    output_prefix: str
    pipeline_profile: Dict[str, Any]


class ClaimNextResponse(BaseModel):
    assignment: Optional[JobAssignment] = None


class RuntimeUpdateRequest(BaseModel):
    worker_id: str
    state: JobState
    stage: str
    phase_name: Optional[str] = None
    current_tier: Optional[str] = None
    title: str
    detail: str
    progress_fraction: Optional[float] = None
    progress_basis: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    estimated_remaining_seconds: Optional[int] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)


class ClientLifecycleEventRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class ArtifactManifestRequest(BaseModel):
    worker_id: str
    manifest: ArtifactManifest


class CompleteJobRequest(BaseModel):
    worker_id: str
    title: str
    detail: str


class FailJobRequest(BaseModel):
    worker_id: str
    failure_reason: str
    failure_detail: str
    stage: Optional[str] = None
    phase_name: Optional[str] = None


class CancelAckRequest(BaseModel):
    worker_id: str
