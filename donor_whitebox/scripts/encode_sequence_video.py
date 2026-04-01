#!/usr/bin/env python3
import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--images-dir", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--fps", type=float, required=True)
    p.add_argument("--codec", default="mp4v")
    return p.parse_args()


def extract_timestamp(path: Path):
    matches = re.findall(r"[+]?(?:\d*\.\d+|\d+)", path.stem)
    if not matches:
        return None
    return float(matches[-1])


def build_frame_timeline(frames, fallback_fps: float):
    timestamps = [extract_timestamp(frame) for frame in frames]
    if all(ts is not None for ts in timestamps):
        values = [float(ts) for ts in timestamps]
        monotonic = all(b > a for a, b in zip(values[:-1], values[1:]))
        if monotonic:
            return values

    frame_dt = 1.0 / max(fallback_fps, 1e-9)
    return [i * frame_dt for i in range(len(frames))]


def encode_with_ffmpeg(images_dir: Path, output: Path, fps: float) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False

    frames = sorted(images_dir.glob("*.jpg"))
    if not frames:
        return False
    timeline = build_frame_timeline(frames, fps)

    with tempfile.NamedTemporaryFile("w", suffix=".ffconcat", delete=False) as f:
        concat_path = Path(f.name)
        f.write("ffconcat version 1.0\n")
        for idx, frame_path in enumerate(frames):
            f.write(f"file '{frame_path.as_posix()}'\n")
            if idx + 1 < len(frames):
                dt = max(timeline[idx + 1] - timeline[idx], 1e-6)
                f.write(f"duration {dt:.9f}\n")
        # ffconcat requires the final file to appear twice for the last duration
        f.write(f"file '{frames[-1].as_posix()}'\n")

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-safe",
        "0",
        "-f",
        "concat",
        "-i",
        str(concat_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "vfr",
        "-movflags",
        "+faststart",
        str(output),
    ]
    try:
        subprocess.run(cmd, check=True)
        return True
    finally:
        concat_path.unlink(missing_ok=True)


def encode_with_cv2(frames, output: Path, fps: float, codec: str):
    import cv2

    first = cv2.imread(str(frames[0]), cv2.IMREAD_COLOR)
    if first is None:
        raise RuntimeError(f"failed reading first frame {frames[0]}")
    height, width = first.shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*codec)
    writer = cv2.VideoWriter(str(output), fourcc, fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"failed opening video writer for {output}")

    for frame_path in frames:
        frame = cv2.imread(str(frame_path), cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"failed reading {frame_path}")
        writer.write(frame)
    writer.release()


def main():
    args = parse_args()
    images_dir = Path(args.images_dir)
    output = Path(args.output)
    frames = sorted(images_dir.glob("*.jpg"))
    if not frames:
        raise RuntimeError(f"no jpg frames found in {images_dir}")

    output.parent.mkdir(parents=True, exist_ok=True)

    if encode_with_ffmpeg(images_dir, output, args.fps):
        backend = "ffmpeg"
    else:
        encode_with_cv2(frames, output, args.fps, args.codec)
        backend = "cv2"
    print(
        f"[encode-sequence-video] backend={backend} frames={len(frames)} "
        f"fps={args.fps:.6f} output={output}"
    )


if __name__ == "__main__":
    main()
