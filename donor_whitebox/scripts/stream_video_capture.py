#!/usr/bin/env python3
import argparse
import shutil
import time
from pathlib import Path

import cv2


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--video", required=True)
    p.add_argument("--dst-root", required=True)
    p.add_argument("--fps", type=float, default=0.0)
    p.add_argument("--realtime-scale", type=float, default=1.0)
    p.add_argument("--jpeg-quality", type=int, default=95)
    p.add_argument("--copy-from-root", default=None)
    return p.parse_args()


def copy_metadata(copy_from_root: Path, dst_root: Path):
    for name in ("calib.txt", "traj_gt.txt", "manifest.txt", "sequence_summary.json"):
        src = copy_from_root / name
        if src.exists():
            shutil.copy2(src, dst_root / name)


def main():
    args = parse_args()
    video_path = Path(args.video)
    dst_root = Path(args.dst_root)
    dst_images = dst_root / "images"
    dst_images.mkdir(parents=True, exist_ok=True)

    if args.copy_from_root:
        copy_metadata(Path(args.copy_from_root), dst_root)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"failed opening video {video_path}")

    fps = args.fps if args.fps > 0 else cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        raise RuntimeError(f"invalid fps for {video_path}: {fps}")
    frame_dt = 1.0 / fps
    start = time.monotonic()
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        timestamp = index * frame_dt
        name = f"{index:06d}_{timestamp:.6f}.jpg"
        out_path = dst_images / name
        if not cv2.imwrite(str(out_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), args.jpeg_quality]):
            raise RuntimeError(f"failed writing {out_path}")

        target_elapsed = timestamp * args.realtime_scale
        actual_elapsed = time.monotonic() - start
        if actual_elapsed < target_elapsed:
            time.sleep(target_elapsed - actual_elapsed)
        index += 1

    cap.release()
    (dst_root / ".complete").write_text("ok\n", encoding="utf-8")
    print(f"[stream-video-capture] frames={index} fps={fps:.6f} dst={dst_root}")


if __name__ == "__main__":
    main()
