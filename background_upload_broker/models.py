from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional


class JobState(str, Enum):
    PREPARING_UPLOAD = "preparing_upload"
    UPLOADING = "uploading"
    QUEUED = "queued"
    RECONSTRUCTING = "reconstructing"
    TRAINING = "training"
    PACKAGING = "packaging"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ArtifactPayload:
    download_url: str
    format: str

    def to_json(self) -> Dict[str, Any]:
        return {"download_url": self.download_url, "format": self.format}


@dataclass
class WorkerAssignment:
    worker_id: str
    queue_name: str

    def to_json(self) -> Dict[str, Any]:
        return {"worker_id": self.worker_id, "queue_name": self.queue_name}


@dataclass
class UploadRequest:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)

    def to_json(self) -> Dict[str, Any]:
        return {"method": self.method, "url": self.url, "headers": self.headers}


@dataclass
class JobRecord:
    job_id: str
    file_name: str
    file_size_bytes: int
    content_type: str
    capture_origin: str
    client_record_id: Optional[str]
    created_at_epoch: float
    updated_at_epoch: float
    state: JobState
    title: str
    detail: str
    progress_fraction: float = 0.0
    elapsed_seconds: int = 0
    estimated_remaining_seconds: Optional[int] = None
    progress_basis: Optional[str] = None
    artifact: Optional[ArtifactPayload] = None
    failure_reason: Optional[str] = None
    worker_assignment: Optional[WorkerAssignment] = None
    local_upload_path: Optional[str] = None
    local_artifact_path: Optional[str] = None
    remote_input_path: Optional[str] = None
    remote_run_name: Optional[str] = None
    remote_output_root: Optional[str] = None
    remote_artifact_path: Optional[str] = None
    remote_summary_path: Optional[str] = None
    remote_verdict_path: Optional[str] = None
    upload_started_at_epoch: Optional[float] = None
    upload_completed_at_epoch: Optional[float] = None
    remote_started_at_epoch: Optional[float] = None
    last_runtime_seen_at_epoch: Optional[float] = None
    cancellation_requested: bool = False

    def to_status_json(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "job_id": self.job_id,
            "state": self.state.value,
            "title": self.title,
            "detail": self.detail,
            "progress_fraction": self.progress_fraction,
            "elapsed_seconds": self.elapsed_seconds,
            "estimated_remaining_seconds": self.estimated_remaining_seconds,
            "progress_basis": self.progress_basis,
            "failure_reason": self.failure_reason,
        }
        if self.artifact is not None:
            payload["artifact"] = self.artifact.to_json()
        return payload

    def to_persisted_json(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        if self.artifact is not None:
            payload["artifact"] = self.artifact.to_json()
        if self.worker_assignment is not None:
            payload["worker_assignment"] = self.worker_assignment.to_json()
        return payload

    @classmethod
    def from_persisted_json(cls, payload: Dict[str, Any]) -> "JobRecord":
        artifact_payload = payload.get("artifact")
        worker_assignment_payload = payload.get("worker_assignment")
        return cls(
            job_id=payload["job_id"],
            file_name=payload["file_name"],
            file_size_bytes=int(payload["file_size_bytes"]),
            content_type=payload["content_type"],
            capture_origin=payload["capture_origin"],
            client_record_id=payload.get("client_record_id"),
            created_at_epoch=float(payload["created_at_epoch"]),
            updated_at_epoch=float(payload["updated_at_epoch"]),
            state=JobState(payload["state"]),
            title=payload["title"],
            detail=payload["detail"],
            progress_fraction=float(payload.get("progress_fraction", 0.0)),
            elapsed_seconds=int(payload.get("elapsed_seconds", 0) or 0),
            estimated_remaining_seconds=payload.get("estimated_remaining_seconds"),
            progress_basis=payload.get("progress_basis"),
            artifact=ArtifactPayload(**artifact_payload) if artifact_payload else None,
            failure_reason=payload.get("failure_reason"),
            worker_assignment=WorkerAssignment(**worker_assignment_payload)
            if worker_assignment_payload
            else None,
            local_upload_path=payload.get("local_upload_path"),
            local_artifact_path=payload.get("local_artifact_path"),
            remote_input_path=payload.get("remote_input_path"),
            remote_run_name=payload.get("remote_run_name"),
            remote_output_root=payload.get("remote_output_root"),
            remote_artifact_path=payload.get("remote_artifact_path"),
            remote_summary_path=payload.get("remote_summary_path"),
            remote_verdict_path=payload.get("remote_verdict_path"),
            upload_started_at_epoch=payload.get("upload_started_at_epoch"),
            upload_completed_at_epoch=payload.get("upload_completed_at_epoch"),
            remote_started_at_epoch=payload.get("remote_started_at_epoch"),
            last_runtime_seen_at_epoch=payload.get("last_runtime_seen_at_epoch"),
            cancellation_requested=bool(payload.get("cancellation_requested", False)),
        )

    @property
    def upload_path(self) -> Optional[Path]:
        return Path(self.local_upload_path) if self.local_upload_path else None

    @property
    def artifact_path(self) -> Optional[Path]:
        return Path(self.local_artifact_path) if self.local_artifact_path else None
