from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import signal
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from requests import exceptions as requests_exceptions

from .config import config

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:  # pragma: no cover - depends on runtime deps
    boto3 = None
    BotoConfig = None


@dataclass(frozen=True)
class Snapshot:
    state: str
    stage: str
    phase_name: Optional[str]
    current_tier: Optional[str]
    title: str
    detail: str
    progress_fraction: Optional[float]
    progress_basis: Optional[str]
    elapsed_seconds: Optional[int]
    estimated_remaining_seconds: Optional[int]
    metrics: dict[str, Any] = field(default_factory=dict)
    failure_reason: Optional[str] = None
    failure_detail: Optional[str] = None
    artifact_path: Optional[str] = None
    summary_path: Optional[str] = None
    verdict_path: Optional[str] = None
    cancel_active_run: bool = False


@dataclass(frozen=True)
class TierPlan:
    name: str
    prep_mode: str
    stride: Optional[int] = None
    max_frames: Optional[int] = None
    matcher: Optional[str] = None


@dataclass
class ActiveJobRun:
    assignment: dict[str, Any]
    run_name: str
    local_input_path: Path
    tier_plan: list[TierPlan]
    phase: str = "prep_pending"
    tier_index: int = 0
    process: Optional[subprocess.Popen[Any]] = None
    wrapper_log_path: Optional[Path] = None
    last_runtime_push: Optional[tuple[Any, ...]] = None
    cancel_poll_counter: int = 0
    prep_ready_payload: Optional[dict[str, Any]] = None
    recovered: bool = False
    staged_primary_artifact: Optional[dict[str, Any]] = None
    staged_primary_artifact_path: Optional[str] = None
    staged_primary_artifact_signature: Optional[tuple[int, int]] = None
    pending_primary_artifact_signature: Optional[tuple[int, int]] = None
    pending_primary_artifact_stable_polls: int = 0
    staged_metrics_artifact: Optional[dict[str, Any]] = None
    staged_metrics_artifact_path: Optional[str] = None
    staged_metrics_artifact_signature: Optional[tuple[int, int]] = None
    pending_metrics_artifact_signature: Optional[tuple[int, int]] = None
    pending_metrics_artifact_stable_polls: int = 0
    staged_viewer_manifest_artifact: Optional[dict[str, Any]] = None
    staged_viewer_manifest_artifact_path: Optional[str] = None
    staged_viewer_manifest_artifact_signature: Optional[tuple[int, int]] = None
    pending_viewer_manifest_artifact_signature: Optional[tuple[int, int]] = None
    pending_viewer_manifest_artifact_stable_polls: int = 0
    published_artifact_manifest_signature: Optional[tuple[tuple[str, str], ...]] = None

    @property
    def job_id(self) -> str:
        return str(self.assignment["job_id"])

    @property
    def current_tier(self) -> TierPlan:
        return self.tier_plan[self.tier_index]


class AssignmentCancelledError(RuntimeError):
    pass


FRAME_SAMPLING_TIER_PRESETS: dict[str, TierPlan] = {
    "full": TierPlan(
        name="official_default",
        prep_mode="official_default",
    ),
}

TIER_NAME_TO_SAMPLING_PROFILE = {
    preset.name: profile
    for profile, preset in FRAME_SAMPLING_TIER_PRESETS.items()
}

SUPPORTED_TIER_NAMES = tuple(TIER_NAME_TO_SAMPLING_PROFILE.keys())


class ControlPlaneClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.max_attempts = 4

    def _json(self, method: str, path: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                    timeout=config.control_plane_timeout_sec,
                )
                response.raise_for_status()
                if not response.content:
                    return {}
                return response.json()
            except requests_exceptions.RequestException as error:
                last_error = error
                response = getattr(error, "response", None)
                should_retry = response is None or response.status_code >= 500
                if attempt >= self.max_attempts or not should_retry:
                    raise
                time.sleep(min(5, attempt))
        if last_error is not None:
            raise last_error
        return {}

    @staticmethod
    def _capability_flags() -> dict[str, bool]:
        return {
            "hislam2": True,
            "3dgs": True,
            "probe_gate": False,
            "worker_pull": True,
        }

    def register(self, *, worker_id: Optional[str] = None) -> dict[str, Any]:
        payload = {
            "provider": config.provider,
            "region": config.region,
            "instance_label": config.instance_label,
            "host_fingerprint": config.host_fingerprint or self._default_host_fingerprint(),
            "gpu_model": config.gpu_model,
            "gpu_count": config.gpu_count,
            "vram_mb": config.vram_mb,
            "cpu_cores": config.cpu_cores,
            "ram_mb": config.ram_mb,
            "disk_free_mb": self._disk_free_mb(),
            "software_version": config.software_version,
            "capability_flags": self._capability_flags(),
        }
        if worker_id:
            payload["worker_id"] = worker_id
        return self._json(
            "POST",
            "/v1/workers/register",
            payload,
        )

    def heartbeat(self, worker_id: str, *, state: str, current_job_id: Optional[str]) -> None:
        self._json(
            "POST",
            f"/v1/workers/{worker_id}/heartbeat",
            {
                "state": state,
                "gpu_util": None,
                "gpu_mem_used_mb": None,
                "cpu_util": None,
                "disk_free_mb": self._disk_free_mb(),
                "current_job_id": current_job_id,
            },
        )

    def claim_next(self, worker_id: str) -> Optional[dict[str, Any]]:
        payload = self._json(
            "POST",
            f"/v1/workers/{worker_id}/claim-next",
            {
                "max_concurrent_jobs": 1,
                "gpu_model": config.gpu_model,
                "gpu_count": config.gpu_count,
                "vram_mb": config.vram_mb,
                "disk_free_mb": self._disk_free_mb(),
                "capability_flags": self._capability_flags(),
            },
        )
        return payload.get("assignment")

    def runtime(self, job_id: str, payload: dict[str, Any]) -> None:
        self._json("POST", f"/v1/jobs/{job_id}/runtime", payload)

    def upload_artifact_manifest(self, job_id: str, payload: dict[str, Any]) -> None:
        self._json("POST", f"/v1/jobs/{job_id}/artifact-manifest", payload)

    def complete(self, job_id: str, worker_id: str, title: str, detail: str) -> None:
        self._json(
            "POST",
            f"/v1/jobs/{job_id}/complete",
            {"worker_id": worker_id, "title": title, "detail": detail},
        )

    def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        reason: str,
        detail: str,
        stage: Optional[str] = None,
        phase_name: Optional[str] = None,
    ) -> None:
        self._json(
            "POST",
            f"/v1/jobs/{job_id}/fail",
            {
                "worker_id": worker_id,
                "failure_reason": reason,
                "failure_detail": detail,
                "stage": stage,
                "phase_name": phase_name,
            },
        )

    def cancel_ack(self, job_id: str, worker_id: str) -> None:
        self._json("POST", f"/v1/jobs/{job_id}/cancel-ack", {"worker_id": worker_id})

    def fetch_job(self, job_id: str) -> dict[str, Any]:
        return self._json("GET", f"/v1/jobs/{job_id}")

    @staticmethod
    def _default_host_fingerprint() -> str:
        return f"{socket.gethostname()}:{socket.getfqdn()}"

    @staticmethod
    def _disk_free_mb() -> int:
        usage = shutil.disk_usage(config.local_root)
        return int(usage.free // (1024 * 1024))


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
                    if chunk:
                        handle.write(chunk)
                        bytes_written += len(chunk)
                        if progress_callback is not None:
                            progress_callback(bytes_written, total_bytes)

    def download_chunk_stream_to_path(
        self,
        manifest_url: str,
        target_path: Path,
        progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
        should_abort: Optional[Callable[[int, Optional[int]], bool]] = None,
    ) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        next_chunk_number = 1
        bytes_written = 0

        with target_path.open("wb") as handle:
            while True:
                if should_abort is not None and should_abort(bytes_written, None):
                    raise AssignmentCancelledError("chunk_stream_cancelled")
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
                            if should_abort is not None and should_abort(bytes_written, total_size_int):
                                raise AssignmentCancelledError("chunk_stream_cancelled")
                    next_chunk_number += 1

                if upload_completed and total_chunks > 0 and next_chunk_number > total_chunks:
                    return
                if not progressed:
                    if should_abort is not None and should_abort(bytes_written, total_size_int):
                        raise AssignmentCancelledError("chunk_stream_cancelled")
                    time.sleep(max(0.5, config.chunk_manifest_poll_interval_sec))

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

    def delete_object(self, storage_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=storage_key)

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


class WorkerAgent:
    def __init__(self, client: ControlPlaneClient, storage: ObjectStorageClient) -> None:
        self.client = client
        self.storage = storage
        self.worker_id: Optional[str] = None
        self.heartbeat_interval_sec = 15
        self.pull_interval_sec = 5
        self.runtime_directory = Path(config.runtime_directory)
        self.runtime_directory.mkdir(parents=True, exist_ok=True)
        self.liveness_heartbeat_file = Path(config.liveness_heartbeat_file)
        self.worker_identity_file = self.runtime_directory / "worker_identity.json"
        self.active_runs: dict[str, ActiveJobRun] = {}

    def bootstrap(self) -> None:
        payload = self.client.register(worker_id=self._load_persisted_worker_id())
        self.worker_id = payload["worker_id"]
        self._persist_worker_id()
        self.heartbeat_interval_sec = config.heartbeat_interval_override_sec or payload["heartbeat_interval_sec"]
        self.pull_interval_sec = config.pull_interval_override_sec or payload["pull_interval_sec"]
        self._mark_alive(state="bootstrapped", current_job_id=None)

    def _load_persisted_worker_id(self) -> Optional[str]:
        try:
            payload = json.loads(self.worker_identity_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None
        worker_id = str(payload.get("worker_id") or "").strip()
        if not worker_id:
            return None
        expected_host_fingerprint = config.host_fingerprint or self.client._default_host_fingerprint()
        if str(payload.get("host_fingerprint") or "").strip() != expected_host_fingerprint:
            return None
        if str(payload.get("instance_label") or "").strip() != config.instance_label:
            return None
        return worker_id

    def _persist_worker_id(self) -> None:
        if not self.worker_id:
            return
        payload = {
            "worker_id": self.worker_id,
            "host_fingerprint": config.host_fingerprint or self.client._default_host_fingerprint(),
            "instance_label": config.instance_label,
            "provider": config.provider,
            "region": config.region,
        }
        self.worker_identity_file.write_text(json.dumps(payload, ensure_ascii=True, sort_keys=True), encoding="utf-8")

    def run_forever(self) -> None:
        if not self.worker_id:
            self.bootstrap()
        assert self.worker_id is not None
        self._resume_local_runs_after_restart()

        last_heartbeat = 0.0
        while True:
            self._reconcile_active_runs()
            self._start_local_phases()

            now = time.time()
            if now - last_heartbeat >= self.heartbeat_interval_sec:
                state, current_job_id = self._heartbeat_state()
                try:
                    self.client.heartbeat(self.worker_id, state=state, current_job_id=current_job_id)
                    last_heartbeat = now
                    self._mark_alive(state=f"heartbeat_{state}", current_job_id=current_job_id)
                except requests_exceptions.RequestException:
                    self._mark_alive(state="heartbeat_error", current_job_id=current_job_id)

            self._claim_assignments_if_capacity()
            time.sleep(max(0.5, config.scheduler_tick_interval_sec))

    def _heartbeat_state(self) -> tuple[str, Optional[str]]:
        active_run = next(iter(self.active_runs.values()), None)
        if active_run is not None:
            return ("busy", active_run.job_id)
        return ("idle", None)

    def _claim_assignments_if_capacity(self) -> None:
        assert self.worker_id is not None
        while self._can_claim_more():
            active_job_id = next(iter(self.active_runs.keys()), None)
            try:
                self._mark_alive(state="claim_poll", current_job_id=active_job_id)
                assignment = self.client.claim_next(self.worker_id)
            except requests_exceptions.RequestException:
                self._mark_alive(state="claim_error", current_job_id=active_job_id)
                return

            if assignment is None:
                self._mark_alive(
                    state="idle_no_assignment" if active_job_id is None else "no_additional_assignment",
                    current_job_id=active_job_id,
                )
                return

            try:
                self._accept_assignment(assignment)
            except Exception as error:
                self._mark_alive(state="assignment_error", current_job_id=assignment["job_id"])
                self.client.fail(
                    assignment["job_id"],
                    self.worker_id,
                    reason="worker_runtime_error",
                    detail=str(error),
                    stage="assigned",
                    phase_name="bootstrap",
                )

    def _can_claim_more(self) -> bool:
        if len(self.active_runs) >= max(1, config.max_active_jobs):
            return False
        active_prep_jobs = sum(1 for run in self.active_runs.values() if run.phase in {"prep_pending", "prepping"})
        return active_prep_jobs < max(1, config.max_parallel_prep_jobs)

    def _accept_assignment(self, assignment: dict[str, Any]) -> None:
        job_id = assignment["job_id"]
        if job_id in self.active_runs:
            return

        input_mode = str(assignment["input"].get("mode") or "object_storage")
        input_url = assignment["input"].get("download_url")
        manifest_url = assignment["input"].get("manifest_url")
        input_file_name = Path(assignment["input"]["storage_key"]).name
        local_input_path = Path(config.local_input_directory) / job_id / input_file_name
        local_input_path.parent.mkdir(parents=True, exist_ok=True)
        tier_plan = self._tier_plan(assignment)
        run_name = self._make_run_name(job_id)
        summary_dir = Path(config.donor_output_directory) / f"hislam2_{run_name}" / "summaries"
        summary_dir.mkdir(parents=True, exist_ok=True)
        # Never let stale stream-train residue from an older attempt leak into a new assignment.
        # In conservative mode this would falsely light up training/timeline before upload finishes.
        for stale_path in (
            summary_dir / "STREAM_TRAIN_STATUS.json",
            summary_dir / "STREAM_TRAIN_STOP.flag",
        ):
            try:
                stale_path.unlink()
            except FileNotFoundError:
                pass
        initial_state = "streaming_input" if input_mode == "chunked_stream" else "downloading_input"
        stream_run = ActiveJobRun(
            assignment=assignment,
            run_name=run_name,
            local_input_path=local_input_path,
            tier_plan=tier_plan,
            phase=initial_state,
        )
        self._mark_alive(state=initial_state, current_job_id=job_id)
        last_download_mark = 0.0
        last_assignment_heartbeat = 0.0
        last_cancel_poll = 0.0
        last_stream_runtime_push = 0.0
        last_stream_runtime_signature: Optional[tuple[Any, ...]] = None
        last_stream_overlap_tick = 0.0
        stream_prewarm_process: Optional[subprocess.Popen[Any]] = None
        stream_prewarm_status_path: Optional[Path] = None
        stream_prewarm_stop_flag: Optional[Path] = None
        stream_live_sfm_process: Optional[subprocess.Popen[Any]] = None
        stream_live_sfm_status_path: Optional[Path] = None
        stream_live_sfm_stop_flag: Optional[Path] = None
        stream_train_seed_process: Optional[subprocess.Popen[Any]] = None
        stream_train_seed_status_path: Optional[Path] = None
        stream_train_seed_stop_flag: Optional[Path] = None
        stream_upload_complete_flag: Optional[Path] = None

        if input_mode == "chunked_stream" and config.stream_prewarm_enabled:
            (
                stream_prewarm_process,
                stream_prewarm_status_path,
                stream_prewarm_stop_flag,
                stream_upload_complete_flag,
            ) = self._start_stream_prewarm(
                run_name=run_name,
                tier_name=tier_plan[0].name,
                local_input_path=local_input_path,
                target_size_bytes=int(assignment["input"].get("size_bytes") or 0),
            )
        if (
            input_mode == "chunked_stream"
            and config.stream_live_sfm_enabled
            and tier_plan[0].prep_mode == "fallback"
            and tier_plan[0].stride is not None
            and tier_plan[0].max_frames is not None
            and tier_plan[0].matcher is not None
        ):
            (
                stream_live_sfm_process,
                stream_live_sfm_status_path,
                stream_live_sfm_stop_flag,
                stream_upload_complete_flag,
            ) = self._start_stream_live_sfm(
                run_name=run_name,
                tier=tier_plan[0],
                upload_complete_flag=stream_upload_complete_flag,
            )
        if (
            input_mode == "chunked_stream"
            and config.stream_train_seed_enabled
            and tier_plan[0].prep_mode == "fallback"
            and stream_live_sfm_status_path is not None
        ):
            (
                stream_train_seed_process,
                stream_train_seed_status_path,
                stream_train_seed_stop_flag,
                stream_upload_complete_flag,
            ) = self._start_stream_train_seed(
                run_name=run_name,
                tier=tier_plan[0],
                upload_complete_flag=stream_upload_complete_flag,
            )

        def emit_stream_runtime_if_available() -> None:
            nonlocal last_stream_runtime_push, last_stream_runtime_signature, last_stream_overlap_tick
            now = time.time()
            if now - last_stream_overlap_tick >= 1.0:
                stream_snapshot = self._poll_local_snapshot(stream_run.run_name)
                self._maybe_stage_artifact_bundle_upload(stream_run, stream_snapshot)
                self._maybe_publish_staged_artifact_manifest(stream_run, stream_snapshot)
                last_stream_overlap_tick = now
            if not self.worker_id:
                return
            if now - last_stream_runtime_push < 1.0:
                return
            status_payload = self._select_stream_status(
                prewarm_status_path=stream_prewarm_status_path,
                live_sfm_status_path=stream_live_sfm_status_path,
                train_seed_status_path=stream_train_seed_status_path,
            )
            if not status_payload:
                return

            title = str(status_payload.get("title") or "").strip()
            stage = str(status_payload.get("stage") or "preparing")
            if not title:
                title = "正在边上传边预热"
                if stage == "sfm" or stage.startswith("sfm"):
                    title = "正在边上传边做相机重建"

            resolved_stage = str(status_payload.get("stage") or "preparing")
            resolved_phase_name = status_payload.get("phase_name")
            runtime_payload: dict[str, Any] = {
                "worker_id": self.worker_id,
                "state": self._job_state_for_runtime(resolved_stage, str(resolved_phase_name or "")),
                "stage": resolved_stage,
                "phase_name": resolved_phase_name,
                "current_tier": status_payload.get("current_tier") or tier_plan[0].name,
                "title": title,
                "detail": str(status_payload.get("detail") or "远端正在边下载输入、边做前置预热。"),
                "progress_fraction": self._normalize_progress(status_payload.get("progress")),
                "progress_basis": status_payload.get("progress_basis"),
                "elapsed_seconds": self._int_or_none(status_payload.get("elapsed_sec")),
                "estimated_remaining_seconds": self._int_or_none(status_payload.get("estimated_remaining_sec")),
                "metrics": {},
            }
            for metric_name in (
                "current_units",
                "target_units",
                "accepted_live_frames",
                "downloaded_bytes",
                "upload_completed",
                "extracted_frames",
                "selected_frames",
                "registered_images",
                "visible_bytes",
                "visible_chunk_count",
                "completed_part_count",
                "total_chunks",
            ):
                metric_value = status_payload.get(metric_name)
                if metric_value is not None:
                    runtime_payload["metrics"][metric_name] = metric_value
            unit_label = status_payload.get("unit_label")
            if unit_label:
                runtime_payload["metrics"]["unit_label"] = unit_label
            for metric_name in ("current_round", "current_profile"):
                metric_value = str(status_payload.get(metric_name) or "").strip()
                if metric_value:
                    runtime_payload["metrics"][metric_name] = metric_value

            signature = (
                runtime_payload["stage"],
                runtime_payload["phase_name"],
                runtime_payload["detail"],
                runtime_payload["progress_fraction"],
                runtime_payload["progress_basis"],
                runtime_payload["elapsed_seconds"],
                runtime_payload["estimated_remaining_seconds"],
                tuple(sorted(runtime_payload["metrics"].items())),
            )
            if signature == last_stream_runtime_signature:
                return
            try:
                self.client.runtime(job_id, runtime_payload)
                last_stream_runtime_push = now
                last_stream_runtime_signature = signature
            except requests_exceptions.RequestException:
                return

        def maintain_assignment(bytes_written: int, total_bytes: Optional[int]) -> bool:
            nonlocal last_assignment_heartbeat, last_cancel_poll
            now = time.time()
            extra: dict[str, Any] = {"downloaded_bytes": bytes_written}
            if total_bytes is not None:
                extra["target_bytes"] = total_bytes

            if now - last_assignment_heartbeat >= max(2.0, float(self.heartbeat_interval_sec) / 2.0):
                if self.worker_id:
                    try:
                        self.client.heartbeat(self.worker_id, state="busy", current_job_id=job_id)
                    except requests_exceptions.RequestException:
                        pass
                self._mark_alive(state=initial_state, current_job_id=job_id, extra=extra)
                last_assignment_heartbeat = now

            emit_stream_runtime_if_available()

            if now - last_cancel_poll >= max(1.0, config.chunk_manifest_poll_interval_sec):
                try:
                    control_plane_job = self.client.fetch_job(job_id)
                except requests_exceptions.RequestException:
                    control_plane_job = {}
                last_cancel_poll = now
                if control_plane_job.get("state") == "cancelled":
                    return True
            return False

        def on_download_progress(bytes_written: int, total_bytes: Optional[int]) -> None:
            nonlocal last_download_mark
            now = time.time()
            if now - last_download_mark < 1.0:
                return
            extra: dict[str, Any] = {"downloaded_bytes": bytes_written}
            if total_bytes is not None:
                extra["target_bytes"] = total_bytes
            self._mark_alive(state=initial_state, current_job_id=job_id, extra=extra)
            last_download_mark = now

        if self.worker_id:
            try:
                self.client.heartbeat(self.worker_id, state="busy", current_job_id=job_id)
            except requests_exceptions.RequestException:
                pass
        last_assignment_heartbeat = time.time()

        try:
            if input_mode == "chunked_stream":
                if not manifest_url:
                    raise RuntimeError("chunk_manifest_url_missing")
                self.storage.download_chunk_stream_to_path(
                    manifest_url,
                    local_input_path,
                    progress_callback=on_download_progress,
                    should_abort=maintain_assignment,
                )
            else:
                if not input_url:
                    raise RuntimeError("input_download_url_missing")
                self.storage.download_to_path(input_url, local_input_path, progress_callback=on_download_progress)
        except AssignmentCancelledError:
            self._stop_stream_prewarm(
                stream_prewarm_process,
                stop_flag=stream_prewarm_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=0.5,
            )
            self._stop_stream_prewarm(
                stream_live_sfm_process,
                stop_flag=stream_live_sfm_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=0.5,
            )
            self._stop_stream_prewarm(
                stream_train_seed_process,
                stop_flag=stream_train_seed_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=0.5,
            )
            self._cancel_run(run_name)
            self._clear_prep_ready_marker(run_name)
            self._cleanup_stream_prewarm_storage(run_name, tier_plan[0].name)
            self._cleanup_staged_artifacts(stream_run)
            shutil.rmtree(local_input_path.parent, ignore_errors=True)
            self._mark_alive(state="stream_cancelled", current_job_id=job_id)
            if self.worker_id:
                try:
                    self.client.cancel_ack(job_id, self.worker_id)
                except requests_exceptions.RequestException:
                    pass
                try:
                    self.client.heartbeat(self.worker_id, state="idle", current_job_id=None)
                except requests_exceptions.RequestException:
                    pass
            return
        except Exception:
            self._stop_stream_prewarm(
                stream_prewarm_process,
                stop_flag=stream_prewarm_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=0.5,
            )
            self._stop_stream_prewarm(
                stream_live_sfm_process,
                stop_flag=stream_live_sfm_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=0.5,
            )
            self._stop_stream_prewarm(
                stream_train_seed_process,
                stop_flag=stream_train_seed_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=0.5,
            )
            self._cancel_run(run_name)
            self._clear_prep_ready_marker(run_name)
            self._cleanup_stream_prewarm_storage(run_name, tier_plan[0].name)
            self._cleanup_staged_artifacts(stream_run)
            raise

        self._stop_stream_prewarm(
            stream_prewarm_process,
            stop_flag=stream_prewarm_stop_flag,
            upload_complete_flag=stream_upload_complete_flag,
            graceful_exit_sec=2.0,
        )
        self._stop_stream_prewarm(
            stream_live_sfm_process,
            stop_flag=stream_live_sfm_stop_flag,
            upload_complete_flag=stream_upload_complete_flag,
            graceful_exit_sec=max(2.0, config.stream_live_sfm_finalize_grace_sec),
        )

        adopted_stream_train = (
            config.stream_train_seed_enabled
            and stream_train_seed_process is not None
            and stream_train_seed_process.poll() is None
        )
        if not adopted_stream_train:
            self._stop_stream_prewarm(
                stream_train_seed_process,
                stop_flag=stream_train_seed_stop_flag,
                upload_complete_flag=stream_upload_complete_flag,
                graceful_exit_sec=max(1.0, config.stream_train_seed_finalize_grace_sec),
            )

        promoted_prep_ready: Optional[dict[str, Any]] = None
        if (
            not adopted_stream_train
            and input_mode == "chunked_stream"
            and config.stream_promote_live_sfm_after_upload
        ):
            promoted_prep_ready = self._maybe_promote_live_sfm_to_prep_ready(
                run_name=run_name,
                tier_name=tier_plan[0].name,
            )
        else:
            self._clear_prep_ready_marker(run_name)

        stream_run.phase = (
            "training"
            if adopted_stream_train
            else "gpu_ready" if promoted_prep_ready is not None else "prep_pending"
        )
        stream_run.prep_ready_payload = promoted_prep_ready
        stream_run.process = stream_train_seed_process if adopted_stream_train else None
        stream_run.wrapper_log_path = (
            Path(config.donor_logs_directory) / f"{run_name}.{tier_plan[0].name}.stream_train.log"
            if adopted_stream_train
            else None
        )
        stream_run.last_runtime_push = None
        run = stream_run
        self.active_runs[job_id] = run
        if adopted_stream_train:
            run.last_runtime_push = None
            self._mark_alive(
                state="seed_train_adopted_as_full_train",
                current_job_id=job_id,
                extra={"current_tier": tier_plan[0].name},
            )
        elif promoted_prep_ready is not None:
            self._mark_alive(
                state="live_sfm_promoted_to_gpu_ready",
                current_job_id=job_id,
                extra={
                    "current_tier": tier_plan[0].name,
                    "registered_images": promoted_prep_ready.get("registered_images"),
                    "selected_frames": promoted_prep_ready.get("selected_frames"),
                },
            )
        self._mark_alive(
            state="assignment_accepted",
            current_job_id=job_id,
            extra={"downloaded_bytes": local_input_path.stat().st_size},
        )

    @staticmethod
    def _normalize_frame_sampling_profile(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        return normalized if normalized in FRAME_SAMPLING_TIER_PRESETS else "full"

    @classmethod
    def _sampling_profile_from_pipeline_profile(cls, pipeline_profile: Any) -> str:
        candidate = pipeline_profile if isinstance(pipeline_profile, dict) else {}
        direct_profile = cls._normalize_frame_sampling_profile(
            candidate.get("frame_sampling_profile")
            or candidate.get("sampling_profile")
            or candidate.get("collection_profile")
        )
        if direct_profile != "full":
            return direct_profile

        label = cls._normalize_frame_sampling_profile(candidate.get("label"))
        if label != "full":
            return label

        tier_name = str(candidate.get("tier_name") or "").strip()
        mapped_tier = TIER_NAME_TO_SAMPLING_PROFILE.get(tier_name)
        if mapped_tier:
            return mapped_tier

        title = str(candidate.get("requested_preset_title") or candidate.get("title") or "").strip()
        for profile_name, preset in FRAME_SAMPLING_TIER_PRESETS.items():
            preset_title = str(preset.name or "").strip()
            if not preset_title:
                preset_title = profile_name
            if title == preset_title:
                return profile_name
            if str(candidate.get("max_frames") or "").strip() == str(preset.max_frames or ""):
                return profile_name

        fraction_value = candidate.get("requested_frame_fraction")
        if fraction_value in (None, ""):
            fraction_value = candidate.get("frame_fraction")
        try:
            fraction = float(fraction_value)
        except (TypeError, ValueError):
            fraction = None
        if fraction is not None:
            for profile_name, preset in FRAME_SAMPLING_TIER_PRESETS.items():
                preset_fraction = 1.0
                if abs(preset_fraction - fraction) <= 0.02:
                    return profile_name

        return "full"

    @classmethod
    def _requested_frame_sampling_profile(
        cls,
        assignment: Optional[dict[str, Any]] = None,
        *,
        current_tier: Optional[str] = None,
    ) -> str:
        if current_tier:
            mapped = TIER_NAME_TO_SAMPLING_PROFILE.get(str(current_tier).strip())
            if mapped:
                return mapped
        pipeline_profile = (assignment or {}).get("pipeline_profile") or {}
        if isinstance(pipeline_profile, dict):
            return cls._sampling_profile_from_pipeline_profile(pipeline_profile)
        return "full"

    @classmethod
    def _tier_plan(
        cls,
        assignment: Optional[dict[str, Any]] = None,
        *,
        current_tier: Optional[str] = None,
    ) -> list[TierPlan]:
        sampling_profile = cls._requested_frame_sampling_profile(
            assignment,
            current_tier=current_tier,
        )
        return [FRAME_SAMPLING_TIER_PRESETS[sampling_profile]]

    def _start_local_phases(self) -> None:
        gpu_active = sum(1 for run in self.active_runs.values() if run.phase == "training")
        if gpu_active < max(1, config.max_parallel_gpu_jobs):
            for run in self.active_runs.values():
                if run.phase == "gpu_ready":
                    if not self._can_begin_training(run):
                        continue
                    self._start_train_phase(run)
                    gpu_active += 1
                    if gpu_active >= max(1, config.max_parallel_gpu_jobs):
                        break

        prep_running = sum(1 for run in self.active_runs.values() if run.phase == "prepping")
        for run in self.active_runs.values():
            if prep_running >= max(1, config.max_parallel_prep_jobs):
                break
            if run.phase == "prep_pending":
                self._start_prep_phase(run)
                prep_running += 1

    def _upload_completed_for_run(
        self,
        run: ActiveJobRun,
        *,
        control_plane_job: Optional[dict[str, Any]] = None,
    ) -> bool:
        # Once PREP_READY exists, the worker has already downloaded the full input
        # and finished conservative-mode preprocessing. Training can proceed even if
        # the job-status poll response is missing legacy upload metadata.
        if self._prep_ready_file(run.run_name).exists():
            return True

        input_descriptor = run.assignment.get("input") or {}
        assignment_upload_completed = input_descriptor.get("upload_completed")
        if isinstance(assignment_upload_completed, bool) and assignment_upload_completed:
            return True

        input_mode = str(input_descriptor.get("mode") or "object_storage").strip().lower()
        if input_mode != "chunked_stream":
            return True

        if not isinstance(control_plane_job, dict):
            return False

        if bool(control_plane_job.get("upload_completed")):
            return True
        if control_plane_job.get("upload_completed_at"):
            return True

        progress_basis = str(control_plane_job.get("progress_basis") or "").strip().lower()
        stage = str(control_plane_job.get("stage") or "").strip().lower()
        phase_name = str(control_plane_job.get("phase_name") or "").strip().lower()
        state = str(control_plane_job.get("state") or "").strip().lower()

        if progress_basis in {
            "control_plane_upload_complete",
            "upload_complete",
            "multipart_upload_complete",
            "chunked_upload_complete",
            "multipart_auto_completed",
            "multipart_auto_completed_after_probe",
            "object_storage_visible",
            "prep_ready_waiting_gpu",
        }:
            return True
        if stage == "gpu_wait" or phase_name == "gpu_wait":
            return True
        if state in {"training_probe", "training_full", "exporting", "completed"}:
            return True
        return False

    def _can_begin_training(self, run: ActiveJobRun) -> bool:
        if config.stream_train_seed_enabled or config.stream_promote_live_sfm_after_upload:
            return True
        if self._upload_completed_for_run(run):
            return True
        try:
            control_plane_job = self.client.fetch_job(run.job_id)
        except requests_exceptions.RequestException:
            return False
        if self._upload_completed_for_run(run, control_plane_job=control_plane_job):
            return True
        self._mark_alive(
            state="training_blocked_waiting_upload_complete",
            current_job_id=run.job_id,
            extra={
                "current_tier": run.current_tier.name,
                "progress_basis": control_plane_job.get("progress_basis"),
                "stage": control_plane_job.get("stage"),
                "phase_name": control_plane_job.get("phase_name"),
            },
        )
        return False

    def _training_start_allowed(self, run: ActiveJobRun) -> bool:
        if config.stream_train_seed_enabled or config.stream_promote_live_sfm_after_upload:
            return True
        if self._upload_completed_for_run(run):
            return True
        try:
            control_plane_job = self.client.fetch_job(run.job_id)
        except requests_exceptions.RequestException:
            self._mark_alive(
                state="training_hard_blocked_waiting_upload_complete",
                current_job_id=run.job_id,
                extra={
                    "current_tier": run.current_tier.name,
                    "reason": "control_plane_unreachable",
                },
            )
            return False
        if self._upload_completed_for_run(run, control_plane_job=control_plane_job):
            return True
        self._mark_alive(
            state="training_hard_blocked_waiting_upload_complete",
            current_job_id=run.job_id,
            extra={
                "current_tier": run.current_tier.name,
                "progress_basis": control_plane_job.get("progress_basis"),
                "stage": control_plane_job.get("stage"),
                "phase_name": control_plane_job.get("phase_name"),
            },
        )
        return False

    def _start_prep_phase(self, run: ActiveJobRun) -> None:
        tier = run.current_tier
        self._clear_prep_ready_marker(run.run_name)
        wrapper_log = Path(config.donor_logs_directory) / f"{run.run_name}.{tier.name}.prep.wrapper.log"
        command = ["bash", config.donor_prep_script, run.run_name, str(run.local_input_path), tier.name]
        run.process = self._launch_phase_process(command, wrapper_log)
        run.wrapper_log_path = wrapper_log
        run.phase = "prepping"
        run.last_runtime_push = None
        self._mark_alive(state=f"prep_started:{tier.name}", current_job_id=run.job_id)

    def _start_train_phase(self, run: ActiveJobRun) -> None:
        if not self._training_start_allowed(run):
            run.phase = "gpu_ready"
            run.last_runtime_push = None
            return
        tier = run.current_tier
        wrapper_log = Path(config.donor_logs_directory) / f"{run.run_name}.{tier.name}.train.wrapper.log"
        command = ["bash", config.donor_train_script, run.run_name, tier.name]
        run.process = self._launch_phase_process(command, wrapper_log)
        run.wrapper_log_path = wrapper_log
        run.phase = "training"
        run.last_runtime_push = None
        self._mark_alive(state=f"train_started:{tier.name}", current_job_id=run.job_id)

    def _launch_phase_process(self, command: list[str], log_path: Path) -> subprocess.Popen[Any]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                command,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            handle.close()
        return process

    def _start_stream_prewarm(
        self,
        *,
        run_name: str,
        tier_name: str,
        local_input_path: Path,
        target_size_bytes: int,
    ) -> tuple[subprocess.Popen[Any], Path, Path, Path]:
        base_dir = Path(config.donor_output_directory) / f"hislam2_{run_name}"
        summary_dir = base_dir / "summaries"
        prep_root = base_dir / f"{tier_name}_prep"
        feed_root = base_dir / f"{tier_name}_feed"
        status_path = summary_dir / "STREAM_PREWARM_STATUS.json"
        stop_flag = summary_dir / "STREAM_PREWARM_STOP.flag"
        upload_complete_flag = summary_dir / "UPLOAD_COMPLETE.flag"
        log_path = Path(config.donor_logs_directory) / f"{run_name}.{tier_name}.stream_prewarm.log"

        for path in (stop_flag, upload_complete_flag, status_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        command = [
            config.donor_audit_python,
            str(Path(config.donor_root) / "scripts" / "incremental_video_prewarm.py"),
            "--video",
            str(local_input_path),
            "--prep-root",
            str(prep_root),
            "--feed-root",
            str(feed_root),
            "--status-json",
            str(status_path),
            "--stop-flag",
            str(stop_flag),
            "--upload-complete-flag",
            str(upload_complete_flag),
            "--current-tier",
            tier_name,
            "--target-size-bytes",
            str(max(0, target_size_bytes)),
            "--poll-interval-sec",
            str(config.stream_prewarm_poll_interval_sec),
        ]
        return self._launch_phase_process(command, log_path), status_path, stop_flag, upload_complete_flag

    def _start_stream_live_sfm(
        self,
        *,
        run_name: str,
        tier: TierPlan,
        upload_complete_flag: Optional[Path],
    ) -> tuple[subprocess.Popen[Any], Path, Path, Path]:
        base_dir = Path(config.donor_output_directory) / f"hislam2_{run_name}"
        summary_dir = base_dir / "summaries"
        prep_root = base_dir / f"{tier.name}_prep"
        feed_root = base_dir / f"{tier.name}_feed"
        status_path = summary_dir / "STREAM_LIVE_SFM_STATUS.json"
        stop_flag = summary_dir / "STREAM_LIVE_SFM_STOP.flag"
        ready_path = prep_root / "LIVE_SFM_READY.json"
        upload_complete_path = upload_complete_flag or (summary_dir / "UPLOAD_COMPLETE.flag")
        log_path = Path(config.donor_logs_directory) / f"{run_name}.{tier.name}.stream_live_sfm.log"

        for path in (stop_flag, status_path, ready_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        command = [
            config.donor_prep_python,
            str(Path(config.donor_root) / "scripts" / "incremental_live_sfm.py"),
            "--prep-root",
            str(prep_root),
            "--feed-root",
            str(feed_root),
            "--status-json",
            str(status_path),
            "--ready-json",
            str(ready_path),
            "--stop-flag",
            str(stop_flag),
            "--upload-complete-flag",
            str(upload_complete_path),
            "--current-tier",
            tier.name,
            "--poll-interval-sec",
            str(config.stream_prewarm_poll_interval_sec),
            "--min-frames",
            str(max(4, config.stream_live_sfm_min_frames)),
            "--min-new-frames",
            str(max(1, config.stream_live_sfm_min_new_frames)),
            "--colmap-binary",
            "colmap",
            "--colmap-frame-stride",
            str(max(1, tier.stride or 1)),
            "--colmap-max-frames",
            str(max(8, min(tier.max_frames or 96, config.stream_live_sfm_max_frames_cap))),
            "--colmap-matcher",
            str(tier.matcher or "sequential"),
        ]
        return self._launch_phase_process(command, log_path), status_path, stop_flag, upload_complete_path

    def _start_stream_train_seed(
        self,
        *,
        run_name: str,
        tier: TierPlan,
        upload_complete_flag: Optional[Path],
    ) -> tuple[subprocess.Popen[Any], Path, Path, Path]:
        base_dir = Path(config.donor_output_directory) / f"hislam2_{run_name}"
        summary_dir = base_dir / "summaries"
        prep_root = base_dir / f"{tier.name}_prep"
        status_path = summary_dir / "STREAM_TRAIN_STATUS.json"
        stop_flag = summary_dir / "STREAM_TRAIN_STOP.flag"
        ready_path = prep_root / "LIVE_SFM_READY.json"
        upload_complete_path = upload_complete_flag or (summary_dir / "UPLOAD_COMPLETE.flag")
        log_path = Path(config.donor_logs_directory) / f"{run_name}.{tier.name}.stream_train.log"

        for path in (stop_flag, status_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        command = [
            config.donor_prep_python,
            str(Path(config.donor_root) / "scripts" / "incremental_train_seed.py"),
            "--root",
            str(config.donor_root),
            "--repo",
            "/root/gs_refs/HI-SLAM2",
            "--prep-root",
            str(prep_root),
            "--ready-json",
            str(ready_path),
            "--status-json",
            str(status_path),
            "--stop-flag",
            str(stop_flag),
            "--upload-complete-flag",
            str(upload_complete_path),
            "--current-tier",
            tier.name,
            "--poll-interval-sec",
            str(config.stream_train_seed_poll_interval_sec),
            "--min-selected-frames",
            str(max(4, config.stream_train_seed_min_selected_frames)),
            "--min-registered-images",
            str(max(2, config.stream_train_seed_min_registered_images)),
        ]
        if config.stream_train_seed_skip_tsdf:
            command.append("--skip-tsdf")
        return self._launch_phase_process(command, log_path), status_path, stop_flag, upload_complete_path

    @staticmethod
    def _read_stream_status(path: Optional[Path]) -> Optional[dict[str, Any]]:
        if path is None or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _select_stream_status(
        cls,
        prewarm_status_path: Optional[Path],
        live_sfm_status_path: Optional[Path],
        train_seed_status_path: Optional[Path],
    ) -> Optional[dict[str, Any]]:
        prewarm = cls._read_stream_status(prewarm_status_path)
        live_sfm = cls._read_stream_status(live_sfm_status_path)
        train_seed: Optional[dict[str, Any]] = None
        if config.stream_train_seed_enabled or config.stream_promote_live_sfm_after_upload:
            train_seed = cls._read_stream_status(train_seed_status_path)
        if train_seed:
            train_stage = str(train_seed.get("stage") or "").strip().lower()
            if train_stage == "train" or train_stage.startswith("train"):
                return train_seed
        if not live_sfm:
            return prewarm
        phase_name = str(live_sfm.get("phase_name") or "").strip().lower()
        progress_basis = str(live_sfm.get("progress_basis") or "").strip().lower()
        stage = str(live_sfm.get("stage") or "").strip().lower()
        if phase_name in {"sfm_wait_live", "live_sfm_retry_wait"} and prewarm:
            return prewarm
        if stage == "sfm" or stage.startswith("sfm") or progress_basis.startswith("prep_live_sfm"):
            return live_sfm
        return live_sfm or prewarm

    @staticmethod
    def _cleanup_stream_prewarm_storage(run_name: str, tier_name: str) -> None:
        base_dir = Path(config.donor_output_directory) / f"hislam2_{run_name}"
        prewarm_log = Path(config.donor_logs_directory) / f"{run_name}.{tier_name}.stream_prewarm.log"
        live_sfm_log = Path(config.donor_logs_directory) / f"{run_name}.{tier_name}.stream_live_sfm.log"
        train_seed_log = Path(config.donor_logs_directory) / f"{run_name}.{tier_name}.stream_train.log"
        logs_root = Path(config.donor_logs_directory) / run_name
        shutil.rmtree(base_dir, ignore_errors=True)
        shutil.rmtree(logs_root, ignore_errors=True)
        for path in (prewarm_log, live_sfm_log, train_seed_log):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _stop_stream_prewarm(
        process: Optional[subprocess.Popen[Any]],
        *,
        stop_flag: Optional[Path],
        upload_complete_flag: Optional[Path],
        graceful_exit_sec: float,
    ) -> None:
        if upload_complete_flag is not None:
            upload_complete_flag.parent.mkdir(parents=True, exist_ok=True)
            upload_complete_flag.write_text("ok\n", encoding="utf-8")
        if process is None:
            return
        deadline = time.time() + max(0.0, graceful_exit_sec)
        while process.poll() is None and time.time() < deadline:
            time.sleep(0.2)
        if process.poll() is None and stop_flag is not None:
            stop_flag.parent.mkdir(parents=True, exist_ok=True)
            stop_flag.write_text("stop\n", encoding="utf-8")
            deadline = time.time() + 2.0
            while process.poll() is None and time.time() < deadline:
                time.sleep(0.2)
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            time.sleep(0.5)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return

    def _reconcile_active_runs(self) -> None:
        for job_id in list(self.active_runs.keys()):
            run = self.active_runs.get(job_id)
            if run is None:
                continue
            snapshot = self._snapshot_for_run(run)
            self._maybe_stage_artifact_bundle_upload(run, snapshot)
            self._maybe_publish_staged_artifact_manifest(run, snapshot)
            self._mark_alive(
                state=f"runtime_{run.phase}:{snapshot.stage}",
                current_job_id=run.job_id,
                extra={
                    "phase_name": snapshot.phase_name,
                    "current_tier": snapshot.current_tier,
                    "progress_basis": snapshot.progress_basis,
                },
            )
            self._maybe_push_runtime(run, snapshot)

            run.cancel_poll_counter += 1
            if run.cancel_poll_counter % max(1, config.control_plane_cancel_poll_every) == 0:
                try:
                    control_plane_job = self.client.fetch_job(run.job_id)
                except requests_exceptions.RequestException:
                    control_plane_job = {}
                if control_plane_job.get("state") == "cancelled":
                    self._cancel_active_run(run)
                    self.client.cancel_ack(run.job_id, self.worker_id or "")
                    self.active_runs.pop(run.job_id, None)
                    continue

            if run.recovered and run.process is None and snapshot.state in {"completed", "failed", "cancelled"}:
                self._handle_recovered_terminal_snapshot(run, snapshot)
                continue

            if run.process is not None and run.process.poll() is not None:
                self._handle_phase_exit(run)

    def _snapshot_for_run(self, run: ActiveJobRun) -> Snapshot:
        if run.phase == "prep_pending":
            return Snapshot(
                state="assigned",
                stage="prep_queue",
                phase_name="prep_queue",
                current_tier=run.current_tier.name,
                title="任务已分配",
                detail="worker 已接单，正在等待本机 CPU 预处理槽。",
                progress_fraction=0.2,
                progress_basis="worker_assigned",
                elapsed_seconds=self._elapsed_seconds_for_run(run.run_name),
                estimated_remaining_seconds=None,
            )
        if run.phase == "gpu_ready":
            return Snapshot(
                state="assigned",
                stage="gpu_wait",
                phase_name="gpu_wait",
                current_tier=run.current_tier.name,
                title="预处理已完成",
                detail="当前这条任务的官方 HI-SLAM 预处理已完成，正在等待 GPU 训练槽。",
                progress_fraction=0.52,
                progress_basis="prep_ready_waiting_gpu",
                elapsed_seconds=self._elapsed_seconds_for_run(run.run_name),
                estimated_remaining_seconds=None,
            )
        return self._poll_local_snapshot(run.run_name)

    def _maybe_push_runtime(self, run: ActiveJobRun, snapshot: Snapshot) -> None:
        if snapshot.state in {"completed", "failed", "cancelled"}:
            return
        state_tuple = (
            snapshot.state,
            snapshot.stage,
            snapshot.phase_name,
            snapshot.current_tier,
            snapshot.progress_fraction,
            snapshot.progress_basis,
            snapshot.elapsed_seconds,
            snapshot.estimated_remaining_seconds,
            snapshot.title,
            snapshot.detail,
            json.dumps(snapshot.metrics, sort_keys=True, ensure_ascii=False),
        )
        if state_tuple == run.last_runtime_push:
            return
        self.client.runtime(
            run.job_id,
            {
                "worker_id": self.worker_id,
                "state": snapshot.state,
                "stage": snapshot.stage,
                "phase_name": snapshot.phase_name,
                "current_tier": snapshot.current_tier,
                "title": snapshot.title,
                "detail": snapshot.detail,
                "progress_fraction": snapshot.progress_fraction,
                "progress_basis": snapshot.progress_basis,
                "elapsed_seconds": snapshot.elapsed_seconds,
                "estimated_remaining_seconds": snapshot.estimated_remaining_seconds,
                "metrics": snapshot.metrics,
            },
        )
        run.last_runtime_push = state_tuple

    def _handle_phase_exit(self, run: ActiveJobRun) -> None:
        final_snapshot = self._snapshot_for_run(run)
        run.process = None

        if run.phase == "prepping":
            if self._prep_ready_file(run.run_name).exists():
                run.phase = "gpu_ready"
                run.last_runtime_push = None
                return
            if self._advance_to_next_tier(run):
                run.phase = "prep_pending"
                run.last_runtime_push = None
                return
            reason, detail = self._final_failure_for_run(run, phase="prepping")
            self.client.fail(
                run.job_id,
                self.worker_id or "",
                reason=reason,
                detail=detail,
                stage=final_snapshot.stage,
                phase_name=final_snapshot.phase_name or "prep",
            )
            self._cleanup_run_storage(run, snapshot=None, keep_primary_artifact=False)
            self.active_runs.pop(run.job_id, None)
            return

        if run.phase == "training":
            final_snapshot = self._poll_local_snapshot(run.run_name)
            if final_snapshot.state == "completed":
                manifest = self._upload_artifacts(run.assignment, final_snapshot, run=run)
                self._mark_alive(state="uploading_artifacts", current_job_id=run.job_id)
                self.client.upload_artifact_manifest(run.job_id, {"worker_id": self.worker_id, "manifest": manifest})
                self.client.complete(
                    run.job_id,
                    self.worker_id or "",
                    title=final_snapshot.title,
                    detail=final_snapshot.detail,
                )
                self._cleanup_run_storage(run, snapshot=final_snapshot, keep_primary_artifact=True)
                self.active_runs.pop(run.job_id, None)
                return
            if self._advance_to_next_tier(run):
                run.phase = "prep_pending"
                run.last_runtime_push = None
                return
            reason, detail = self._final_failure_for_run(run, phase="training")
            self.client.fail(
                run.job_id,
                self.worker_id or "",
                reason=reason,
                detail=detail,
                stage=final_snapshot.stage,
                phase_name=final_snapshot.phase_name or "train",
            )
            self._cleanup_staged_artifacts(run)
            self._cleanup_run_storage(run, snapshot=None, keep_primary_artifact=False)
            self.active_runs.pop(run.job_id, None)

    def _handle_recovered_terminal_snapshot(self, run: ActiveJobRun, snapshot: Snapshot) -> None:
        if snapshot.state == "completed":
            manifest = self._upload_artifacts(run.assignment, snapshot, run=run)
            self._mark_alive(state="uploading_artifacts", current_job_id=run.job_id)
            self.client.upload_artifact_manifest(run.job_id, {"worker_id": self.worker_id, "manifest": manifest})
            self.client.complete(
                run.job_id,
                self.worker_id or "",
                title=snapshot.title,
                detail=snapshot.detail,
            )
            self._cleanup_run_storage(run, snapshot=snapshot, keep_primary_artifact=True)
            self.active_runs.pop(run.job_id, None)
            return

        if snapshot.state == "failed":
            self.client.fail(
                run.job_id,
                self.worker_id or "",
                reason=snapshot.failure_reason or "worker_runtime_error",
                detail=snapshot.failure_detail or snapshot.detail,
                stage=snapshot.stage,
                phase_name=snapshot.phase_name or "train",
            )
            self._cleanup_staged_artifacts(run)
            self._cleanup_run_storage(run, snapshot=None, keep_primary_artifact=False)
            self.active_runs.pop(run.job_id, None)
            return

        if snapshot.state == "cancelled":
            self.client.cancel_ack(run.job_id, self.worker_id or "")
            self._cleanup_staged_artifacts(run)
            self._cleanup_run_storage(run, snapshot=None, keep_primary_artifact=False)
            self.active_runs.pop(run.job_id, None)

    def _advance_to_next_tier(self, run: ActiveJobRun) -> bool:
        if run.tier_index + 1 >= len(run.tier_plan):
            return False
        run.tier_index += 1
        self._clear_prep_ready_marker(run.run_name)
        return True

    def _resume_local_runs_after_restart(self) -> None:
        output_root = Path(config.donor_output_directory)
        if not output_root.is_dir():
            return

        for run_dir in sorted(output_root.glob("hislam2_mobile_job_*_official_default_*")):
            if not run_dir.is_dir():
                continue
            run_name = run_dir.name.removeprefix("hislam2_")
            job_id = self._job_id_from_run_name(run_name)
            if not job_id or job_id in self.active_runs:
                continue

            try:
                control_plane_job = self.client.fetch_job(job_id)
            except requests_exceptions.RequestException:
                continue

            if control_plane_job.get("state") in {"completed", "cancelled"}:
                continue

            snapshot = self._poll_local_snapshot(run_name)
            if snapshot.state == "completed":
                try:
                    self._salvage_completed_run(job_id=job_id, run_name=run_name, snapshot=snapshot)
                except Exception:
                    continue
                continue

            active_worker = self._find_active_worker(
                run_name,
                Path(config.donor_logs_directory) / f"{run_name}.pid",
            )
            prep_ready_exists = self._prep_ready_file(run_name).exists()
            if not active_worker and not prep_ready_exists:
                continue

            local_input_root = Path(config.local_input_directory) / job_id
            local_input_path = next(local_input_root.iterdir(), local_input_root / "recovered_input.mp4") if local_input_root.exists() else local_input_root / "recovered_input.mp4"
            tier_plan = self._tier_plan(current_tier=snapshot.current_tier)
            tier_index = 0
            if snapshot.current_tier:
                for idx, tier in enumerate(tier_plan):
                    if tier.name == snapshot.current_tier:
                        tier_index = idx
                        break
            recovered_phase = "gpu_ready" if prep_ready_exists else ("training" if snapshot.stage == "train" else "prepping")
            snapshot_phase_name = (snapshot.phase_name or "").strip().lower()
            control_plane_state = str(control_plane_job.get("state") or "").strip().lower()
            if recovered_phase == "training":
                if snapshot_phase_name.startswith("seed_") and not config.stream_train_seed_enabled:
                    recovered_phase = "prepping"
                elif (
                    control_plane_state not in {"training_probe", "training_full", "exporting"}
                    and not bool(control_plane_job.get("upload_completed"))
                    and not control_plane_job.get("upload_completed_at")
                ):
                    # Conservative mode: do not resurrect an early-training snapshot
                    # after restart unless the control-plane job is already in a real
                    # training/export state or upload has fully completed.
                    recovered_phase = "prepping"
            self.active_runs[job_id] = ActiveJobRun(
                assignment={
                    "job_id": job_id,
                    "output_prefix": self._artifact_prefix_for_job(job_id),
                },
                run_name=run_name,
                local_input_path=local_input_path,
                tier_plan=tier_plan,
                phase=recovered_phase,
                tier_index=tier_index,
                process=None,
                wrapper_log_path=None,
                recovered=True,
            )

    def _salvage_completed_run(self, *, job_id: str, run_name: str, snapshot: Snapshot) -> None:
        manifest = self._upload_artifacts(
            {"job_id": job_id, "output_prefix": self._artifact_prefix_for_job(job_id)},
            snapshot,
        )
        self._mark_alive(state="uploading_artifacts", current_job_id=job_id)
        self.client.upload_artifact_manifest(job_id, {"worker_id": self.worker_id, "manifest": manifest})
        self.client.complete(job_id, self.worker_id or "", title=snapshot.title, detail=snapshot.detail)
        cleanup_run = ActiveJobRun(
            assignment={"job_id": job_id, "output_prefix": self._artifact_prefix_for_job(job_id)},
            run_name=run_name,
            local_input_path=(Path(config.local_input_directory) / job_id / "recovered_input.mp4"),
            tier_plan=self._tier_plan(current_tier=snapshot.current_tier),
            phase="training",
            recovered=True,
        )
        self._cleanup_run_storage(cleanup_run, snapshot=snapshot, keep_primary_artifact=True)

    @staticmethod
    def _job_id_from_run_name(run_name: str) -> Optional[str]:
        prefix = "mobile_"
        marker = "_official_default_"
        if not run_name.startswith(prefix):
            return None
        marker_index = run_name.find(marker)
        if marker_index <= len(prefix):
            return None
        job_id = run_name[len(prefix):marker_index].strip()
        return job_id or None

    @staticmethod
    def _artifact_prefix_for_job(job_id: str) -> str:
        return f"{config.artifact_bucket_prefix.rstrip('/')}/{job_id}/"

    def _final_failure_for_run(self, run: ActiveJobRun, *, phase: str) -> tuple[str, str]:
        output_root = Path(config.donor_output_directory) / f"hislam2_{run.run_name}"
        summaries = output_root / "summaries"
        logs_root = Path(config.donor_logs_directory) / run.run_name
        tier_names = [tier.name for tier in run.tier_plan]
        return self._summarize_all_tier_failures(summaries, logs_root, tier_names, phase=phase)

    def _cancel_active_run(self, run: ActiveJobRun) -> None:
        process = run.process
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(1)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            run.process = None
        self._write_cancelled_marker(run.run_name, "worker 已按用户取消请求停止本地任务。")
        self._cleanup_staged_artifacts(run)
        # The donor wrapper can fork long-lived child processes that outlive the
        # Popen handle above. Cancel by run name as well so a superseded/cancelled
        # job cannot quietly keep consuming CPU and overwriting runtime status.
        self._cancel_run(run.run_name)
        self._clear_prep_ready_marker(run.run_name)
        self._cleanup_run_storage(run, snapshot=None, keep_primary_artifact=False)
        self.active_runs.pop(run.job_id, None)

    def _mark_alive(
        self,
        *,
        state: str,
        current_job_id: Optional[str],
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "pid": os.getpid(),
            "timestamp_epoch": time.time(),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "worker_id": self.worker_id,
            "state": state,
            "current_job_id": current_job_id,
        }
        if extra:
            payload.update(extra)

        try:
            self.liveness_heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.liveness_heartbeat_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")
            tmp_path.replace(self.liveness_heartbeat_file)
        except OSError:
            return

    @staticmethod
    def _make_run_name(job_id: str) -> str:
        return f"mobile_{job_id}_official_default_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"

    @staticmethod
    def _prep_ready_file(run_name: str) -> Path:
        return Path(config.donor_output_directory) / f"hislam2_{run_name}" / "summaries" / "PREP_READY.json"

    @classmethod
    def _clear_prep_ready_marker(cls, run_name: str) -> None:
        marker = cls._prep_ready_file(run_name)
        try:
            marker.unlink()
        except FileNotFoundError:
            return

    @staticmethod
    def _live_sfm_ready_file(run_name: str, tier_name: str) -> Path:
        return (
            Path(config.donor_output_directory)
            / f"hislam2_{run_name}"
            / f"{tier_name}_prep"
            / "LIVE_SFM_READY.json"
        )

    @staticmethod
    def _summary_dir(run_name: str) -> Path:
        return Path(config.donor_output_directory) / f"hislam2_{run_name}" / "summaries"

    def _maybe_promote_live_sfm_to_prep_ready(self, *, run_name: str, tier_name: str) -> Optional[dict[str, Any]]:
        ready_payload = self._load_json(self._live_sfm_ready_file(run_name, tier_name))
        if not ready_payload:
            return None
        if not bool(ready_payload.get("upload_completed")):
            return None

        prep_root = Path(str(ready_payload.get("prep_root") or "")).expanduser()
        feed_root = Path(str(ready_payload.get("feed_root") or "")).expanduser()
        images_dir = feed_root / "images"
        calib_path = feed_root / "calib.txt"
        sparse_root = prep_root / "sparse" / "0"
        if not (feed_root.is_dir() and images_dir.is_dir() and calib_path.is_file()):
            return None
        if not sparse_root.is_dir():
            return None

        selected_frames = self._int_or_none(ready_payload.get("selected_frames")) or 0
        registered_images = self._int_or_none(ready_payload.get("registered_images")) or 0
        if (
            selected_frames < max(1, config.stream_promote_live_sfm_min_selected_frames)
            or registered_images < max(1, config.stream_promote_live_sfm_min_registered_images)
        ):
            return None

        summary_dir = self._summary_dir(run_name)
        summary_dir.mkdir(parents=True, exist_ok=True)
        live_audit_summary = feed_root / "live_audit_summary.json"
        audit_summary_path = summary_dir / f"{tier_name}_audit_summary.json"
        if live_audit_summary.is_file():
            shutil.copy2(live_audit_summary, audit_summary_path)
        elif not audit_summary_path.exists():
            audit_summary_path.write_text(
                json.dumps(
                    {
                        "gate_disabled": True,
                        "source": "live_sfm_promotion",
                        "selected_frames": selected_frames,
                        "registered_images": registered_images,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        promoted_payload = {
            "tier": tier_name,
            "prep_root": str(prep_root),
            "feed_root": str(feed_root),
            "audit_summary_path": str(audit_summary_path),
            "ready_at_epoch": int(time.time()),
            "source": "live_sfm_promotion",
            "selected_frames": selected_frames,
            "registered_images": registered_images,
        }
        prep_ready_path = self._prep_ready_file(run_name)
        prep_ready_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = prep_ready_path.with_suffix(prep_ready_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(promoted_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(prep_ready_path)
        return promoted_payload

    @staticmethod
    def _write_cancelled_marker(run_name: str, reason: str) -> None:
        summaries = Path(config.donor_output_directory) / f"hislam2_{run_name}" / "summaries"
        summaries.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_name": run_name,
            "reason": reason,
            "cancelled_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        marker = summaries / "CANCELLED.json"
        tmp_path = marker.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")
        tmp_path.replace(marker)

    def _cleanup_run_storage(
        self,
        run: ActiveJobRun,
        *,
        snapshot: Optional[Snapshot],
        keep_primary_artifact: bool,
    ) -> None:
        output_root = Path(config.donor_output_directory) / f"hislam2_{run.run_name}"
        logs_root = Path(config.donor_logs_directory) / run.run_name
        launch_log = Path(config.donor_logs_directory) / f"{run.run_name}.launch.log"
        pid_file = Path(config.donor_logs_directory) / f"{run.run_name}.pid"

        if keep_primary_artifact and config.retain_local_primary_artifact and snapshot and snapshot.artifact_path:
            artifact_path = Path(snapshot.artifact_path)
            if artifact_path.exists():
                retained_dir = Path(config.donor_retained_artifact_directory)
                retained_dir.mkdir(parents=True, exist_ok=True)
                retained_path = retained_dir / f"{run.job_id}_{artifact_path.name}"
                try:
                    if retained_path.exists():
                        retained_path.unlink()
                    shutil.move(str(artifact_path), str(retained_path))
                except Exception:
                    pass

        self._clear_prep_ready_marker(run.run_name)
        shutil.rmtree(run.local_input_path.parent, ignore_errors=True)
        shutil.rmtree(output_root, ignore_errors=True)
        shutil.rmtree(logs_root, ignore_errors=True)

        stream_logs: list[Path] = []
        for tier in run.tier_plan:
            stream_logs.extend(
                [
                    Path(config.donor_logs_directory) / f"{run.run_name}.{tier.name}.stream_prewarm.log",
                    Path(config.donor_logs_directory) / f"{run.run_name}.{tier.name}.stream_live_sfm.log",
                    Path(config.donor_logs_directory) / f"{run.run_name}.{tier.name}.stream_train.log",
                ]
            )

        for path in (launch_log, pid_file, run.wrapper_log_path, *stream_logs):
            if not path:
                continue
            try:
                Path(path).unlink()
            except FileNotFoundError:
                continue

    @staticmethod
    def _elapsed_seconds_for_run(run_name: str) -> Optional[int]:
        output_root = Path(config.donor_output_directory) / f"hislam2_{run_name}"
        launch_log = Path(config.donor_logs_directory) / f"{run_name}.launch.log"
        summaries = output_root / "summaries"
        return WorkerAgent._elapsed_seconds(output_root, launch_log, summaries)

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return int(stat.st_size), int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))

    def _maybe_stage_single_artifact_upload(
        self,
        run: ActiveJobRun,
        *,
        artifact_path: Path,
        storage_key: str,
        artifact_type: str,
        staged_attr: str,
        staged_path_attr: str,
        staged_signature_attr: str,
        pending_signature_attr: str,
        pending_polls_attr: str,
    ) -> None:
        if not artifact_path.exists() or not artifact_path.is_file():
            return
        try:
            signature = self._file_signature(artifact_path)
        except OSError:
            return
        if signature[0] <= 0:
            return
        if getattr(run, pending_signature_attr) != signature:
            setattr(run, pending_signature_attr, signature)
            setattr(run, pending_polls_attr, 1)
            return
        setattr(run, pending_polls_attr, int(getattr(run, pending_polls_attr) or 0) + 1)
        if int(getattr(run, pending_polls_attr) or 0) < max(1, config.stream_artifact_stage_min_stable_polls):
            return
        if (
            getattr(run, staged_signature_attr) == signature
            and getattr(run, staged_path_attr) == str(artifact_path)
        ):
            return

        manifest = self.storage.upload_file(
            storage_key=storage_key,
            local_path=artifact_path,
            artifact_type=artifact_type,
        )
        setattr(run, staged_attr, manifest)
        setattr(run, staged_path_attr, str(artifact_path))
        setattr(run, staged_signature_attr, signature)

    def _maybe_stage_artifact_bundle_upload(self, run: ActiveJobRun, snapshot: Snapshot) -> None:
        if not config.stream_artifact_stage_enabled:
            return
        output_root = Path(config.donor_output_directory) / f"hislam2_{run.run_name}"

        output_prefix = run.assignment["output_prefix"].rstrip("/")
        artifact_path = Path(snapshot.artifact_path) if snapshot.artifact_path else output_root / f"{run.current_tier.name}_out" / "3dgs_final.ply"
        self._maybe_stage_single_artifact_upload(
            run,
            artifact_path=artifact_path,
            storage_key=f"{output_prefix}/{artifact_path.name}",
            artifact_type="primary_artifact",
            staged_attr="staged_primary_artifact",
            staged_path_attr="staged_primary_artifact_path",
            staged_signature_attr="staged_primary_artifact_signature",
            pending_signature_attr="pending_primary_artifact_signature",
            pending_polls_attr="pending_primary_artifact_stable_polls",
        )

        summary_path = (
            Path(snapshot.summary_path)
            if snapshot.summary_path
            else output_root / "summaries" / f"{run.current_tier.name}_full_summary.json"
        )
        self._maybe_stage_single_artifact_upload(
            run,
            artifact_path=summary_path,
            storage_key=f"{output_prefix}/{summary_path.name}",
            artifact_type="metrics",
            staged_attr="staged_metrics_artifact",
            staged_path_attr="staged_metrics_artifact_path",
            staged_signature_attr="staged_metrics_artifact_signature",
            pending_signature_attr="pending_metrics_artifact_signature",
            pending_polls_attr="pending_metrics_artifact_stable_polls",
        )

        verdict_path = (
            Path(snapshot.verdict_path)
            if snapshot.verdict_path
            else output_root / "summaries" / f"{run.current_tier.name}_full_verdict.json"
        )
        self._maybe_stage_single_artifact_upload(
            run,
            artifact_path=verdict_path,
            storage_key=f"{output_prefix}/{verdict_path.name}",
            artifact_type="viewer_manifest",
            staged_attr="staged_viewer_manifest_artifact",
            staged_path_attr="staged_viewer_manifest_artifact_path",
            staged_signature_attr="staged_viewer_manifest_artifact_signature",
            pending_signature_attr="pending_viewer_manifest_artifact_signature",
            pending_polls_attr="pending_viewer_manifest_artifact_stable_polls",
        )

    @staticmethod
    def _artifact_manifest_signature(manifest: dict[str, Any]) -> tuple[tuple[str, str], ...]:
        signature_items: list[tuple[str, str]] = []
        for key in ("primary_artifact", "metrics", "viewer_manifest", "preview"):
            item = manifest.get(key) or {}
            storage_key = str(item.get("storage_key") or "").strip()
            checksum = str(item.get("checksum_sha256") or "").strip()
            if storage_key:
                signature_items.append((key, f"{storage_key}|{checksum}"))
        return tuple(sorted(signature_items))

    def _build_staged_manifest(self, run: ActiveJobRun) -> dict[str, Any]:
        manifest: dict[str, Any] = {}
        if run.staged_primary_artifact:
            manifest["primary_artifact"] = dict(run.staged_primary_artifact)
        if run.staged_metrics_artifact:
            manifest["metrics"] = dict(run.staged_metrics_artifact)
        if run.staged_viewer_manifest_artifact:
            manifest["viewer_manifest"] = dict(run.staged_viewer_manifest_artifact)
        return manifest

    def _maybe_publish_staged_artifact_manifest(self, run: ActiveJobRun, snapshot: Snapshot) -> None:
        normalized_stage = (snapshot.stage or "").strip().lower()
        if (
            not config.stream_artifact_publish_early
            and normalized_stage not in {"export", "completed"}
            and not normalized_stage.startswith("export")
        ):
            return
        manifest = self._build_staged_manifest(run)
        if not manifest.get("primary_artifact"):
            return
        signature = self._artifact_manifest_signature(manifest)
        if not signature or signature == run.published_artifact_manifest_signature:
            return
        try:
            self.client.upload_artifact_manifest(run.job_id, {"worker_id": self.worker_id, "manifest": manifest})
        except requests_exceptions.RequestException:
            return
        run.published_artifact_manifest_signature = signature

    def _cleanup_staged_artifacts(self, run: ActiveJobRun) -> None:
        for staged in (
            run.staged_primary_artifact or {},
            run.staged_metrics_artifact or {},
            run.staged_viewer_manifest_artifact or {},
        ):
            storage_key = str(staged.get("storage_key") or "").strip()
            if not storage_key:
                continue
            try:
                self.storage.delete_object(storage_key)
            except Exception:
                pass
        run.staged_primary_artifact = None
        run.staged_primary_artifact_path = None
        run.staged_primary_artifact_signature = None
        run.pending_primary_artifact_signature = None
        run.pending_primary_artifact_stable_polls = 0
        run.staged_metrics_artifact = None
        run.staged_metrics_artifact_path = None
        run.staged_metrics_artifact_signature = None
        run.pending_metrics_artifact_signature = None
        run.pending_metrics_artifact_stable_polls = 0
        run.staged_viewer_manifest_artifact = None
        run.staged_viewer_manifest_artifact_path = None
        run.staged_viewer_manifest_artifact_signature = None
        run.pending_viewer_manifest_artifact_signature = None
        run.pending_viewer_manifest_artifact_stable_polls = 0
        run.published_artifact_manifest_signature = None

    def _upload_artifacts(self, assignment: dict[str, Any], snapshot: Snapshot, *, run: Optional[ActiveJobRun] = None) -> dict[str, Any]:
        output_prefix = assignment["output_prefix"].rstrip("/")
        manifest: dict[str, Any] = {}

        if snapshot.artifact_path:
            artifact_path = Path(snapshot.artifact_path)
            storage_key = f"{output_prefix}/{artifact_path.name}"
            reused_staged = False
            if run is not None and run.staged_primary_artifact and run.staged_primary_artifact_path == str(artifact_path):
                try:
                    signature = self._file_signature(artifact_path)
                except OSError:
                    signature = None
                if signature is not None and signature == run.staged_primary_artifact_signature:
                    manifest["primary_artifact"] = dict(run.staged_primary_artifact)
                    reused_staged = True
            if not reused_staged:
                manifest["primary_artifact"] = self.storage.upload_file(
                    storage_key=storage_key,
                    local_path=artifact_path,
                    artifact_type="primary_artifact",
                )

        if config.upload_auxiliary_artifacts and snapshot.summary_path:
            summary_path = Path(snapshot.summary_path)
            storage_key = f"{output_prefix}/{summary_path.name}"
            reused_staged = False
            if run is not None and run.staged_metrics_artifact and run.staged_metrics_artifact_path == str(summary_path):
                try:
                    signature = self._file_signature(summary_path)
                except OSError:
                    signature = None
                if signature is not None and signature == run.staged_metrics_artifact_signature:
                    manifest["metrics"] = dict(run.staged_metrics_artifact)
                    reused_staged = True
            if not reused_staged:
                manifest["metrics"] = self.storage.upload_file(
                    storage_key=storage_key,
                    local_path=summary_path,
                    artifact_type="metrics",
                )

        if config.upload_auxiliary_artifacts and snapshot.verdict_path:
            verdict_path = Path(snapshot.verdict_path)
            storage_key = f"{output_prefix}/{verdict_path.name}"
            reused_staged = False
            if run is not None and run.staged_viewer_manifest_artifact and run.staged_viewer_manifest_artifact_path == str(verdict_path):
                try:
                    signature = self._file_signature(verdict_path)
                except OSError:
                    signature = None
                if signature is not None and signature == run.staged_viewer_manifest_artifact_signature:
                    manifest["viewer_manifest"] = dict(run.staged_viewer_manifest_artifact)
                    reused_staged = True
            if not reused_staged:
                manifest["viewer_manifest"] = self.storage.upload_file(
                    storage_key=storage_key,
                    local_path=verdict_path,
                    artifact_type="viewer_manifest",
                )

        if run is not None:
            run.staged_primary_artifact = manifest.get("primary_artifact")
            run.staged_primary_artifact_path = snapshot.artifact_path
            if snapshot.artifact_path:
                try:
                    run.staged_primary_artifact_signature = self._file_signature(Path(snapshot.artifact_path))
                except OSError:
                    run.staged_primary_artifact_signature = None
            run.staged_metrics_artifact = manifest.get("metrics")
            run.staged_metrics_artifact_path = snapshot.summary_path
            if snapshot.summary_path:
                try:
                    run.staged_metrics_artifact_signature = self._file_signature(Path(snapshot.summary_path))
                except OSError:
                    run.staged_metrics_artifact_signature = None
            run.staged_viewer_manifest_artifact = manifest.get("viewer_manifest")
            run.staged_viewer_manifest_artifact_path = snapshot.verdict_path
            if snapshot.verdict_path:
                try:
                    run.staged_viewer_manifest_artifact_signature = self._file_signature(Path(snapshot.verdict_path))
                except OSError:
                    run.staged_viewer_manifest_artifact_signature = None
            run.published_artifact_manifest_signature = self._artifact_manifest_signature(manifest)
        return manifest

    def _poll_local_snapshot(self, run_name: str) -> Snapshot:
        output_root = Path(config.donor_output_directory) / f"hislam2_{run_name}"
        summaries = output_root / "summaries"
        logs_root = Path(config.donor_logs_directory) / run_name
        launch_log = Path(config.donor_logs_directory) / f"{run_name}.launch.log"
        pid_file = Path(config.donor_logs_directory) / f"{run_name}.pid"
        tiers = list(SUPPORTED_TIER_NAMES)

        runtime = self._load_json(summaries / "RUNTIME_STATUS.json")
        cancelled = self._load_json(summaries / "CANCELLED.json")
        all_failed = self._load_json(summaries / "all_tiers_failed.json")
        success = self._load_json(summaries / "SUCCESS.json")
        active_worker = self._find_active_worker(run_name, pid_file)
        runtime_age = self._age_seconds(summaries / "RUNTIME_STATUS.json")

        selected = self._select_artifact(output_root, summaries, tiers)

        if cancelled:
            return Snapshot(
                state="cancelled",
                stage="cancelled",
                phase_name=None,
                current_tier=None,
                title="任务已取消",
                detail=cancelled.get("reason", "worker 已停止本地任务。"),
                progress_fraction=0.0,
                progress_basis="cancelled",
                elapsed_seconds=None,
                estimated_remaining_seconds=0,
                failure_reason="cancelled_by_user",
                failure_detail=cancelled.get("reason"),
            )

        if success and selected and Path(selected["artifactPath"]).exists():
            return Snapshot(
                state="completed",
                stage="completed",
                phase_name="artifact",
                current_tier=success.get("tier") or selected.get("tier"),
                title="结果已完成",
                detail="远端已经生成并选定最终产物。",
                progress_fraction=1.0,
                progress_basis="success",
                elapsed_seconds=self._elapsed_seconds(output_root, launch_log, summaries),
                estimated_remaining_seconds=0,
                artifact_path=selected.get("artifactPath"),
                summary_path=selected.get("summaryPath"),
                verdict_path=selected.get("verdictPath"),
            )

        if selected and selected.get("selectionMode") == "full_best_effort" and Path(selected["artifactPath"]).exists():
            return Snapshot(
                state="completed",
                stage="completed",
                phase_name="artifact",
                current_tier=selected.get("tier"),
                title="结果已完成",
                detail=self._best_effort_detail(selected),
                progress_fraction=1.0,
                progress_basis="best_effort_full_artifact",
                elapsed_seconds=self._elapsed_seconds(output_root, launch_log, summaries),
                estimated_remaining_seconds=0,
                artifact_path=selected.get("artifactPath"),
                summary_path=selected.get("summaryPath"),
                verdict_path=selected.get("verdictPath"),
                cancel_active_run=active_worker is not None,
            )

        if all_failed:
            reason, detail = self._summarize_all_tier_failures(summaries, logs_root, tiers)
            return Snapshot(
                state="failed",
                stage="failed",
                phase_name=None,
                current_tier=None,
                title="远端生成失败",
                detail=detail,
                progress_fraction=None,
                progress_basis="all_tiers_failed",
                elapsed_seconds=self._elapsed_seconds(output_root, launch_log, summaries),
                estimated_remaining_seconds=0,
                failure_reason=reason,
                failure_detail=detail,
            )

        if runtime:
            runtime_state = str(runtime.get("state") or "").strip().lower()
            stage = runtime.get("stage") or "queued"
            phase_name = runtime.get("phase_name")
            progress = self._normalize_progress(runtime.get("progress"))
            now_epoch = int(time.time())
            run_started_at_epoch = self._int_or_none(runtime.get("run_started_at_epoch"))
            phase_started_at_epoch = self._int_or_none(runtime.get("phase_started_at_epoch"))
            elapsed_seconds = (
                max(0, now_epoch - run_started_at_epoch)
                if run_started_at_epoch is not None
                else self._int_or_none(runtime.get("elapsed_sec")) or self._elapsed_seconds(output_root, launch_log, summaries)
            )
            estimated_remaining_seconds = self._int_or_none(runtime.get("estimated_remaining_sec"))
            current_tier = runtime.get("current_tier")
            runtime_with_heartbeat = dict(runtime)
            runtime_with_heartbeat["elapsed_sec"] = elapsed_seconds
            if phase_started_at_epoch is not None:
                runtime_with_heartbeat["phase_elapsed_seconds"] = max(0, now_epoch - phase_started_at_epoch)

            if runtime_state == "cancelled":
                return Snapshot(
                    state="cancelled",
                    stage=stage,
                    phase_name=phase_name,
                    current_tier=current_tier,
                    title="任务已取消",
                    detail=runtime.get("detail") or "worker 已停止本地任务。",
                    progress_fraction=progress,
                    progress_basis=runtime.get("progress_basis") or "runtime_cancelled",
                    elapsed_seconds=elapsed_seconds,
                    estimated_remaining_seconds=0,
                    failure_reason="cancelled_by_user",
                    failure_detail=runtime.get("detail"),
                )

            if runtime_state == "failed":
                reason = str(runtime.get("reason") or "runtime_failed")
                detail = runtime.get("detail") or "远端任务已失败。"
                if reason == "all_tiers_failed":
                    reason, detail = self._summarize_all_tier_failures(summaries, logs_root, tiers)
                return Snapshot(
                    state="failed",
                    stage=stage,
                    phase_name=phase_name,
                    current_tier=current_tier,
                    title="远端生成失败",
                    detail=detail,
                    progress_fraction=progress,
                    progress_basis=runtime.get("progress_basis") or "runtime_failure",
                    elapsed_seconds=elapsed_seconds,
                    estimated_remaining_seconds=0,
                    failure_reason=reason,
                    failure_detail=detail,
                )

            snapshot = Snapshot(
                state=self._job_state_for_runtime(stage, phase_name),
                stage=stage,
                phase_name=phase_name,
                current_tier=current_tier,
                title=runtime.get("title") or self._title_for_stage(stage, phase_name),
                detail=self._runtime_detail(runtime_with_heartbeat, stage, phase_name),
                progress_fraction=progress,
                progress_basis=runtime.get("progress_basis") or "runtime_status",
                elapsed_seconds=elapsed_seconds,
                estimated_remaining_seconds=estimated_remaining_seconds,
                metrics=self._runtime_metrics(runtime_with_heartbeat),
                artifact_path=selected.get("artifactPath") if selected else None,
                summary_path=selected.get("summaryPath") if selected else None,
                verdict_path=selected.get("verdictPath") if selected else None,
            )
            if not active_worker and runtime_age is not None and runtime_age > config.runtime_stale_timeout_sec:
                return Snapshot(
                    state="failed",
                    stage=snapshot.stage,
                    phase_name=snapshot.phase_name,
                    current_tier=snapshot.current_tier,
                    title="远端生成失败",
                    detail="远端已经没有活跃的 prep/train worker，而且状态超过阈值没有更新，这次任务已经卡住。",
                    progress_fraction=snapshot.progress_fraction,
                    progress_basis=snapshot.progress_basis,
                    elapsed_seconds=snapshot.elapsed_seconds,
                    estimated_remaining_seconds=0,
                    failure_reason="worker_stalled_or_runtime_stale",
                    failure_detail="runtime_status_stale_and_no_active_worker",
                )
            return snapshot

        prep_ready = self._load_json(self._prep_ready_file(run_name))
        if prep_ready:
            return Snapshot(
                state="assigned",
                stage="gpu_wait",
                phase_name="gpu_wait",
                current_tier=str(prep_ready.get("tier") or "") or None,
                title="预处理已完成",
                detail="当前这条任务的官方 HI-SLAM 预处理已完成，正在等待 GPU 训练槽。",
                progress_fraction=0.52,
                progress_basis="prep_ready_waiting_gpu",
                elapsed_seconds=self._elapsed_seconds(output_root, launch_log, summaries),
                estimated_remaining_seconds=None,
            )

        if active_worker:
            return Snapshot(
                state="reconstructing",
                stage="sfm",
                phase_name="prep",
                current_tier=self._tier_from_command(active_worker),
                title="正在运行官方 HI-SLAM 预处理",
                detail="worker 已经启动，本地正在运行官方 HI-SLAM 预处理。",
                progress_fraction=0.2,
                progress_basis="active_worker_without_runtime",
                elapsed_seconds=self._elapsed_seconds(output_root, launch_log, summaries),
                estimated_remaining_seconds=None,
            )

        return Snapshot(
            state="assigned",
            stage="queued",
            phase_name="bootstrap",
            current_tier=None,
            title="任务已分配",
            detail="worker 正在等待 donor pipeline 启动状态。",
            progress_fraction=0.18,
            progress_basis="worker_assigned",
            elapsed_seconds=self._elapsed_seconds(output_root, launch_log, summaries),
            estimated_remaining_seconds=None,
        )

    @staticmethod
    def _load_json(path: Path) -> Optional[dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except Exception:
            return None

    @staticmethod
    def _normalize_progress(value: Any) -> Optional[float]:
        if not isinstance(value, (int, float)):
            return None
        progress = float(value)
        if progress > 1.0:
            progress /= 100.0
        return max(0.0, min(1.0, progress))

    @staticmethod
    def _int_or_none(value: Any) -> Optional[int]:
        if isinstance(value, (int, float)):
            return int(value)
        return None

    @classmethod
    def _runtime_metrics(cls, runtime: dict[str, Any]) -> dict[str, Any]:
        metrics: dict[str, Any] = {}
        current_units = cls._int_or_none(runtime.get("current_units"))
        target_units = cls._int_or_none(runtime.get("target_units"))
        if current_units is not None:
            metrics["current_units"] = current_units
        if target_units is not None:
            metrics["target_units"] = target_units

        unit_label = str(runtime.get("unit_label") or "").strip()
        if unit_label:
            metrics["unit_label"] = unit_label

        progress_basis = str(runtime.get("progress_basis") or "").strip()
        if progress_basis:
            metrics["progress_basis"] = progress_basis

        metric_int_fields = (
            "downloaded_bytes",
            "extracted_frames",
            "accepted_live_frames",
            "selected_frames",
            "registered_images",
            "visible_bytes",
            "visible_chunk_count",
            "total_chunks",
            "phase_elapsed_seconds",
            "matcher_block_index",
            "matcher_block_total",
            "matcher_block_row_index",
            "matcher_block_row_total",
            "matcher_block_col_index",
            "matcher_block_col_total",
            "matcher_block_started_at_epoch",
            "matcher_block_elapsed_sec",
        )
        for field_name in metric_int_fields:
            metric_value = cls._int_or_none(runtime.get(field_name))
            if metric_value is not None:
                metrics[field_name] = metric_value

        current_round = str(runtime.get("current_round") or "").strip()
        if current_round:
            metrics["current_round"] = current_round

        current_profile = str(runtime.get("current_profile") or "").strip()
        if current_profile:
            metrics["current_profile"] = current_profile

        upload_completed = runtime.get("upload_completed")
        if isinstance(upload_completed, bool):
            metrics["upload_completed"] = upload_completed

        return metrics

    @classmethod
    def _runtime_detail(cls, runtime: dict[str, Any], stage: str, phase_name: Optional[str]) -> str:
        detail = str(runtime.get("detail") or "").strip()
        metrics = cls._runtime_metrics(runtime)
        current_units = metrics.get("current_units")
        target_units = metrics.get("target_units")
        unit_label = str(metrics.get("unit_label") or "").strip()

        if current_units is not None and target_units is not None:
            ratio = f"{current_units}/{target_units}"
            if unit_label:
                ratio = f"{ratio} {unit_label}"
            if ratio not in detail:
                if detail:
                    trimmed = detail[:-1] if detail.endswith("。") else detail
                    return f"{trimmed}（{ratio}）。"
                return cls._fallback_runtime_detail(stage, phase_name, ratio)

        if detail:
            return detail
        return cls._fallback_runtime_detail(stage, phase_name, None)

    @staticmethod
    def _fallback_runtime_detail(stage: str, phase_name: Optional[str], ratio: Optional[str]) -> str:
        normalized_stage = (stage or "").strip().lower()
        normalized_phase = (phase_name or "").strip().lower()

        if normalized_phase in {"extract_frames", "extract_frames_live"}:
            suffix = f"（{ratio}）。" if ratio else "。"
            return f"正在从原始视频里抽取帧图像{suffix}"
        if normalized_phase in {"matcher", "match"} or normalized_stage == "sfm_match":
            suffix = f"（{ratio}）。" if ratio else "。"
            return f"正在匹配相邻视角{suffix}"
        if normalized_stage.startswith("train") or normalized_phase == "full":
            suffix = f"（{ratio}）。" if ratio else "。"
            return f"正在运行官方 HI-SLAM 训练{suffix}"
        if normalized_stage.startswith("export"):
            suffix = f"（{ratio}）。" if ratio else "。"
            return f"正在整理官方 HI-SLAM 输出{suffix}"
        if normalized_stage.startswith("prep") or normalized_stage == "sfm":
            suffix = f"（{ratio}）。" if ratio else "。"
            return f"正在运行官方 HI-SLAM 预处理{suffix}"
        return "远端正在继续处理任务。"

    @staticmethod
    def _job_state_for_runtime(stage: str, phase_name: Optional[str]) -> str:
        normalized_stage = (stage or "").strip().lower()
        if normalized_stage == "sfm" or normalized_stage.startswith("sfm") or normalized_stage.startswith("prep"):
            return "reconstructing"
        if normalized_stage == "train" or normalized_stage.startswith("train"):
            return "training_full"
        if normalized_stage == "export" or normalized_stage.startswith("export"):
            return "exporting"
        return "queued"

    @staticmethod
    def _title_for_stage(stage: str, phase_name: Optional[str]) -> str:
        normalized_stage = (stage or "").strip().lower()
        if normalized_stage == "sfm" or normalized_stage.startswith("sfm") or normalized_stage.startswith("prep"):
            return "正在运行官方 HI-SLAM 预处理"
        if normalized_stage == "train" or normalized_stage.startswith("train"):
            return "远端正在做官方 HI-SLAM 训练"
        if normalized_stage == "export" or normalized_stage.startswith("export"):
            return "正在整理官方 HI-SLAM 输出"
        return "任务处理中"

    @staticmethod
    def _age_seconds(path: Path) -> Optional[int]:
        if not path.exists():
            return None
        try:
            return max(0, int(time.time() - path.stat().st_mtime))
        except OSError:
            return None

    @staticmethod
    def _elapsed_seconds(output_root: Path, launch_log: Path, summaries: Path) -> Optional[int]:
        timestamps: list[float] = []
        for path in (output_root, launch_log, summaries):
            if path.exists():
                try:
                    timestamps.append(path.stat().st_mtime)
                except OSError:
                    pass
        if not timestamps:
            return None
        return max(0, int(time.time() - min(timestamps)))

    @staticmethod
    def _tier_from_command(command: str) -> Optional[str]:
        for tier in SUPPORTED_TIER_NAMES:
            if tier in command:
                return tier
        return None

    @staticmethod
    def _select_artifact(output_root: Path, summaries: Path, tiers: list[str]) -> Optional[dict[str, Any]]:
        success_path = summaries / "SUCCESS.json"
        success = WorkerAgent._load_json(success_path)
        if success:
            artifact_path_raw = str(success.get("artifact_path") or "").strip()
            if artifact_path_raw:
                artifact_path = Path(artifact_path_raw)
                if artifact_path.exists():
                    summary_path_raw = str(success.get("summary_path") or "").strip()
                    verdict_path_raw = str(success.get("verdict_path") or "").strip()
                    verdict_exists = verdict_path_raw and Path(verdict_path_raw).exists()
                    return {
                        "artifactPath": str(artifact_path),
                        "summaryPath": summary_path_raw if summary_path_raw and Path(summary_path_raw).exists() else None,
                        "verdictPath": verdict_path_raw if verdict_exists else None,
                        "tier": success.get("tier"),
                        "selectionMode": "full_best_effort" if bool(success.get("best_effort")) else "full_passed",
                        "qualityFailures": success.get("failures") or [],
                    }
        for tier in tiers:
            summary_path = summaries / f"{tier}_full_summary.json"
            verdict_path = summaries / f"{tier}_full_verdict.json"
            summary = WorkerAgent._load_json(summary_path)
            verdict = WorkerAgent._load_json(verdict_path)
            candidate = output_root / f"{tier}_out" / "3dgs_final.ply"
            if summary and summary.get("has_3dgs_final") and candidate.exists():
                return {
                    "artifactPath": str(candidate),
                    "summaryPath": str(summary_path),
                    "verdictPath": str(verdict_path) if verdict_path.exists() else None,
                    "tier": tier,
                    "selectionMode": "full_passed" if verdict and verdict.get("passed") is True else "full_best_effort",
                    "qualityFailures": verdict.get("failures") if verdict else [],
                }
        return None

    @staticmethod
    def _best_effort_detail(selected: dict[str, Any]) -> str:
        tier = selected.get("tier") or "unknown_tier"
        failures = [str(item) for item in (selected.get("qualityFailures") or []) if item]
        if failures:
            return (
                f"远端已经生成可展示 3DGS，但 {tier} 没通过质量门限（{', '.join(failures)}）；"
                "已按 best-effort 发布，并停止继续 fallback。"
            )
        return f"远端已经生成可展示 3DGS，已按 {tier} 的 best-effort 结果发布，并停止继续 fallback。"

    @staticmethod
    def _summarize_all_tier_failures(
        summaries: Path,
        logs_root: Path,
        tiers: list[str],
        *,
        phase: str,
    ) -> tuple[str, str]:
        compact: list[str] = []
        details: list[str] = []
        for tier in tiers:
            failure = WorkerAgent._load_json(summaries / f"{tier}_prep_failure.json")
            if not failure:
                audit_failure = WorkerAgent._load_json(summaries / f"{tier}_audit_failure.json")
                if audit_failure:
                    reason = audit_failure.get("reason") or "audit_gate_failed"
                    detail = (
                        audit_failure.get("detail")
                        or
                        WorkerAgent._extract_audit_failure_detail(
                            summaries / f"{tier}_audit_summary.json",
                            logs_root / f"{tier}_audit.log",
                        )
                        or reason
                    )
                    compact.append(f"{tier}={reason}")
                    details.append(f"- {tier}: {detail}")
                    continue
                probe_failure = WorkerAgent._load_json(summaries / f"{tier}_probe_failure.json")
                if probe_failure:
                    reason = probe_failure.get("reason") or "probe_failed"
                    detail = probe_failure.get("detail") or WorkerAgent._extract_failure_detail(logs_root / f"{tier}_probe.log") or reason
                    compact.append(f"{tier}={reason}")
                    details.append(f"- {tier}: {detail}")
                    continue
                full_failure = WorkerAgent._load_json(summaries / f"{tier}_full_failure.json")
                if full_failure:
                    reason = full_failure.get("reason") or "full_failed"
                    detail = full_failure.get("detail") or WorkerAgent._extract_failure_detail(logs_root / f"{tier}_full.log") or reason
                    compact.append(f"{tier}={reason}")
                    details.append(f"- {tier}: {detail}")
                continue
            reason = failure.get("reason") or "prep_failed"
            detail = failure.get("detail") or WorkerAgent._extract_failure_detail(logs_root / f"{tier}_prep.log") or reason
            compact.append(f"{tier}={reason}")
            details.append(f"- {tier}: {detail}")
        if not compact:
            if phase == "prepping":
                return ("prep_unknown_failure", "远端正式预处理没有成功结束，也没有留下明确失败标记。")
            if len(tiers) == 1:
                return ("full_no_final_3dgs", "远端完整训练已经跑完，但没有产出最终可交互的 3DGS 成果。")
            return ("all_tiers_failed", "远端尝试了多个 tier，但这次没有生成可用结果。")
        if len(compact) == 1 and compact[0].endswith("=audit_gate_failed"):
            return ("audit_gate_failed", details[0].removeprefix("- ").strip())
        if len(compact) == 1 and compact[0].endswith("=full_no_final_3dgs"):
            return ("full_no_final_3dgs", details[0].removeprefix("- ").strip())
        heading = "这次失败发生在远端训练/导出阶段：" if all("full_" in item or "probe_" in item for item in compact) else "这次失败发生在远端处理阶段："
        return ("all_tiers_failed:" + ";".join(compact), "\n".join([heading] + details))

    @staticmethod
    def _extract_failure_detail(log_path: Path) -> Optional[str]:
        if not log_path.exists():
            return None
        text = log_path.read_text(errors="ignore")
        lowered = text.lower()
        if "no images with matches found in the database" in lowered:
            if "cameras.txt" in lowered or "failed to create sparse model" in lowered:
                return "没有建立出足够的图像匹配，未生成可用 sparse / cameras.txt"
            return "没有建立出足够的图像匹配"
        if "feature_extractor" in lowered and "sigkill" in lowered:
            return "COLMAP feature_extractor 被 SIGKILL 中止，疑似内存压力"
        if "failed to create sparse model" in lowered:
            return "未能建立可用的 sparse model"
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and line.strip() not in {"{", "}", "[", "]"}
        ]
        return lines[-1] if lines else None

    @staticmethod
    def _extract_audit_failure_detail(summary_path: Path, log_path: Path) -> Optional[str]:
        summary = WorkerAgent._load_json(summary_path)
        if summary:
            gate_metrics = summary.get("gate_metrics") or {}
            processed = gate_metrics.get("processed_live_frames") or summary.get("processed_live_frames")
            accepted = gate_metrics.get("accepted_frames") or summary.get("accepted")
            feed_fps = gate_metrics.get("feed_fps") or summary.get("feed_fps")
            accept_rate = gate_metrics.get("accept_rate")
            too_bright_rate = gate_metrics.get("too_bright_rate") or summary.get("too_bright_rate")
            parts: list[str] = []
            if processed is not None and accepted is not None:
                parts.append(f"已审核 {accepted}/{processed} 帧")
            if feed_fps is not None:
                parts.append(f"feed_fps={float(feed_fps):.2f}")
            if accept_rate is not None:
                parts.append(f"accept_rate={float(accept_rate):.2f}")
            if too_bright_rate is not None:
                parts.append(f"too_bright_rate={float(too_bright_rate):.2f}")
            if parts:
                return "视角审核没有通过训练前门槛（" + "，".join(parts) + "）。"
        return WorkerAgent._extract_failure_detail(log_path)

    @staticmethod
    def _find_active_worker(run_name: str, pid_file: Path) -> Optional[str]:
        runner_pid = None
        if pid_file.exists():
            try:
                runner_pid = int(pid_file.read_text().strip())
            except Exception:
                runner_pid = None

        rows: list[tuple[int, int, str]] = []
        output = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True, stderr=subprocess.DEVNULL)
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            try:
                rows.append((int(parts[0]), int(parts[1]), parts[2]))
            except ValueError:
                continue

        children_by_parent: dict[int, list[tuple[int, str]]] = {}
        for pid, ppid, command in rows:
            children_by_parent.setdefault(ppid, []).append((pid, command))

        descendant_pids: set[int] = set()
        if runner_pid is not None:
            stack = [runner_pid]
            while stack:
                parent = stack.pop()
                for child_pid, _ in children_by_parent.get(parent, []):
                    if child_pid in descendant_pids:
                        continue
                    descendant_pids.add(child_pid)
                    stack.append(child_pid)

        worker_markers = (
            "incremental_video_prewarm.py",
            "incremental_live_sfm.py",
            "incremental_train_seed.py",
            "remote_run_hislam2_realvideo_prep_audit_phase.sh",
            "remote_run_hislam2_realvideo_train_phase.sh",
            "prepare_real_video_owndata.py",
            "preprocess_owndata.py",
            "audit_real_video_frames.py",
            "demo.py",
            "colmap",
            "feature_extractor",
            "mapper",
            "bundle_adjuster",
            "model_converter",
        )
        for pid, _ppid, command in rows:
            if "<<'PY'" in command or "ps -axo pid=,ppid=,command=" in command:
                continue
            if run_name in command or pid in descendant_pids:
                if any(marker in command for marker in worker_markers):
                    return command
        return None

    @staticmethod
    def _cancel_run(run_name: str) -> None:
        pid_file = Path(config.donor_logs_directory) / f"{run_name}.pid"
        runner_pid: Optional[int] = None
        if pid_file.exists():
            try:
                runner_pid = int(pid_file.read_text().strip())
            except Exception:
                runner_pid = None

        rows: list[tuple[int, int, str]] = []
        try:
            output = subprocess.check_output(
                ["ps", "-axo", "pid=,ppid=,command="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.SubprocessError:
            output = ""

        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            try:
                rows.append((int(parts[0]), int(parts[1]), parts[2]))
            except ValueError:
                continue

        children_by_parent: dict[int, list[int]] = {}
        for pid, ppid, _command in rows:
            children_by_parent.setdefault(ppid, []).append(pid)

        target_pids: set[int] = set()
        worker_markers = (
            "incremental_video_prewarm.py",
            "incremental_live_sfm.py",
            "incremental_train_seed.py",
            "remote_run_hislam2_realvideo_prep_audit_phase.sh",
            "remote_run_hislam2_realvideo_train_phase.sh",
            "prepare_real_video_owndata.py",
            "preprocess_owndata.py",
            "audit_real_video_frames.py",
            "demo.py",
            "colmap",
            "feature_extractor",
            "mapper",
            "bundle_adjuster",
            "model_converter",
        )

        stack: list[int] = []
        if runner_pid is not None:
            target_pids.add(runner_pid)
            stack.append(runner_pid)

        for pid, _ppid, command in rows:
            if run_name in command and any(marker in command for marker in worker_markers):
                if pid not in target_pids:
                    target_pids.add(pid)
                    stack.append(pid)

        while stack:
            parent_pid = stack.pop()
            for child_pid in children_by_parent.get(parent_pid, []):
                if child_pid in target_pids:
                    continue
                target_pids.add(child_pid)
                stack.append(child_pid)

        if not target_pids:
            return

        for sig in (signal.SIGTERM, signal.SIGKILL):
            for pid in sorted(target_pids, reverse=True):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    continue
                except PermissionError:
                    continue
            if sig == signal.SIGTERM:
                time.sleep(0.5)

        deadline = time.time() + 12.0
        while time.time() < deadline:
            try:
                if WorkerAgent._find_active_worker(run_name, pid_file) is None:
                    break
            except subprocess.SubprocessError:
                break
            time.sleep(0.25)


def main() -> None:
    WorkerAgent(ControlPlaneClient(config.control_plane_base_url), ObjectStorageClient()).run_forever()


if __name__ == "__main__":
    main()
