from __future__ import annotations

import unittest
from datetime import timedelta

from control_plane.app.repository import InMemoryRepository, WorkerRow, utcnow
from control_plane.app.models import JobState, WorkerState


def _worker_payload(
    *,
    worker_id: str | None = None,
    host_fingerprint: str,
    capability_flags: dict[str, object] | None = None,
    gpu_count: int = 1,
    vram_mb: int = 32768,
    disk_free_mb: int = 200000,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "vast",
        "region": "eu-north",
        "instance_label": "danish-5090",
        "host_fingerprint": host_fingerprint,
        "gpu_model": "RTX 5090",
        "gpu_count": gpu_count,
        "vram_mb": vram_mb,
        "cpu_cores": 32,
        "ram_mb": 131072,
        "disk_free_mb": disk_free_mb,
        "software_version": "worker-0.2.0",
        "capability_flags": capability_flags or {"hislam2": True, "3dgs": True, "worker_pull": True},
    }
    if worker_id:
        payload["worker_id"] = worker_id
    return payload


class RepositorySchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemoryRepository()

    def _create_ready_job(self, *, file_name: str = "demo.mov"):
        row = self.repo.create_job(
            tenant_id="tenant_demo",
            user_id="user_demo",
            client_record_id=None,
            capture_origin="ios",
            file_name=file_name,
            content_type="video/quicktime",
            size_bytes=32 * 1024 * 1024,
        )
        return self.repo.update_job(
            row.job_id,
            input_storage_key=f"uploads/{row.job_id}/{file_name}",
            state=JobState.QUEUED,
            title="视频已上传",
            detail="等待 GPU",
        )

    def test_register_worker_reuses_host_identity_and_retires_idle_duplicates(self) -> None:
        now = utcnow()
        canonical = WorkerRow(
            worker_id="worker_zombie",
            provider="vast",
            region="eu-north",
            instance_label="danish-5090",
            host_fingerprint="host-a",
            gpu_model="RTX 5090",
            gpu_count=1,
            vram_mb=32768,
            cpu_cores=32,
            ram_mb=131072,
            disk_free_mb=200000,
            software_version="worker-0.1.0",
            capability_flags={"hislam2": True, "3dgs": True, "worker_pull": True},
            state=WorkerState.IDLE,
            current_job_id=None,
            last_heartbeat_at=now,
            updated_at=now,
        )
        stale_duplicate = WorkerRow(
            worker_id="worker_stale_dup",
            provider="vast",
            region="eu-north",
            instance_label="danish-5090",
            host_fingerprint="host-a",
            gpu_model="RTX 5090",
            gpu_count=1,
            vram_mb=32768,
            cpu_cores=32,
            ram_mb=131072,
            disk_free_mb=200000,
            software_version="worker-0.0.9",
            capability_flags={"hislam2": True, "3dgs": True, "worker_pull": True},
            state=WorkerState.IDLE,
            current_job_id=None,
            last_heartbeat_at=now - timedelta(minutes=5),
            updated_at=now - timedelta(minutes=5),
        )
        self.repo.workers[canonical.worker_id] = canonical
        self.repo.workers[stale_duplicate.worker_id] = stale_duplicate

        row = self.repo.register_worker(**_worker_payload(host_fingerprint="host-a"))

        self.assertEqual(row.worker_id, "worker_zombie")
        self.assertEqual(self.repo.workers["worker_zombie"].state, WorkerState.IDLE)
        self.assertLess(self.repo.workers["worker_zombie"].last_heartbeat_at, utcnow() + timedelta(seconds=1))
        self.assertEqual(self.repo.workers["worker_stale_dup"].state, WorkerState.OFFLINE)

    def test_reserve_idle_worker_skips_ineligible_worker(self) -> None:
        job = self._create_ready_job()
        weak = self.repo.register_worker(
            **_worker_payload(
                host_fingerprint="host-weak",
                vram_mb=8192,
            )
        )
        strong = self.repo.register_worker(**_worker_payload(host_fingerprint="host-strong"))

        worker = self.repo.reserve_idle_worker_for_job(job.job_id)

        self.assertIsNotNone(worker)
        assert worker is not None
        self.assertEqual(worker.worker_id, strong.worker_id)
        self.assertNotEqual(worker.worker_id, weak.worker_id)

    def test_claim_next_requires_worker_capabilities(self) -> None:
        job = self._create_ready_job(file_name="portrait.mov")
        weak = self.repo.register_worker(
            **_worker_payload(
                host_fingerprint="host-bad-cap",
                capability_flags={"hislam2": False, "3dgs": True, "worker_pull": True},
            )
        )
        strong = self.repo.register_worker(**_worker_payload(host_fingerprint="host-good-cap"))

        none_assignment = self.repo.claim_next_job(
            weak.worker_id,
            min_chunk_ready_bytes=0,
            gpu_count=weak.gpu_count,
            vram_mb=weak.vram_mb,
            disk_free_mb=weak.disk_free_mb,
            capability_flags=weak.capability_flags,
        )
        assigned = self.repo.claim_next_job(
            strong.worker_id,
            min_chunk_ready_bytes=0,
            gpu_count=strong.gpu_count,
            vram_mb=strong.vram_mb,
            disk_free_mb=strong.disk_free_mb,
            capability_flags=strong.capability_flags,
        )

        self.assertIsNone(none_assignment)
        self.assertIsNotNone(assigned)
        assert assigned is not None
        self.assertEqual(assigned.job_id, job.job_id)
        self.assertEqual(assigned.assigned_worker_id, strong.worker_id)


if __name__ == "__main__":
    unittest.main()
