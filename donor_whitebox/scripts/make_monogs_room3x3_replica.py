#!/usr/bin/env python3
import argparse
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
    parser.add_argument("--depth-scale", type=float, default=6553.5)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def load_cam2world_rows(traj_path: Path):
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
    results_dir = output_dir / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    images_dir = sequence_root / "images"
    traj_path = sequence_root / "traj_gt.txt"
    image_paths = sorted(images_dir.glob("*.ppm"))
    pose_rows = load_cam2world_rows(traj_path)
    if len(image_paths) != len(pose_rows):
        raise RuntimeError(f"trajectory/image mismatch: {len(pose_rows)} vs {len(image_paths)}")

    sys.path.insert(0, str(hislam2_root / "hislam2"))
    from midas.omnidata import OmnidataModel

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    mean = torch.as_tensor([0.485, 0.456, 0.406], device=device)[:, None, None]
    stdv = torch.as_tensor([0.229, 0.224, 0.225], device=device)[:, None, None]
    resize = transforms.Resize((512, 512), antialias=True)
    depth_model = OmnidataModel(
        "depth",
        str(hislam2_root / "pretrained_models" / "omnidata_dpt_depth_v2.ckpt"),
        device=device,
    )

    traj_lines = []
    for idx, (image_path, (_, cam2world)) in enumerate(zip(image_paths, pose_rows)):
        rgb_out = results_dir / f"frame{idx:06d}.jpg"
        depth_out = results_dir / f"depth{idx:06d}.png"
        if args.overwrite or not rgb_out.exists():
            image = Image.open(image_path).convert("RGB")
            image.save(rgb_out, quality=95)
        if args.overwrite or not depth_out.exists():
            image_np = np.array(Image.open(image_path).convert("RGB"), copy=True)
            tensor = torch.from_numpy(image_np).permute(2, 0, 1).float().to(device) / 255.0
            tensor = tensor.sub(mean).div(stdv)
            input_size = tensor.shape[-2:]
            resized = resize(tensor).unsqueeze(0)
            depth = depth_model(resized)
            depth = depth[None] * 50.0
            depth = F.interpolate(depth, input_size, mode="bicubic", align_corners=False)
            depth = depth.float().squeeze().detach().cpu().numpy()
            depth = np.clip(depth, 0.0, None)
            depth_png = np.clip(depth * args.depth_scale, 0.0, 65535.0).astype(np.uint16)
            Image.fromarray(depth_png).save(depth_out)
        traj_lines.append(" ".join(f"{v:.8f}" for v in cam2world.reshape(-1)))
        if (idx + 1) % 25 == 0 or idx + 1 == len(image_paths):
            print(f"[make_monogs_room3x3_replica] frames {idx + 1}/{len(image_paths)}")

    (output_dir / "traj.txt").write_text("\n".join(traj_lines) + "\n", encoding="utf-8")
    print(f"[make_monogs_room3x3_replica] done output={output_dir}")


if __name__ == "__main__":
    main()
