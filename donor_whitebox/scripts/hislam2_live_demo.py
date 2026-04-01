#!/usr/bin/env python3
import argparse
import os
import re
import resource
import sys
import time
from pathlib import Path

repo = Path(os.environ.get("HI_SLAM2_REPO", "/root/gs_refs/HI-SLAM2"))
sys.path.append(str(repo / "hislam2"))

import cv2  # noqa: E402
import lietorch  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from tqdm import tqdm  # noqa: E402

from hi2 import Hi2  # noqa: E402

rlimit = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (100000, rlimit[1]))


def prepare_frame(path, calib, undistort=False, cropborder=0):
    res = 341 * 640
    calib_np = np.loadtxt(calib, delimiter=" ")
    k = np.array([[calib_np[0], 0, calib_np[2]], [0, calib_np[1], calib_np[3]], [0, 0, 1]])
    image = cv2.imread(str(path))
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    intrinsics = torch.tensor(calib_np[:4])
    if len(calib_np) > 4 and undistort:
        image = cv2.undistort(image, k, calib_np[4:])
    if cropborder > 0:
        image = image[cropborder:-cropborder, cropborder:-cropborder]
        intrinsics[2:] -= cropborder

    h0, w0, _ = image.shape
    h1 = int(h0 * np.sqrt((res) / (h0 * w0)))
    w1 = int(w0 * np.sqrt((res) / (h0 * w0)))
    h1 = h1 - h1 % 8
    w1 = w1 - w1 % 8
    image = cv2.resize(image, (w1, h1))
    image = torch.as_tensor(image).permute(2, 0, 1)

    intrinsics[[0, 2]] *= (w1 / w0)
    intrinsics[[1, 3]] *= (h1 / h0)
    return image[None].contiguous(), intrinsics[None]


def streamed_file_list(imagedir: Path):
    valid = {".jpg", ".png", ".ppm"}
    return sorted(p for p in imagedir.iterdir() if p.suffix.lower() in valid)


def extract_timestamp(path: Path) -> float:
    matches = re.findall(r"[+]?(?:\d*\.\d+|\d+)", path.stem)
    if not matches:
        raise RuntimeError(f"failed extracting timestamp from {path.name}")
    return float(matches[-1])


def save_trajectory(hi2, traj_full, imagedir, output, start=0):
    t = hi2.video.counter.value
    tstamps = hi2.video.tstamp[:t]
    poses_wc = lietorch.SE3(hi2.video.poses[:t]).inv().data
    np.save(f"{output}/intrinsics.npy", hi2.video.intrinsics[0].cpu().numpy() * 8)

    tstamps_full = np.array([extract_timestamp(x) for x in streamed_file_list(Path(imagedir))[start:]])[..., np.newaxis]
    tstamps_kf = tstamps_full[tstamps.cpu().numpy().astype(int)]
    ttraj_kf = np.concatenate([tstamps_kf, poses_wc.cpu().numpy()], axis=1)
    np.savetxt(f"{output}/traj_kf.txt", ttraj_kf)
    if traj_full is not None:
        ttraj_full = np.concatenate([tstamps_full[:len(traj_full)], traj_full], axis=1)
        np.savetxt(f"{output}/traj_full.txt", ttraj_full)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--imagedir", type=str, required=True)
    parser.add_argument("--calib", type=str, required=True)
    parser.add_argument("--config", type=str, required=True)
    parser.add_argument("--output", default="outputs/live_demo")
    parser.add_argument("--gtdepthdir", type=str, default=None)
    parser.add_argument("--weights", default=str(repo / "pretrained_models/droid.pth"))
    parser.add_argument("--buffer", type=int, default=1000)
    parser.add_argument("--undistort", action="store_true")
    parser.add_argument("--cropborder", type=int, default=0)
    parser.add_argument("--droidvis", action="store_true")
    parser.add_argument("--gsvis", action="store_true")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--length", type=int, default=100000)
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--stable-seconds", type=float, default=1.0)
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    try:
        torch.multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass

    image_dir = Path(args.imagedir)
    complete_path = image_dir.parent / ".complete"
    processed = set()
    hi2 = None
    frame_index = 0
    pbar = tqdm(desc="Processing streamed keyframes")

    while frame_index < args.length:
        now = time.time()
        files = streamed_file_list(image_dir)[args.start:]
        ready = []
        for path in files:
            if path.name in processed:
                continue
            try:
                if now - path.stat().st_mtime < args.stable_seconds:
                    continue
            except FileNotFoundError:
                continue
            ready.append(path)

        if ready:
            complete_now = complete_path.exists()
            remaining = [p for p in files if p.name not in processed]
            only_ready_left = len(remaining) == len(ready)
            for i, path in enumerate(ready):
                image, intrinsics = prepare_frame(path, args.calib, args.undistort, args.cropborder)
                if hi2 is None:
                    args.image_size = [image.shape[2], image.shape[3]]
                    hi2 = Hi2(args)
                is_last = complete_now and only_ready_left and i == len(ready) - 1
                hi2.track(frame_index, image, intrinsics=intrinsics, is_last=is_last)
                processed.add(path.name)
                frame_index += 1
                pbar.update()
                pbar.set_description(f"Processing streamed keyframe {hi2.video.counter.value} gs {hi2.gs.gaussians._xyz.shape[0]}")
                if is_last or frame_index >= args.length:
                    break
            if complete_now and only_ready_left:
                break
        else:
            if complete_path.exists():
                remaining = [p for p in files if p.name not in processed]
                if not remaining:
                    break
            time.sleep(args.poll_interval)

    pbar.close()
    if hi2 is None:
        raise RuntimeError("no frames were processed")

    traj = hi2.terminate()
    save_trajectory(hi2, traj, args.imagedir, args.output, start=args.start)
    print("Done")


if __name__ == "__main__":
    main()
