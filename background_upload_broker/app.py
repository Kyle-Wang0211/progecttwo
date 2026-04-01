from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import uuid
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dispatcher import GPUDispatcher
from models import ArtifactPayload, JobRecord, JobState, UploadRequest


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.uploads_dir = self.root / "uploads"
        self.artifacts_dir = self.root / "artifacts"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "jobs.json"
        self.lock = threading.RLock()
        self.jobs: Dict[str, JobRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.manifest_path.exists():
            return
        try:
            payload = json.loads(self.manifest_path.read_text())
        except Exception:
            return
        jobs_payload = payload.get("jobs", [])
        for item in jobs_payload:
            try:
                job = JobRecord.from_persisted_json(item)
            except Exception:
                continue
            self.jobs[job.job_id] = job

    def _save(self) -> None:
        payload = {"jobs": [job.to_persisted_json() for job in self.jobs.values()]}
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(self.manifest_path)

    def create_job(
        self,
        *,
        file_name: str,
        file_size_bytes: int,
        content_type: str,
        capture_origin: str,
        client_record_id: Optional[str],
    ) -> JobRecord:
        with self.lock:
            job_id = f"job_{uuid.uuid4().hex}"
            now = time.time()
            local_upload_path = self.uploads_dir / f"{job_id}_{file_name}"
            local_artifact_path = self.artifacts_dir / f"{job_id}.ply"
            job = JobRecord(
                job_id=job_id,
                file_name=file_name,
                file_size_bytes=file_size_bytes,
                content_type=content_type,
                capture_origin=capture_origin,
                client_record_id=client_record_id,
                created_at_epoch=now,
                updated_at_epoch=now,
                state=JobState.UPLOADING,
                title="正在上传到 broker",
                detail="系统后台上传完成后，broker 会自动把任务调度到可用 GPU。",
                progress_fraction=0.0,
                elapsed_seconds=0,
                estimated_remaining_seconds=None,
                progress_basis="upload_bytes",
                local_upload_path=str(local_upload_path),
                local_artifact_path=str(local_artifact_path),
                upload_started_at_epoch=now,
            )
            self.jobs[job_id] = job
            self._save()
            return job

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self.lock:
            return self.jobs.get(job_id)

    def update(self, job_id: str, changes: Dict[str, Any]) -> Optional[JobRecord]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            for key, value in changes.items():
                if key == "state" and isinstance(value, JobState):
                    setattr(job, key, value)
                elif key == "artifact" and isinstance(value, ArtifactPayload):
                    setattr(job, key, value)
                else:
                    setattr(job, key, value)
            job.updated_at_epoch = time.time()
            job.elapsed_seconds = max(0, int(job.updated_at_epoch - job.created_at_epoch))
            self._save()
            return job

    def to_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            job.elapsed_seconds = max(0, int(time.time() - job.created_at_epoch))
            self._save()
            return job.to_status_json()


class BrokerRuntime:
    def __init__(self, root: Path) -> None:
        self.store = JobStore(root)
        self.dispatcher = GPUDispatcher()
        self.public_base_url = os.environ.get("BROKER_PUBLIC_BASE_URL", "").strip()
        self.server: Optional[ThreadingHTTPServer] = None
        self.refresh_min_interval_sec = float(os.environ.get("BROKER_REFRESH_MIN_INTERVAL_SEC", "4"))
        self._refresh_lock = threading.Lock()
        self._refresh_inflight: set[str] = set()

    def build_public_url(self, path: str, request: Optional[BaseHTTPRequestHandler] = None) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/") + path
        if request is None:
            raise RuntimeError("public_base_url_not_configured")
        host = request.headers.get("Host")
        if not host:
            raise RuntimeError("missing_host_header")
        return f"http://{host}{path}"

    def build_public_url_from_host(self, path: str, host: Optional[str]) -> str:
        if self.public_base_url:
            return self.public_base_url.rstrip("/") + path
        if not host:
            raise RuntimeError("missing_host_header")
        return f"http://{host}{path}"

    def create_job(self, payload: Dict[str, Any], request: BaseHTTPRequestHandler) -> Dict[str, Any]:
        job = self.store.create_job(
            file_name=payload["fileName"],
            file_size_bytes=int(payload["fileSizeBytes"]),
            content_type=payload["contentType"],
            capture_origin=payload.get("captureOrigin", "unknown"),
            client_record_id=payload.get("clientRecordId"),
        )
        upload_url = self.build_public_url(f"/v1/uploads/{job.job_id}", request=request)
        return {
            "jobId": job.job_id,
            "upload": UploadRequest(
                method="PUT",
                url=upload_url,
                headers={"Content-Type": job.content_type},
            ).to_json(),
            "pollPath": f"/v1/mobile-jobs/{job.job_id}",
            "cancelPath": f"/v1/mobile-jobs/{job.job_id}",
        }

    def complete_upload(self, job_id: str, request: BaseHTTPRequestHandler) -> Dict[str, Any]:
        job = self.store.get(job_id)
        if not job:
            raise KeyError("job_not_found")
        if job.cancellation_requested:
            self.store.update(
                job_id,
                {
                    "state": JobState.CANCELLED,
                    "title": "任务已取消",
                    "detail": "用户在上传完成前已取消这次任务。",
                    "failure_reason": "cancelled_by_user",
                },
            )
            return self.must_get_status(job_id)

        self.store.update(
            job_id,
            {
                "state": JobState.QUEUED,
                "title": "broker 已接收视频",
                "detail": "正在调度可用 GPU worker。",
                "upload_completed_at_epoch": time.time(),
                "progress_fraction": 0.10,
                "progress_basis": "upload_complete",
            },
        )
        worker_assignment = self.dispatcher.assign(job_id)
        self.store.update(job_id, {"worker_assignment": worker_assignment})
        thread = threading.Thread(
            target=self.dispatcher.run_job,
            kwargs={
                "job": self.store.get(job_id),
                "update_job": self.store.update,
                "build_public_url": partial(self.build_public_url, request=request),
            },
            daemon=True,
            name=f"broker-job-{job_id}",
        )
        thread.start()
        return self.must_get_status(job_id)

    def cancel_job(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            raise KeyError("job_not_found")
        self.store.update(
            job_id,
            {
                "cancellation_requested": True,
                "state": JobState.CANCELLED,
                "title": "任务已取消",
                "detail": "后台不会继续推进这条任务。",
                "failure_reason": "cancelled_by_user",
            },
        )
        try:
            self.dispatcher.cancel_remote_job(job)
        except Exception:
            pass

    def _should_refresh_job(
        self,
        job: JobRecord,
    ) -> bool:
        active_states = {
            JobState.QUEUED,
            JobState.RECONSTRUCTING,
            JobState.TRAINING,
            JobState.PACKAGING,
            JobState.DOWNLOADING,
        }
        if job.state in active_states:
            return True
        return (
            job.state == JobState.FAILED
            and job.failure_reason == "worker_stalled_or_runtime_stale"
        )

    def _refresh_active_job_if_needed(
        self,
        job: JobRecord,
        request: Optional[BaseHTTPRequestHandler] = None,
    ) -> None:
        if not self._should_refresh_job(job):
            return
        if not job.remote_run_name:
            return
        if job.cancellation_requested:
            return
        request_host = request.headers.get("Host") if request is not None else None
        now = time.time()
        if (
            job.last_runtime_seen_at_epoch is not None
            and now - job.last_runtime_seen_at_epoch < self.refresh_min_interval_sec
        ):
            return
        with self._refresh_lock:
            if job.job_id in self._refresh_inflight:
                return
            self._refresh_inflight.add(job.job_id)

        def refresh() -> None:
            try:
                self.dispatcher.refresh_job(
                    job.job_id,
                    job.remote_run_name,
                    self.store.update,
                    partial(self.build_public_url_from_host, host=request_host),
                )
            except Exception:
                # Keep serving the last known status instead of tearing down the
                # HTTP response when a transient broker->GPU refresh fails.
                return
            finally:
                with self._refresh_lock:
                    self._refresh_inflight.discard(job.job_id)

        threading.Thread(
            target=refresh,
            daemon=True,
            name=f"broker-refresh-{job.job_id}",
        ).start()

    def must_get_status(self, job_id: str, request: Optional[BaseHTTPRequestHandler] = None) -> Dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError("job_not_found")
        self._refresh_active_job_if_needed(job, request=request)
        status = self.store.to_status(job_id)
        if status is None:
            raise KeyError("job_not_found")
        return status

    def get_artifact_bytes(self, job_id: str) -> bytes:
        job = self.store.get(job_id)
        if not job:
            raise KeyError("job_not_found")
        local_path = self.dispatcher.ensure_local_artifact(job)
        if not local_path or not local_path.exists():
            raise FileNotFoundError("artifact_not_ready")
        self.store.update(
            job_id,
            {
                "artifact": ArtifactPayload(
                    download_url=self.build_public_url(f"/v1/mobile-jobs/{job_id}/artifact"),
                    format="ply",
                ),
                "local_artifact_path": str(local_path),
            },
        )
        return local_path.read_bytes()


class BrokerHandler(BaseHTTPRequestHandler):
    server_version = "Aether3DBroker/0.2"

    @property
    def runtime(self) -> BrokerRuntime:
        return self.server.runtime  # type: ignore[attr-defined]

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/health":
                return self._json(HTTPStatus.OK, {"ok": True})

            if path.startswith("/v1/mobile-jobs/") and path.endswith("/artifact"):
                job_id = path.split("/")[3]
                try:
                    payload = self.runtime.get_artifact_bytes(job_id)
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {"detail": "job_not_found"})
                except FileNotFoundError:
                    return self._json(HTTPStatus.CONFLICT, {"detail": "artifact_not_ready"})
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            if path.startswith("/v1/mobile-jobs/"):
                job_id = path.split("/")[3]
                try:
                    status = self.runtime.must_get_status(job_id, request=self)
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {"detail": "job_not_found"})
                return self._json(HTTPStatus.OK, status)

            self._json(HTTPStatus.NOT_FOUND, {"detail": "not_found"})
            return
        except Exception as error:
            traceback.print_exc()
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": f"broker_get_failed:{error}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path != "/v1/mobile-jobs":
                return self._json(HTTPStatus.NOT_FOUND, {"detail": "not_found"})
            payload = self._read_json()
            response = self.runtime.create_job(payload, self)
        except KeyError as error:
            return self._json(HTTPStatus.BAD_REQUEST, {"detail": f"missing_field:{error}"})
        except Exception as error:
            traceback.print_exc()
            return self._json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
        self._json(HTTPStatus.OK, response)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/v1/uploads/"):
                return self._json(HTTPStatus.NOT_FOUND, {"detail": "not_found"})

            job_id = parsed.path.split("/")[3]
            job = self.runtime.store.get(job_id)
            if not job:
                return self._json(HTTPStatus.NOT_FOUND, {"detail": "job_not_found"})

            content_length = int(self.headers.get("Content-Length", "0") or 0)
            if content_length <= 0:
                return self._json(HTTPStatus.BAD_REQUEST, {"detail": "missing_content_length"})

            upload_path = Path(job.local_upload_path or "")
            upload_path.parent.mkdir(parents=True, exist_ok=True)
            uploaded = 0
            with upload_path.open("wb") as handle:
                remaining = content_length
                while remaining > 0:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    uploaded += len(chunk)
                    remaining -= len(chunk)
                    self.runtime.store.update(
                        job_id,
                        {
                            "state": JobState.UPLOADING,
                            "title": "正在上传到 broker",
                            "detail": f"已上传 {uploaded / 1_048_576:.1f} MB / {content_length / 1_048_576:.1f} MB",
                            "progress_fraction": min(0.10, 0.10 * (uploaded / max(content_length, 1))),
                            "progress_basis": "upload_bytes",
                        },
                    )

            self.runtime.store.update(job_id, {"local_upload_path": str(upload_path)})
            status = self.runtime.complete_upload(job_id, self)
            self._json(HTTPStatus.OK, status)
        except Exception as error:
            traceback.print_exc()
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": f"broker_upload_failed:{error}"})

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            if not parsed.path.startswith("/v1/mobile-jobs/"):
                return self._json(HTTPStatus.NOT_FOUND, {"detail": "not_found"})
            job_id = parsed.path.split("/")[3]
            self.runtime.cancel_job(job_id)
        except KeyError:
            return self._json(HTTPStatus.NOT_FOUND, {"detail": "job_not_found"})
        except Exception as error:
            traceback.print_exc()
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"detail": f"broker_cancel_failed:{error}"})
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[broker] " + format % args + "\n")

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    root = Path(__file__).resolve().parent / "runtime"
    runtime = BrokerRuntime(root)
    server = ThreadingHTTPServer((host, port), BrokerHandler)
    server.runtime = runtime  # type: ignore[attr-defined]
    runtime.server = server
    return server


def main() -> None:
    host = os.environ.get("BROKER_BIND_HOST", "0.0.0.0")
    port = int(os.environ.get("BROKER_PORT", "8787"))
    server = create_server(host, port)
    print(f"[broker] listening on http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
