from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any, Optional

import requests
from requests import exceptions as requests_exceptions

from .config import config


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
        if last_error is not None:
            raise last_error
        return {}

    @staticmethod
    def capability_flags() -> dict[str, Any]:
        pipeline_families: list[str] = ["object_slam3r_surface_v1"] if config.claim_enabled else []
        return {
            "pipeline_families": pipeline_families,
            "supports_default_surface_asset": True,
            "supports_hq_gaussian": True,
            "standby_mode": not config.claim_enabled,
            "standby_note": config.standby_note,
            "official_stack": ["SLAM3R", "Sparse2DGS", "SuGaR", "3D-HGS"],
            "official_command_templates_pinned": bool(
                config.slam3r_command_template
                and config.sparse2dgs_command_template
                and config.sugar_command_template
                and config.hgs_command_template
            ),
        }

    def register(self, *, worker_id: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
            "software_version": (
                "object-slam3r-surface-v1-0.2.1"
                if config.claim_enabled
                else "object-slam3r-surface-v1-0.2.1-standby"
            ),
            "capability_flags": self.capability_flags(),
        }
        if worker_id:
            payload["worker_id"] = worker_id
        return self._json("POST", "/v1/workers/register", payload)

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
        if not config.claim_enabled:
            return None
        payload = self._json(
            "POST",
            f"/v1/workers/{worker_id}/claim-next",
            {
                "max_concurrent_jobs": 1,
                "gpu_model": config.gpu_model,
                "gpu_count": config.gpu_count,
                "vram_mb": config.vram_mb,
                "disk_free_mb": self._disk_free_mb(),
                "capability_flags": self.capability_flags(),
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

    @staticmethod
    def _default_host_fingerprint() -> str:
        return f"{socket.gethostname()}:{socket.getfqdn()}"

    @staticmethod
    def _disk_free_mb() -> int:
        usage = shutil.disk_usage(config.local_root)
        return int(usage.free // (1024 * 1024))


def push_runtime(
    client: ControlPlaneClient,
    *,
    job_id: str,
    worker_id: str,
    state: str,
    stage: str,
    title: str,
    detail: str,
    progress_fraction: Optional[float] = None,
    phase_name: Optional[str] = None,
    current_tier: Optional[str] = None,
    progress_basis: Optional[str] = None,
    metrics: Optional[dict[str, Any]] = None,
) -> None:
    client.runtime(
        job_id,
        {
            "worker_id": worker_id,
            "state": state,
            "stage": stage,
            "phase_name": phase_name,
            "current_tier": current_tier,
            "title": title,
            "detail": detail,
            "progress_fraction": progress_fraction,
            "progress_basis": progress_basis,
            "metrics": metrics or {},
        },
    )
