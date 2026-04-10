from __future__ import annotations

import traceback
import time

from .config import config
from .claim_loop import run_once
from .runtime import ControlPlaneClient
from .storage_client import ObjectStorageClient


def main() -> None:
    client = ControlPlaneClient(config.control_plane_base_url)
    storage = ObjectStorageClient()
    registration = client.register(worker_id=None)
    worker_id = registration["worker_id"]
    heartbeat_interval_sec = int(registration.get("heartbeat_interval_sec") or config.heartbeat_interval_sec)
    print(
        f"[object_slam3r_surface_v1] worker started worker_id={worker_id} base_url={config.control_plane_base_url}",
        flush=True,
    )

    last_heartbeat = 0.0
    while True:
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval_sec:
            client.heartbeat(worker_id, state="idle", current_job_id=None)
            last_heartbeat = now
        try:
            claimed = run_once(client=client, storage=storage, worker_id=worker_id)
        except Exception:
            traceback.print_exc()
            print("[object_slam3r_surface_v1] worker loop exception", flush=True)
            claimed = False
            time.sleep(max(1.0, config.scheduler_tick_interval_sec))
        if not claimed:
            time.sleep(max(0.5, config.scheduler_tick_interval_sec))


if __name__ == "__main__":
    main()
