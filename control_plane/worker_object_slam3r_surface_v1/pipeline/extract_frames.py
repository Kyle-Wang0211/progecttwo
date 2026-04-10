from __future__ import annotations

import json
import shutil
import subprocess

from ..config import config
from ..context import JobContext


def extract_frames(ctx: JobContext) -> None:
    ctx.current_stage = "curate"
    assert ctx.frames_dir is not None
    for stale in ctx.frames_dir.glob("*"):
        if stale.is_file():
            stale.unlink()

    ffmpeg_bin = shutil.which(config.ffmpeg_bin) or config.ffmpeg_bin
    output_pattern = ctx.frames_dir / "%06d.jpg"
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(ctx.input_video),
        "-vf",
        f"fps={config.extract_fps}",
        "-q:v",
        "2",
        str(output_pattern),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg_extract_failed:{result.stderr.strip() or result.stdout.strip()}")

    frames = sorted(path.name for path in ctx.frames_dir.glob("*.jpg"))
    if not frames:
        raise RuntimeError("ffmpeg_extract_produced_no_frames")

    (ctx.frames_dir / "extract_frames.json").write_text(
        json.dumps(
            {
                "stage": "extract_frames",
                "input_video": str(ctx.input_video),
                "ffmpeg_bin": ffmpeg_bin,
                "fps": config.extract_fps,
                "frame_count": len(frames),
                "frames": frames,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
