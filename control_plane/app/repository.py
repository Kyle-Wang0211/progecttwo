from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from threading import RLock
from typing import Any, Dict, Optional
from uuid import uuid4

from .config import settings
from .models import ArtifactManifest, JobState, WorkerState

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json
except ImportError:  # pragma: no cover - depends on optional runtime deps
    psycopg = None
    dict_row = None
    Json = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stable_worker_id_for_payload(payload: dict[str, Any]) -> str:
    host_fingerprint = str(payload.get("host_fingerprint") or "").strip()
    instance_label = str(payload.get("instance_label") or "").strip()
    if host_fingerprint:
        identity_basis = ":".join(
            part
            for part in (
                host_fingerprint,
                instance_label,
                str(payload.get("provider") or "").strip(),
                str(payload.get("region") or "").strip(),
            )
            if part
        )
    else:
        identity_basis = ":".join(
            [
                str(payload.get("provider") or "").strip(),
                str(payload.get("region") or "").strip(),
                instance_label,
            ]
        )
    digest = hashlib.sha256(identity_basis.encode("utf-8")).hexdigest()[:12]
    return f"worker_{digest}"


def _truthy_capability(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _merged_capability_flags(
    base_flags: Optional[dict[str, Any]],
    override_flags: Optional[dict[str, Any]],
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base_flags or {})
    if isinstance(override_flags, dict):
        merged.update(override_flags)
    return merged


def _normalize_pipeline_strategy(value: Any) -> str:
    strategy = str(value or "").strip().lower()
    return strategy or "autofallback"


LEGACY_PIPELINE_STRATEGIES = {
    "autofallback",
    "legacy_hislam2",
    "official_default",
}


def _allowed_pipeline_strategies_for_capability_flags(capability_flags: Optional[dict[str, Any]]) -> list[str]:
    flags = capability_flags if isinstance(capability_flags, dict) else {}
    families = flags.get("pipeline_families")
    if isinstance(families, list):
        normalized = [
            str(item).strip().lower()
            for item in families
            if str(item).strip()
        ]
        if normalized:
            return normalized
    return sorted(LEGACY_PIPELINE_STRATEGIES)


def _worker_supports_pipeline_strategy(capability_flags: Optional[dict[str, Any]], strategy: Optional[str]) -> bool:
    normalized = _normalize_pipeline_strategy(strategy)
    return normalized in _allowed_pipeline_strategies_for_capability_flags(capability_flags)


def _pipeline_strategy_from_events(events: list[dict[str, Any]]) -> str:
    for event in reversed(events):
        if event.get("event_type") != "pipeline_profile_requested":
            continue
        payload = event.get("payload")
        if isinstance(payload, dict):
            return _normalize_pipeline_strategy(payload.get("strategy"))
    return "autofallback"


def _worker_meets_scheduler_requirements(
    *,
    gpu_count: Optional[int],
    vram_mb: Optional[int],
    disk_free_mb: Optional[int],
    capability_flags: Optional[dict[str, Any]],
) -> bool:
    if int(gpu_count or 0) < settings.scheduler_min_worker_gpu_count:
        return False
    if int(vram_mb or 0) < settings.scheduler_min_worker_vram_mb:
        return False
    if int(disk_free_mb or 0) < settings.scheduler_min_worker_disk_free_mb:
        return False
    flags = capability_flags or {}
    pipeline_families = flags.get("pipeline_families")
    if isinstance(pipeline_families, list):
        normalized_families = [str(item).strip().lower() for item in pipeline_families if str(item).strip()]
        if normalized_families:
            return True
    return all(_truthy_capability(flags.get(name)) for name in settings.scheduler_required_worker_capabilities)


@dataclass
class JobRow:
    job_id: str
    tenant_id: str
    user_id: Optional[str]
    client_record_id: Optional[str]
    capture_origin: str
    input_file_name: str
    input_content_type: str
    input_size_bytes: int
    input_storage_key: Optional[str]
    input_upload_etag: Optional[str]
    state: JobState
    stage: Optional[str]
    phase_name: Optional[str]
    current_tier: Optional[str]
    title: str
    detail: str
    progress_fraction: Optional[float]
    progress_basis: Optional[str]
    elapsed_seconds: Optional[int]
    estimated_remaining_seconds: Optional[int]
    assigned_worker_id: Optional[str]
    artifact: Optional[ArtifactManifest]
    failure_reason: Optional[str]
    failure_detail: Optional[str]
    created_at: datetime
    updated_at: datetime
    upload_completed_at: Optional[datetime] = None
    assigned_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None


@dataclass
class WorkerRow:
    worker_id: str
    provider: str
    region: Optional[str]
    instance_label: str
    host_fingerprint: str
    gpu_model: str
    gpu_count: int
    vram_mb: int
    cpu_cores: Optional[int]
    ram_mb: Optional[int]
    disk_free_mb: Optional[int]
    software_version: Optional[str]
    capability_flags: Dict[str, Any]
    state: WorkerState
    current_job_id: Optional[str]
    last_heartbeat_at: datetime
    updated_at: datetime


class InMemoryRepository:
    """
    Dev-only fallback repository.

    The production path should use PostgresRepository, but keeping this class
    makes local API bring-up possible even before a database is provisioned.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self.jobs: Dict[str, JobRow] = {}
        self.workers: Dict[str, WorkerRow] = {}
        self.job_events: Dict[str, list[dict[str, Any]]] = {}

    def create_job(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str],
        client_record_id: Optional[str],
        capture_origin: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
    ) -> JobRow:
        with self._lock:
            now = utcnow()
            job_id = f"job_{uuid4().hex}"
            row = JobRow(
                job_id=job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                client_record_id=client_record_id,
                capture_origin=capture_origin,
                input_file_name=file_name,
                input_content_type=content_type,
                input_size_bytes=size_bytes,
                input_storage_key=None,
                input_upload_etag=None,
                state=JobState.CREATED,
                stage=None,
                phase_name=None,
                current_tier=None,
                title="任务已创建",
                detail="请开始后台上传视频。",
                progress_fraction=0.0,
                progress_basis="created",
                elapsed_seconds=0,
                estimated_remaining_seconds=None,
                assigned_worker_id=None,
                artifact=None,
                failure_reason=None,
                failure_detail=None,
                created_at=now,
                updated_at=now,
            )
            self.jobs[job_id] = row
            return row

    def cancel_superseded_jobs(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str],
        client_record_id: Optional[str],
        capture_origin: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
        keep_job_id: str,
        failure_reason: str,
        detail: str,
    ) -> list[JobRow]:
        active_states = {
            JobState.CREATED,
            JobState.UPLOADING,
            JobState.UPLOADED,
            JobState.QUEUED,
            JobState.ASSIGNED,
            JobState.RECONSTRUCTING,
            JobState.TRAINING_PROBE,
            JobState.TRAINING_FULL,
            JobState.EXPORTING,
        }
        cancelled: list[JobRow] = []
        with self._lock:
            now = utcnow()
            for job in self.jobs.values():
                if job.job_id == keep_job_id:
                    continue
                if job.tenant_id != tenant_id or job.capture_origin != capture_origin:
                    continue
                if job.state not in active_states:
                    continue

                same_record = client_record_id and job.client_record_id == client_record_id
                same_upload = (
                    job.input_file_name == file_name
                    and job.input_content_type == content_type
                    and job.input_size_bytes == size_bytes
                    and ((user_id and job.user_id == user_id) or (not user_id and not job.user_id))
                )
                if not same_record and not same_upload:
                    continue

                job.state = JobState.CANCELLED
                job.title = "旧任务已被新上传替换"
                job.detail = detail
                job.failure_reason = failure_reason
                job.estimated_remaining_seconds = 0
                job.cancelled_at = now
                job.updated_at = now
                cancelled.append(job)
        return cancelled

    def get_job(self, job_id: str) -> Optional[JobRow]:
        with self._lock:
            return self.jobs.get(job_id)

    def get_latest_job_by_client_record_id(
        self,
        client_record_id: str,
        *,
        capture_origin: Optional[str] = None,
    ) -> Optional[JobRow]:
        normalized_client_record_id = str(client_record_id or "").strip()
        if not normalized_client_record_id:
            return None
        normalized_capture_origin = str(capture_origin or "").strip() or None
        with self._lock:
            matches = [
                row
                for row in self.jobs.values()
                if row.client_record_id == normalized_client_record_id
                and (normalized_capture_origin is None or row.capture_origin == normalized_capture_origin)
            ]
        if not matches:
            return None
        matches.sort(key=lambda row: (row.created_at, row.updated_at), reverse=True)
        return matches[0]

    def update_job(self, job_id: str, **changes: Any) -> JobRow:
        with self._lock:
            row = self.jobs[job_id]
            artifact = changes.pop("artifact", None) if "artifact" in changes else None
            artifact_present = "artifact" in changes or artifact is not None
            for key, value in changes.items():
                setattr(row, key, value)
            if artifact_present:
                row.artifact = artifact
            row.updated_at = utcnow()
            return row

    def append_job_event(self, job_id: str, *, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self.job_events.setdefault(job_id, []).append(
                {
                    "event_type": event_type,
                    "payload": payload,
                    "created_at": utcnow(),
                }
            )

    def list_job_events(self, job_id: str, *, event_type: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self.job_events.get(job_id, []))
        if event_type is None:
            return events
        return [event for event in events if event.get("event_type") == event_type]

    def _canonical_worker_id_for_payload(self, payload: dict[str, Any]) -> str:
        requested_worker_id = str(payload.get("worker_id") or "").strip()
        host_fingerprint = str(payload.get("host_fingerprint") or "").strip()
        instance_label = str(payload.get("instance_label") or "").strip()
        matching_rows = [
            worker
            for worker in self.workers.values()
            if worker.host_fingerprint == host_fingerprint
            and (not instance_label or worker.instance_label == instance_label)
        ]
        matching_rows.sort(
            key=lambda worker: (
                0 if requested_worker_id and worker.worker_id == requested_worker_id else 1,
                0 if worker.current_job_id else 1,
                -worker.last_heartbeat_at.timestamp(),
                -worker.updated_at.timestamp(),
            )
        )
        if matching_rows:
            return matching_rows[0].worker_id
        if requested_worker_id:
            return requested_worker_id
        return _stable_worker_id_for_payload(payload)

    def _retire_duplicate_workers(
        self,
        canonical_worker_id: str,
        host_fingerprint: str,
        instance_label: str,
        *,
        now: datetime,
    ) -> None:
        stale_heartbeat = now - timedelta(days=3650)
        for worker in self.workers.values():
            if worker.worker_id == canonical_worker_id:
                continue
            if worker.host_fingerprint != host_fingerprint:
                continue
            if instance_label and worker.instance_label != instance_label:
                continue
            if worker.current_job_id:
                continue
            worker.state = WorkerState.OFFLINE
            worker.last_heartbeat_at = stale_heartbeat
            worker.updated_at = now

    def _effective_worker_snapshot(
        self,
        worker: WorkerRow,
        *,
        gpu_count: Optional[int] = None,
        vram_mb: Optional[int] = None,
        disk_free_mb: Optional[int] = None,
        capability_flags: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "gpu_count": gpu_count if gpu_count is not None else worker.gpu_count,
            "vram_mb": vram_mb if vram_mb is not None else worker.vram_mb,
            "disk_free_mb": disk_free_mb if disk_free_mb is not None else worker.disk_free_mb,
            "capability_flags": _merged_capability_flags(worker.capability_flags, capability_flags),
        }

    def register_worker(self, **payload: Any) -> WorkerRow:
        with self._lock:
            self._reconcile_terminal_worker_assignments()
            self._reconcile_stale_job_reservations()
            now = utcnow()
            worker_id = self._canonical_worker_id_for_payload(payload)
            existing = self.workers.get(worker_id)
            row = WorkerRow(
                worker_id=worker_id,
                provider=payload["provider"],
                region=payload.get("region"),
                instance_label=payload["instance_label"],
                host_fingerprint=payload["host_fingerprint"],
                gpu_model=payload["gpu_model"],
                gpu_count=payload["gpu_count"],
                vram_mb=payload["vram_mb"],
                cpu_cores=payload.get("cpu_cores"),
                ram_mb=payload.get("ram_mb"),
                disk_free_mb=payload.get("disk_free_mb"),
                software_version=payload.get("software_version"),
                capability_flags=payload.get("capability_flags", {}),
                state=existing.state if existing and existing.current_job_id else WorkerState.IDLE,
                current_job_id=existing.current_job_id if existing else None,
                last_heartbeat_at=now,
                updated_at=now,
            )
            self.workers[worker_id] = row
            self._retire_duplicate_workers(worker_id, row.host_fingerprint, row.instance_label, now=now)
            return row

    def get_worker(self, worker_id: str) -> Optional[WorkerRow]:
        with self._lock:
            self._reconcile_terminal_worker_assignments()
            return self.workers.get(worker_id)

    def heartbeat_worker(self, worker_id: str, **changes: Any) -> WorkerRow:
        with self._lock:
            self._reconcile_terminal_worker_assignments()
            self._reconcile_stale_job_reservations()
            row = self.workers[worker_id]
            for key, value in changes.items():
                setattr(row, key, value)
            now = utcnow()
            row.last_heartbeat_at = now
            row.updated_at = now
            return row

    def reserve_idle_worker_for_job(
        self,
        job_id: str,
        *,
        pipeline_strategy: Optional[str] = None,
    ) -> Optional[WorkerRow]:
        with self._lock:
            self._reconcile_terminal_worker_assignments()
            self._reconcile_stale_job_reservations()
            job = self.jobs[job_id]
            if job.assigned_worker_id:
                existing_worker = self.workers.get(job.assigned_worker_id)
                if existing_worker and _worker_supports_pipeline_strategy(
                    existing_worker.capability_flags,
                    pipeline_strategy,
                ):
                    return existing_worker
                job.assigned_worker_id = None
                job.updated_at = utcnow()
            stale_cutoff = utcnow() - timedelta(seconds=60)
            candidate = next(
                (
                    worker
                    for worker in sorted(self.workers.values(), key=lambda item: item.last_heartbeat_at, reverse=True)
                    if worker.state == WorkerState.IDLE
                    and worker.current_job_id is None
                    and worker.last_heartbeat_at >= stale_cutoff
                    and _worker_meets_scheduler_requirements(**self._effective_worker_snapshot(worker))
                    and _worker_supports_pipeline_strategy(worker.capability_flags, pipeline_strategy)
                ),
                None,
            )
            if candidate is None:
                return None
            job.assigned_worker_id = candidate.worker_id
            job.updated_at = utcnow()
            return candidate

    def claim_next_job(
        self,
        worker_id: str,
        *,
        min_chunk_ready_bytes: int = 0,
        gpu_count: Optional[int] = None,
        vram_mb: Optional[int] = None,
        disk_free_mb: Optional[int] = None,
        capability_flags: Optional[dict[str, Any]] = None,
        pipeline_strategies: Optional[list[str]] = None,
    ) -> Optional[JobRow]:
        with self._lock:
            self._reconcile_terminal_worker_assignments()
            self._reconcile_stale_job_reservations()
            worker = self.workers.get(worker_id)
            if worker is None:
                return None
            if not _worker_meets_scheduler_requirements(
                **self._effective_worker_snapshot(
                    worker,
                    gpu_count=gpu_count,
                    vram_mb=vram_mb,
                    disk_free_mb=disk_free_mb,
                    capability_flags=capability_flags,
                )
            ):
                return None
            idle_jobs = sorted(
                (
                    job
                    for job in self.jobs.values()
                    if (
                        job.state in {JobState.UPLOADED, JobState.QUEUED}
                        or (
                            job.state == JobState.UPLOADING
                            and self._chunk_ready_bytes(job.job_id) >= min_chunk_ready_bytes > 0
                        )
                    )
                    and job.input_storage_key
                    and (job.assigned_worker_id is None or job.assigned_worker_id == worker_id)
                    and (
                        not pipeline_strategies
                        or _pipeline_strategy_from_events(self.job_events.get(job.job_id, [])) in pipeline_strategies
                    )
                ),
                key=lambda job: (
                    0 if job.assigned_worker_id == worker_id else 1,
                    0 if job.state == JobState.UPLOADING else 1,
                    job.created_at,
                ),
            )
            if not idle_jobs:
                return None
            job = idle_jobs[0]
            job.state = JobState.ASSIGNED
            job.assigned_worker_id = worker_id
            job.assigned_at = utcnow()
            job.title = "任务已分配"
            job.detail = "控制平面已把任务分配给可用 GPU worker。"
            job.updated_at = utcnow()
            worker.state = WorkerState.BUSY
            worker.current_job_id = job.job_id
            worker.updated_at = utcnow()
            return job

    def _chunk_ready_bytes(self, job_id: str) -> int:
        max_uploaded_bytes = 0
        for event in self.job_events.get(job_id, []):
            if event.get("event_type") != "chunk_part_ready":
                continue
            payload = event.get("payload") or {}
            try:
                max_uploaded_bytes = max(max_uploaded_bytes, int(payload.get("uploaded_bytes") or 0))
            except (TypeError, ValueError):
                continue
        return max_uploaded_bytes

    def _reconcile_terminal_worker_assignments(self) -> None:
        terminal_states = {JobState.COMPLETED, JobState.CANCELLED, JobState.FAILED}
        now = utcnow()
        for worker in self.workers.values():
            if not worker.current_job_id:
                continue
            job = self.jobs.get(worker.current_job_id)
            if job is None or job.state in terminal_states:
                worker.current_job_id = None
                worker.state = WorkerState.IDLE
                worker.updated_at = now

    def _reconcile_stale_job_reservations(self) -> None:
        reservable_states = {JobState.CREATED, JobState.UPLOADING, JobState.UPLOADED, JobState.QUEUED}
        stale_cutoff = utcnow() - timedelta(seconds=60)
        now = utcnow()
        for job in self.jobs.values():
            if job.state not in reservable_states or not job.assigned_worker_id:
                continue
            worker = self.workers.get(job.assigned_worker_id)
            if worker is None:
                job.assigned_worker_id = None
                job.updated_at = now
                continue
            if worker.last_heartbeat_at < stale_cutoff:
                job.assigned_worker_id = None
                job.updated_at = now
                continue
            if worker.current_job_id and worker.current_job_id != job.job_id:
                job.assigned_worker_id = None
                job.updated_at = now


_ARTIFACT_MISSING = object()


class PostgresRepository:
    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("database_url_required")
        if psycopg is None or Json is None:  # pragma: no cover - depends on installed deps
            raise RuntimeError("psycopg_not_installed")
        self.database_url = database_url

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _canonical_worker_id_for_payload(
        self,
        conn: "psycopg.Connection[Any]",
        payload: dict[str, Any],
    ) -> str:
        requested_worker_id = str(payload.get("worker_id") or "").strip()
        host_fingerprint = str(payload.get("host_fingerprint") or "").strip()
        instance_label = str(payload.get("instance_label") or "").strip()
        if host_fingerprint:
            record = conn.execute(
                """
                select worker_id
                from workers
                where host_fingerprint = %s
                  and (%s = '' or instance_label = %s)
                order by
                    case when %s <> '' and worker_id = %s then 0 else 1 end,
                    case when current_job_id is not null then 0 else 1 end,
                    last_heartbeat_at desc,
                    updated_at desc
                limit 1
                """,
                (host_fingerprint, instance_label, instance_label, requested_worker_id, requested_worker_id),
            ).fetchone()
            if record is not None:
                return record["worker_id"]
        if requested_worker_id:
            return requested_worker_id
        return _stable_worker_id_for_payload(payload)

    def _retire_duplicate_workers(
        self,
        conn: "psycopg.Connection[Any]",
        canonical_worker_id: str,
        host_fingerprint: str,
        instance_label: str,
        *,
        now: datetime,
    ) -> None:
        if not host_fingerprint:
            return
        conn.execute(
            """
            update workers
               set state = %s,
                   current_job_id = null,
                   last_heartbeat_at = %s,
                   updated_at = %s
             where host_fingerprint = %s
               and (%s = '' or instance_label = %s)
               and worker_id <> %s
               and current_job_id is null
            """,
            (
                WorkerState.OFFLINE.value,
                now - timedelta(days=3650),
                now,
                host_fingerprint,
                instance_label,
                instance_label,
                canonical_worker_id,
            ),
        )

    @staticmethod
    def _effective_worker_snapshot_from_record(
        record: dict[str, Any],
        *,
        gpu_count: Optional[int] = None,
        vram_mb: Optional[int] = None,
        disk_free_mb: Optional[int] = None,
        capability_flags: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        return {
            "gpu_count": gpu_count if gpu_count is not None else record.get("gpu_count"),
            "vram_mb": vram_mb if vram_mb is not None else record.get("vram_mb"),
            "disk_free_mb": disk_free_mb if disk_free_mb is not None else record.get("disk_free_mb"),
            "capability_flags": _merged_capability_flags(record.get("capability_flags"), capability_flags),
        }

    def _reconcile_terminal_worker_assignments(
        self,
        conn: "psycopg.Connection[Any]",
        *,
        worker_id: Optional[str] = None,
    ) -> None:
        terminal_states = [JobState.COMPLETED.value, JobState.CANCELLED.value, JobState.FAILED.value]
        params: list[Any] = [utcnow(), terminal_states]
        worker_filter = ""
        if worker_id:
            worker_filter = "and w.worker_id = %s"
            params.append(worker_id)
        conn.execute(
            f"""
            update workers w
               set state = %s,
                   current_job_id = null,
                   updated_at = %s
             where w.current_job_id is not null
               and exists (
                    select 1
                      from jobs j
                     where j.job_id = w.current_job_id
                       and j.state = any(%s::job_state[])
               )
               {worker_filter}
            """,
            (WorkerState.IDLE.value, *params),
        )

    def _ensure_tenant_and_user(self, conn: "psycopg.Connection[Any]", tenant_id: str, user_id: Optional[str]) -> None:
        conn.execute(
            """
            insert into tenants (tenant_id, name)
            values (%s, %s)
            on conflict (tenant_id) do nothing
            """,
            (tenant_id, tenant_id),
        )
        if user_id:
            conn.execute(
                """
                insert into users (user_id, tenant_id, external_id, display_name)
                values (%s, %s, %s, %s)
                on conflict (user_id) do update
                set tenant_id = excluded.tenant_id,
                    external_id = excluded.external_id,
                    display_name = excluded.display_name
                """,
                (user_id, tenant_id, user_id, user_id),
            )

    def create_job(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str],
        client_record_id: Optional[str],
        capture_origin: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
    ) -> JobRow:
        job_id = f"job_{uuid4().hex}"
        now = utcnow()
        with self._connect() as conn:
            self._ensure_tenant_and_user(conn, tenant_id, user_id)
            conn.execute(
                """
                insert into jobs (
                    job_id, tenant_id, user_id, client_record_id, capture_origin,
                    input_file_name, input_content_type, input_size_bytes,
                    state, title, detail, progress_fraction, progress_basis,
                    elapsed_seconds, created_at, updated_at
                ) values (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    job_id,
                    tenant_id,
                    user_id,
                    client_record_id,
                    capture_origin,
                    file_name,
                    content_type,
                    size_bytes,
                    JobState.CREATED.value,
                    "任务已创建",
                    "请开始后台上传视频。",
                    0.0,
                    "created",
                    0,
                    now,
                    now,
                ),
            )
            conn.commit()
            record = conn.execute("select * from jobs where job_id = %s", (job_id,)).fetchone()
        assert record is not None
        return self._job_row_from_record(record)

    def cancel_superseded_jobs(
        self,
        *,
        tenant_id: str,
        user_id: Optional[str],
        client_record_id: Optional[str],
        capture_origin: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
        keep_job_id: str,
        failure_reason: str,
        detail: str,
    ) -> list[JobRow]:
        now = utcnow()
        active_states = (
            JobState.CREATED.value,
            JobState.UPLOADING.value,
            JobState.UPLOADED.value,
            JobState.QUEUED.value,
            JobState.ASSIGNED.value,
            JobState.RECONSTRUCTING.value,
            JobState.TRAINING_PROBE.value,
            JobState.TRAINING_FULL.value,
            JobState.EXPORTING.value,
        )
        match_by_client_record = client_record_id is not None and client_record_id.strip() != ""
        match_by_user_id = user_id is not None and user_id.strip() != ""
        with self._connect() as conn:
            if match_by_client_record:
                records = conn.execute(
                    """
                    update jobs
                    set state = %s,
                        title = %s,
                        detail = %s,
                        failure_reason = %s,
                        estimated_remaining_seconds = %s,
                        cancelled_at = %s,
                        updated_at = %s
                    where job_id <> %s
                      and tenant_id = %s::text
                      and capture_origin = %s::text
                      and state = any(%s::job_state[])
                      and client_record_id = %s::text
                    returning *
                    """,
                    (
                        JobState.CANCELLED.value,
                        "旧任务已被新上传替换",
                        detail,
                        failure_reason,
                        0,
                        now,
                        now,
                        keep_job_id,
                        tenant_id,
                        capture_origin,
                        list(active_states),
                        client_record_id,
                    ),
                ).fetchall()
            else:
                user_clause = "user_id = %s::text" if match_by_user_id else "user_id is null"
                params: tuple[Any, ...]
                if match_by_user_id:
                    params = (
                        JobState.CANCELLED.value,
                        "旧任务已被新上传替换",
                        detail,
                        failure_reason,
                        0,
                        now,
                        now,
                        keep_job_id,
                        tenant_id,
                        capture_origin,
                        list(active_states),
                        file_name,
                        content_type,
                        size_bytes,
                        user_id,
                    )
                else:
                    params = (
                        JobState.CANCELLED.value,
                        "旧任务已被新上传替换",
                        detail,
                        failure_reason,
                        0,
                        now,
                        now,
                        keep_job_id,
                        tenant_id,
                        capture_origin,
                        list(active_states),
                        file_name,
                        content_type,
                        size_bytes,
                    )
                records = conn.execute(
                    f"""
                    update jobs
                    set state = %s,
                        title = %s,
                        detail = %s,
                        failure_reason = %s,
                        estimated_remaining_seconds = %s,
                        cancelled_at = %s,
                        updated_at = %s
                    where job_id <> %s
                      and tenant_id = %s::text
                      and capture_origin = %s::text
                      and state = any(%s::job_state[])
                      and input_file_name = %s::text
                      and input_content_type = %s::text
                      and input_size_bytes = %s::bigint
                      and {user_clause}
                    returning *
                    """,
                    params,
                ).fetchall()
            conn.commit()
        return [self._job_row_from_record(record) for record in records]

    def get_job(self, job_id: str) -> Optional[JobRow]:
        with self._connect() as conn:
            record = conn.execute("select * from jobs where job_id = %s", (job_id,)).fetchone()
        if record is None:
            return None
        return self._job_row_from_record(record)

    def get_latest_job_by_client_record_id(
        self,
        client_record_id: str,
        *,
        capture_origin: Optional[str] = None,
    ) -> Optional[JobRow]:
        normalized_client_record_id = str(client_record_id or "").strip()
        if not normalized_client_record_id:
            return None
        normalized_capture_origin = str(capture_origin or "").strip() or None
        with self._connect() as conn:
            if normalized_capture_origin is None:
                record = conn.execute(
                    """
                    select *
                    from jobs
                    where client_record_id = %s::text
                    order by created_at desc, updated_at desc
                    limit 1
                    """,
                    (normalized_client_record_id,),
                ).fetchone()
            else:
                record = conn.execute(
                    """
                    select *
                    from jobs
                    where client_record_id = %s::text
                      and capture_origin = %s::text
                    order by created_at desc, updated_at desc
                    limit 1
                    """,
                    (normalized_client_record_id, normalized_capture_origin),
                ).fetchone()
        if record is None:
            return None
        return self._job_row_from_record(record)

    def update_job(self, job_id: str, **changes: Any) -> JobRow:
        if not changes:
            job = self.get_job(job_id)
            if not job:
                raise KeyError(job_id)
            return job

        artifact = changes.pop("artifact", _ARTIFACT_MISSING)
        known_columns = {
            "input_storage_key",
            "input_upload_etag",
            "input_size_bytes",
            "state",
            "stage",
            "phase_name",
            "current_tier",
            "title",
            "detail",
            "progress_fraction",
            "progress_basis",
            "elapsed_seconds",
            "estimated_remaining_seconds",
            "assigned_worker_id",
            "failure_reason",
            "failure_detail",
            "upload_completed_at",
            "assigned_at",
            "started_at",
            "completed_at",
            "cancelled_at",
        }

        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in known_columns:
                raise ValueError(f"unsupported_job_column:{key}")
            assignments.append(f"{key} = %s")
            values.append(self._db_value(value))

        if artifact is not _ARTIFACT_MISSING:
            if artifact is None:
                assignments.extend(
                    [
                        "artifact_manifest = %s",
                        "artifact_primary_storage_key = %s",
                        "preview_storage_key = %s",
                        "metrics_storage_key = %s",
                        "artifact_manifest_storage_key = %s",
                    ]
                )
                values.extend([None, None, None, None, None])
            else:
                artifact_payload = artifact.model_dump(mode="json")
                assignments.extend(
                    [
                        "artifact_manifest = %s",
                        "artifact_primary_storage_key = %s",
                        "preview_storage_key = %s",
                        "metrics_storage_key = %s",
                        "artifact_manifest_storage_key = %s",
                    ]
                )
                values.extend(
                    [
                        Json(artifact_payload),
                        artifact.primary_artifact.storage_key if artifact.primary_artifact else None,
                        artifact.preview.storage_key if artifact.preview else None,
                        artifact.metrics.storage_key if artifact.metrics else None,
                        artifact.viewer_manifest.storage_key if artifact.viewer_manifest else None,
                    ]
                )

        assignments.append("updated_at = %s")
        values.append(utcnow())
        values.append(job_id)

        with self._connect() as conn:
            conn.execute(f"update jobs set {', '.join(assignments)} where job_id = %s", tuple(values))
            if artifact is not _ARTIFACT_MISSING:
                self._replace_artifacts(conn, job_id, artifact if artifact is not _ARTIFACT_MISSING else None)
            conn.commit()
            record = conn.execute("select * from jobs where job_id = %s", (job_id,)).fetchone()
        if record is None:
            raise KeyError(job_id)
        return self._job_row_from_record(record)

    def append_job_event(self, job_id: str, *, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                insert into job_events (job_id, event_type, payload)
                values (%s, %s, %s)
                """,
                (job_id, event_type, Json(payload)),
            )
            conn.commit()

    def list_job_events(self, job_id: str, *, event_type: Optional[str] = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if event_type is None:
                records = conn.execute(
                    """
                    select event_type, payload, created_at
                    from job_events
                    where job_id = %s
                    order by created_at asc, event_id asc
                    """,
                    (job_id,),
                ).fetchall()
            else:
                records = conn.execute(
                    """
                    select event_type, payload, created_at
                    from job_events
                    where job_id = %s and event_type = %s
                    order by created_at asc, event_id asc
                    """,
                    (job_id, event_type),
                ).fetchall()
        return [dict(record) for record in records]

    def register_worker(self, **payload: Any) -> WorkerRow:
        now = utcnow()
        with self._connect() as conn:
            self._reconcile_terminal_worker_assignments(conn)
            self._reconcile_stale_job_reservations(conn)
            worker_id = self._canonical_worker_id_for_payload(conn, payload)
            conn.execute(
                """
                insert into workers (
                    worker_id, provider, region, instance_label, host_fingerprint,
                    gpu_model, gpu_count, vram_mb, cpu_cores, ram_mb,
                    disk_free_mb, software_version, capability_flags, state,
                    current_job_id, last_heartbeat_at, created_at, updated_at
                ) values (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                on conflict (worker_id) do update
                set provider = excluded.provider,
                    region = excluded.region,
                    instance_label = excluded.instance_label,
                    host_fingerprint = excluded.host_fingerprint,
                    gpu_model = excluded.gpu_model,
                    gpu_count = excluded.gpu_count,
                    vram_mb = excluded.vram_mb,
                    cpu_cores = excluded.cpu_cores,
                    ram_mb = excluded.ram_mb,
                    disk_free_mb = excluded.disk_free_mb,
                    software_version = excluded.software_version,
                    capability_flags = excluded.capability_flags,
                    state = case
                        when workers.current_job_id is not null then workers.state
                        else excluded.state
                    end,
                    current_job_id = coalesce(workers.current_job_id, excluded.current_job_id),
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    updated_at = excluded.updated_at
                """,
                (
                    worker_id,
                    payload["provider"],
                    payload.get("region"),
                    payload["instance_label"],
                    payload["host_fingerprint"],
                    payload["gpu_model"],
                    payload["gpu_count"],
                    payload["vram_mb"],
                    payload.get("cpu_cores"),
                    payload.get("ram_mb"),
                    payload.get("disk_free_mb"),
                    payload.get("software_version"),
                    Json(payload.get("capability_flags", {})),
                    WorkerState.IDLE.value,
                    None,
                    now,
                    now,
                    now,
                ),
            )
            self._retire_duplicate_workers(
                conn,
                worker_id,
                payload["host_fingerprint"],
                payload["instance_label"],
                now=now,
            )
            conn.commit()
            record = conn.execute("select * from workers where worker_id = %s", (worker_id,)).fetchone()
        assert record is not None
        return self._worker_row_from_record(record)

    def get_worker(self, worker_id: str) -> Optional[WorkerRow]:
        with self._connect() as conn:
            self._reconcile_terminal_worker_assignments(conn, worker_id=worker_id)
            record = conn.execute("select * from workers where worker_id = %s", (worker_id,)).fetchone()
        if record is None:
            return None
        return self._worker_row_from_record(record)

    def heartbeat_worker(self, worker_id: str, **changes: Any) -> WorkerRow:
        now = utcnow()
        gpu_util = changes.pop("gpu_util", None)
        gpu_mem_used_mb = changes.pop("gpu_mem_used_mb", None)
        cpu_util = changes.pop("cpu_util", None)
        disk_free_mb_for_heartbeat = changes.get("disk_free_mb")

        known_columns = {"state", "current_job_id", "disk_free_mb"}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in known_columns:
                continue
            assignments.append(f"{key} = %s")
            values.append(self._db_value(value))
        assignments.extend(["last_heartbeat_at = %s", "updated_at = %s"])
        values.extend([now, now, worker_id])

        with self._connect() as conn:
            self._reconcile_terminal_worker_assignments(conn, worker_id=worker_id)
            self._reconcile_stale_job_reservations(conn)
            conn.execute(
                f"update workers set {', '.join(assignments)} where worker_id = %s",
                tuple(values),
            )
            conn.execute(
                """
                insert into worker_heartbeats (
                    worker_id, state, current_job_id,
                    gpu_util, gpu_mem_used_mb, cpu_util, disk_free_mb, payload
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    worker_id,
                    changes.get("state", WorkerState.IDLE).value
                    if isinstance(changes.get("state"), WorkerState)
                    else changes.get("state", WorkerState.IDLE.value),
                    changes.get("current_job_id"),
                    gpu_util,
                    gpu_mem_used_mb,
                    cpu_util,
                    disk_free_mb_for_heartbeat,
                    Json(
                        {
                            "gpu_util": gpu_util,
                            "gpu_mem_used_mb": gpu_mem_used_mb,
                            "cpu_util": cpu_util,
                            "disk_free_mb": disk_free_mb_for_heartbeat,
                        }
                    ),
                ),
            )
            conn.commit()
            record = conn.execute("select * from workers where worker_id = %s", (worker_id,)).fetchone()
        if record is None:
            raise KeyError(worker_id)
        return self._worker_row_from_record(record)

    def reserve_idle_worker_for_job(
        self,
        job_id: str,
        *,
        pipeline_strategy: Optional[str] = None,
    ) -> Optional[WorkerRow]:
        now = utcnow()
        with self._connect() as conn:
            self._reconcile_terminal_worker_assignments(conn)
            self._reconcile_stale_job_reservations(conn)
            existing = conn.execute("select assigned_worker_id from jobs where job_id = %s", (job_id,)).fetchone()
            if existing is None:
                raise KeyError(job_id)
            if existing.get("assigned_worker_id"):
                worker_record = conn.execute(
                    "select * from workers where worker_id = %s",
                    (existing["assigned_worker_id"],),
                ).fetchone()
                if worker_record and _worker_supports_pipeline_strategy(
                    worker_record.get("capability_flags"),
                    pipeline_strategy,
                ):
                    conn.commit()
                    return self._worker_row_from_record(worker_record)
                conn.execute(
                    """
                    update jobs
                    set assigned_worker_id = null,
                        updated_at = %s
                    where job_id = %s
                    """,
                    (now, job_id),
                )

            candidate_records = conn.execute(
                """
                select *
                from workers
                where state = 'idle'
                  and current_job_id is null
                  and last_heartbeat_at >= %s
                order by last_heartbeat_at desc, updated_at desc
                for update skip locked
                limit 64
                """,
                (now - timedelta(seconds=60),),
            ).fetchall()
            worker_record = next(
                (
                    record
                    for record in candidate_records
                    if _worker_meets_scheduler_requirements(
                        **self._effective_worker_snapshot_from_record(record)
                    )
                    and _worker_supports_pipeline_strategy(
                        record.get("capability_flags"),
                        pipeline_strategy,
                    )
                ),
                None,
            )
            if worker_record is None:
                conn.commit()
                return None

            conn.execute(
                """
                update jobs
                set assigned_worker_id = %s,
                    updated_at = %s
                where job_id = %s
                  and assigned_worker_id is null
                """,
                (worker_record["worker_id"], now, job_id),
            )
            conn.commit()
        return self._worker_row_from_record(worker_record)

    def claim_next_job(
        self,
        worker_id: str,
        *,
        min_chunk_ready_bytes: int = 0,
        gpu_count: Optional[int] = None,
        vram_mb: Optional[int] = None,
        disk_free_mb: Optional[int] = None,
        capability_flags: Optional[dict[str, Any]] = None,
        pipeline_strategies: Optional[list[str]] = None,
    ) -> Optional[JobRow]:
        with self._connect() as conn:
            self._reconcile_terminal_worker_assignments(conn, worker_id=worker_id)
            self._reconcile_stale_job_reservations(conn)
            worker_record = conn.execute(
                """
                select *
                from workers
                where worker_id = %s
                for update
                """,
                (worker_id,),
            ).fetchone()
            if worker_record is None:
                conn.commit()
                return None
            if not _worker_meets_scheduler_requirements(
                **self._effective_worker_snapshot_from_record(
                    worker_record,
                    gpu_count=gpu_count,
                    vram_mb=vram_mb,
                    disk_free_mb=disk_free_mb,
                    capability_flags=capability_flags,
                )
            ):
                conn.commit()
                return None
            records = conn.execute(
                """
                select *
                from jobs
                where (
                    state in ('uploaded', 'queued')
                    or (
                        state = 'uploading'
                        and %s > 0
                        and exists (
                            select 1
                            from job_events init_evt
                            where init_evt.job_id = jobs.job_id
                              and init_evt.event_type = 'chunked_upload_initialized'
                        )
                        and coalesce((
                            select max((ready_evt.payload->>'uploaded_bytes')::bigint)
                            from job_events ready_evt
                            where ready_evt.job_id = jobs.job_id
                              and ready_evt.event_type = 'chunk_part_ready'
                        ), 0) >= %s
                    )
                )
                  and input_storage_key is not null
                  and (assigned_worker_id is null or assigned_worker_id = %s)
                order by
                    case when assigned_worker_id = %s then 0 else 1 end,
                    case when state = 'uploading' then 0 else 1 end,
                    created_at asc
                for update skip locked
                limit 24
                """
                ,
                (min_chunk_ready_bytes, min_chunk_ready_bytes, worker_id, worker_id),
            ).fetchall()
            if not records:
                conn.commit()
                return None

            allowed_strategies = {
                _normalize_pipeline_strategy(item)
                for item in (pipeline_strategies or [])
                if _normalize_pipeline_strategy(item)
            }

            record = None
            for candidate in records:
                if not allowed_strategies:
                    record = candidate
                    break
                strategy_record = conn.execute(
                    """
                    select payload
                    from job_events
                    where job_id = %s
                      and event_type = 'pipeline_profile_requested'
                    order by created_at desc
                    limit 1
                    """,
                    (candidate["job_id"],),
                ).fetchone()
                strategy = "autofallback"
                if strategy_record and isinstance(strategy_record.get("payload"), dict):
                    strategy = _normalize_pipeline_strategy(strategy_record["payload"].get("strategy"))
                if strategy in allowed_strategies:
                    record = candidate
                    break

            if record is None:
                conn.commit()
                return None

            now = utcnow()
            conn.execute(
                """
                update jobs
                set state = %s,
                    assigned_worker_id = %s,
                    assigned_at = %s,
                    title = %s,
                    detail = %s,
                    updated_at = %s
                where job_id = %s
                """,
                (
                    JobState.ASSIGNED.value,
                    worker_id,
                    now,
                    "任务已分配",
                    "控制平面已把任务分配给可用 GPU worker。",
                    now,
                    record["job_id"],
                ),
            )
            conn.execute(
                """
                update workers
                set state = %s,
                    current_job_id = %s,
                    updated_at = %s
                where worker_id = %s
                """,
                (
                    WorkerState.BUSY.value,
                    record["job_id"],
                    now,
                    worker_id,
                ),
            )
            conn.commit()
            latest = conn.execute("select * from jobs where job_id = %s", (record["job_id"],)).fetchone()
        assert latest is not None
        return self._job_row_from_record(latest)

    def _reconcile_stale_job_reservations(self, conn: "psycopg.Connection[Any]") -> None:
        stale_cutoff = utcnow() - timedelta(seconds=60)
        conn.execute(
            """
            update jobs j
               set assigned_worker_id = null,
                   updated_at = %s
             where j.assigned_worker_id is not null
               and j.state = any(%s::job_state[])
               and (
                    not exists (
                        select 1
                          from workers w
                         where w.worker_id = j.assigned_worker_id
                    )
                    or exists (
                        select 1
                          from workers w
                         where w.worker_id = j.assigned_worker_id
                           and (
                                w.last_heartbeat_at < %s
                                or (w.current_job_id is not null and w.current_job_id <> j.job_id)
                           )
                    )
               )
            """,
            (
                utcnow(),
                [
                    JobState.CREATED.value,
                    JobState.UPLOADING.value,
                    JobState.UPLOADED.value,
                    JobState.QUEUED.value,
                ],
                stale_cutoff,
            ),
        )

    def _replace_artifacts(
        self,
        conn: "psycopg.Connection[Any]",
        job_id: str,
        artifact: Optional[ArtifactManifest],
    ) -> None:
        conn.execute("delete from artifacts where job_id = %s", (job_id,))
        if artifact is None:
            return

        rows: list[tuple[str, str, Optional[int], Optional[str]]] = []
        if artifact.primary_artifact:
            rows.append(
                (
                    "primary_artifact",
                    artifact.primary_artifact.storage_key,
                    artifact.primary_artifact.size_bytes,
                    artifact.primary_artifact.checksum_sha256,
                )
            )
        if artifact.preview:
            rows.append(
                (
                    "preview",
                    artifact.preview.storage_key,
                    artifact.preview.size_bytes,
                    artifact.preview.checksum_sha256,
                )
            )
        if artifact.metrics:
            rows.append(
                (
                    "metrics",
                    artifact.metrics.storage_key,
                    artifact.metrics.size_bytes,
                    artifact.metrics.checksum_sha256,
                )
            )
        if artifact.viewer_manifest:
            rows.append(
                (
                    "viewer_manifest",
                    artifact.viewer_manifest.storage_key,
                    artifact.viewer_manifest.size_bytes,
                    artifact.viewer_manifest.checksum_sha256,
                )
            )
        if artifact.comparison_asset:
            rows.append(
                (
                    "comparison_asset",
                    artifact.comparison_asset.storage_key,
                    artifact.comparison_asset.size_bytes,
                    artifact.comparison_asset.checksum_sha256,
                )
            )
        if artifact.comparison_metrics:
            rows.append(
                (
                    "comparison_metrics",
                    artifact.comparison_metrics.storage_key,
                    artifact.comparison_metrics.size_bytes,
                    artifact.comparison_metrics.checksum_sha256,
                )
            )

        for artifact_type, storage_key, size_bytes, checksum in rows:
            conn.execute(
                """
                insert into artifacts (
                    job_id, artifact_type, storage_key, size_bytes, checksum_sha256, created_at
                ) values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    job_id,
                    artifact_type,
                    storage_key,
                    size_bytes,
                    checksum,
                    utcnow(),
                ),
            )

    @staticmethod
    def _db_value(value: Any) -> Any:
        if isinstance(value, JobState):
            return value.value
        if isinstance(value, WorkerState):
            return value.value
        return value

    @staticmethod
    def _job_row_from_record(record: Dict[str, Any]) -> JobRow:
        artifact_payload = record.get("artifact_manifest")
        artifact = ArtifactManifest.model_validate(artifact_payload) if artifact_payload else None
        return JobRow(
            job_id=record["job_id"],
            tenant_id=record["tenant_id"],
            user_id=record.get("user_id"),
            client_record_id=record.get("client_record_id"),
            capture_origin=record["capture_origin"],
            input_file_name=record["input_file_name"],
            input_content_type=record["input_content_type"],
            input_size_bytes=record["input_size_bytes"],
            input_storage_key=record.get("input_storage_key"),
            input_upload_etag=record.get("input_upload_etag"),
            state=JobState(record["state"]),
            stage=record.get("stage"),
            phase_name=record.get("phase_name"),
            current_tier=record.get("current_tier"),
            title=record["title"],
            detail=record["detail"],
            progress_fraction=record.get("progress_fraction"),
            progress_basis=record.get("progress_basis"),
            elapsed_seconds=record.get("elapsed_seconds"),
            estimated_remaining_seconds=record.get("estimated_remaining_seconds"),
            assigned_worker_id=record.get("assigned_worker_id"),
            artifact=artifact,
            failure_reason=record.get("failure_reason"),
            failure_detail=record.get("failure_detail"),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            upload_completed_at=record.get("upload_completed_at"),
            assigned_at=record.get("assigned_at"),
            started_at=record.get("started_at"),
            completed_at=record.get("completed_at"),
            cancelled_at=record.get("cancelled_at"),
        )

    @staticmethod
    def _worker_row_from_record(record: Dict[str, Any]) -> WorkerRow:
        return WorkerRow(
            worker_id=record["worker_id"],
            provider=record["provider"],
            region=record.get("region"),
            instance_label=record["instance_label"],
            host_fingerprint=record["host_fingerprint"],
            gpu_model=record["gpu_model"],
            gpu_count=record["gpu_count"],
            vram_mb=record["vram_mb"],
            cpu_cores=record.get("cpu_cores"),
            ram_mb=record.get("ram_mb"),
            disk_free_mb=record.get("disk_free_mb"),
            software_version=record.get("software_version"),
            capability_flags=record.get("capability_flags") or {},
            state=WorkerState(record["state"]),
            current_job_id=record.get("current_job_id"),
            last_heartbeat_at=record["last_heartbeat_at"],
            updated_at=record["updated_at"],
        )
