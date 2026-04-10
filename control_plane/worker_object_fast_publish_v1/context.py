from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .config import config


@dataclass
class JobContext:
    job_id: str
    worker_id: str
    assignment: dict[str, Any]
    root_dir: Path
    input_dir: Path
    output_dir: Path
    input_video: Path
    output_prefix: str
    current_stage: str = "created"
    frames_dir: Optional[Path] = None
    curated_dir: Optional[Path] = None
    sfm_dir: Optional[Path] = None
    masks_dir: Optional[Path] = None
    support_dir: Optional[Path] = None
    surface_dir: Optional[Path] = None
    default_publish_dir: Optional[Path] = None
    hq_dir: Optional[Path] = None
    should_run_hq_refine: bool = True

    @property
    def pipeline_profile(self) -> dict[str, Any]:
        return dict(self.assignment.get("pipeline_profile") or {})

    @property
    def stack(self) -> dict[str, Any]:
        profile = self.pipeline_profile
        value = profile.get("stack")
        return dict(value) if isinstance(value, dict) else {}

    def pipeline_string(self, key: str, default: str) -> str:
        value = self.pipeline_profile.get(key)
        if value is None:
            return default
        return str(value)

    def pipeline_float(self, key: str, default: float) -> float:
        value = self.pipeline_profile.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def pipeline_int(self, key: str, default: int) -> int:
        value = self.pipeline_profile.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def from_assignment(cls, assignment: dict[str, Any], *, worker_id: str) -> "JobContext":
        job_id = str(assignment["job_id"])
        input_storage_key = str(assignment["input"]["storage_key"])
        input_name = Path(input_storage_key).name or "capture.mov"
        root_dir = Path(config.local_jobs_directory) / job_id
        input_dir = root_dir / "input"
        output_dir = root_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        hq_enabled = str(
            (assignment.get("pipeline_profile") or {}).get("hq_refine") or "optional_3dgs"
        ).lower() not in {"off", "none", "disabled", "false"}
        return cls(
            job_id=job_id,
            worker_id=worker_id,
            assignment=assignment,
            root_dir=root_dir,
            input_dir=input_dir,
            output_dir=output_dir,
            input_video=input_dir / input_name,
            output_prefix=str(assignment.get("output_prefix") or f"artifacts/{job_id}/"),
            should_run_hq_refine=hq_enabled,
        )
