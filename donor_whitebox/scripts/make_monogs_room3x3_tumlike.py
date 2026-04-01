#!/usr/bin/env python3
import argparse
import math
import os
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hislam2-root", required=True)
    parser.add_argument("--sequence-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--depth-scale", type=float, default=5000.0)
    parser.add_argument("--overwrite-depth", action="store_true")
    return parser.parse_args()


def matrix_to_quaternion_xyzw(rot):
    trace = float(rot[0, 0] + rot[1, 1] + rot[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (rot[2, 1] - rot[1, 2]) / s
        qy = (rot[0, 2] - rot[2, 0]) / s
        qz = (rot[1, 0] - rot[0, 1]) / s
    elif rot[0, 0] > rot[1, 1] and rot[0, 0] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[0, 0] - rot[1, 1] - rot[2, 2]) * 2.0
        qw = (rot[2, 1] - rot[1, 2]) / s
        qx = 0.25 * s
        qy = (rot[0, 1] + rot[1, 0]) / s
        qz = (rot[0, 2] + rot[2, 0]) / s
    elif rot[1, 1] > rot[2, 2]:
        s = math.sqrt(1.0 + rot[1, 1] - rot[0, 0] - rot[2, 2]) * 2.0
        qw = (rot[0, 2] - rot[2, 0]) / s
        qx = (rot[0, 1] + rot[1, 0]) / s
        qy = 0.25 * s
        qz = (rot[1, 2] + rot[2, 1]) / s
    else:
        s = math.sqrt(1.0 + rot[2, 2] - rot[0, 0] - rot[1, 1]) * 2.0
        qw = (rot[1, 0] - rot[0, 1]) / s
        qx = (rot[0, 2] + rot[2, 0]) / s
        qy = (rot[1, 2] + rot[2, 1]) / s
        qz = 0.25 * s
    quat = np.array([qx, qy, qz, qw], dtype=np.float64)
    quat /= np.linalg.norm(quat) + 1e-12
    return quat


def load_cam2world_rows(traj_path):
    rows = []
    with open(traj_path, "r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.strip().split()
            if len(fields) != 17:
                continue
            timestamp = float(fields[0])
            cam2world = np.asarray([float(v) for v in fields[1:]], dtype=np.float64).reshape(4, 4, order="F")
            rows.append((timestamp, cam2world))
    return rows


def main():
    args = parse_args()
    hislam2_root = Path(args.hislam2_root).resolve()
    sequence_root = Path(args.sequence_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    images_dir = sequence_root / "images"
    traj_path = sequence_root / "traj_gt.txt"
    depth_dir = output_dir / "depth"
    rgb_link = output_dir / "rgb"

    sys.path.insert(0, str(hislam2_root / "hislam2"))
    from midas.omnidata import OmnidataModel

    output_dir.mkdir(parents=True, exist_ok=True)
    depth_dir.mkdir(parents=True, exist_ok=True)
    if rgb_link.is_symlink() or rgb_link.exists():
        if rgb_link.is_symlink() and rgb_link.resolve() == images_dir:
            pass
        elif rgb_link.is_dir():
            pass
        else:
            raise RuntimeError(f"unexpected rgb path: {rgb_link}")
    else:
        os.symlink(images_dir, rgb_link)

    image_paths = sorted(images_dir.glob("*.ppm"))
    if not image_paths:
        raise RuntimeError(f"no images found under {images_dir}")

    pose_rows = load_cam2world_rows(traj_path)
    if len(pose_rows) != len(image_paths):
        raise RuntimeError(f"trajectory/image mismatch: {len(pose_rows)} vs {len(image_paths)}")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    mean = torch.as_tensor([0.485, 0.456, 0.406], device=device)[:, None, None]
    stdv = torch.as_tensor([0.229, 0.224, 0.225], device=device)[:, None, None]
    resize = transforms.Resize((512, 512), antialias=True)
    depth_model = OmnidataModel(
        "depth",
        str(hislam2_root / "pretrained_models" / "omnidata_dpt_depth_v2.ckpt"),
        device=device,
    )

    rgb_txt = []
    depth_txt = []
    gt_txt = ["# timestamp tx ty tz qx qy qz qw"]

    for idx, (image_path, (timestamp, cam2world)) in enumerate(zip(image_paths, pose_rows)):
        rgb_rel = f"rgb/{image_path.name}"
        depth_name = image_path.name.rsplit(".", 1)[0] + ".png"
        depth_rel = f"depth/{depth_name}"
        depth_path = depth_dir / depth_name

        rgb_txt.append(f"{timestamp:.6f} {rgb_rel}")
        depth_txt.append(f"{timestamp:.6f} {depth_rel}")

        rot = cam2world[:3, :3]
        trans = cam2world[:3, 3]
        qx, qy, qz, qw = matrix_to_quaternion_xyzw(rot)
        gt_txt.append(
            f"{timestamp:.6f} {trans[0]:.6f} {trans[1]:.6f} {trans[2]:.6f} "
            f"{qx:.6f} {qy:.6f} {qz:.6f} {qw:.6f}"
        )

        if depth_path.exists() and not args.overwrite_depth:
            continue

        image = np.array(Image.open(image_path).convert("RGB"), copy=True)
        tensor = torch.from_numpy(image).permute(2, 0, 1).float().to(device) / 255.0
        tensor = tensor.sub(mean).div(stdv)
        input_size = tensor.shape[-2:]
        resized = resize(tensor).unsqueeze(0)
        depth = depth_model(resized)
        depth = depth[None] * 50.0
        depth = F.interpolate(depth, input_size, mode="bicubic", align_corners=False)
        depth = depth.float().squeeze().detach().cpu().numpy()
        depth = np.clip(depth, 0.0, None)
        depth_png = np.clip(depth * args.depth_scale, 0.0, 65535.0).astype(np.uint16)
        Image.fromarray(depth_png).save(depth_path)

        if (idx + 1) % 25 == 0 or idx + 1 == len(image_paths):
            print(f"[make_monogs_room3x3_tumlike] depth {idx + 1}/{len(image_paths)}")

    (output_dir / "rgb.txt").write_text("\n".join(rgb_txt) + "\n", encoding="utf-8")
    (output_dir / "depth.txt").write_text("\n".join(depth_txt) + "\n", encoding="utf-8")
    (output_dir / "groundtruth.txt").write_text("\n".join(gt_txt) + "\n", encoding="utf-8")
    (output_dir / "calib.txt").write_text((sequence_root / "calib.txt").read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[make_monogs_room3x3_tumlike] done output={output_dir}")


if __name__ == "__main__":
    main()
