#!/usr/bin/env python3
import argparse
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
    parser.add_argument("--input-rgb-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    hislam2_root = Path(args.hislam2_root).resolve()
    input_rgb_dir = Path(args.input_rgb_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(hislam2_root / "hislam2"))
    from midas.omnidata import OmnidataModel

    image_paths = sorted(input_rgb_dir.glob("frame_*.png"))
    if not image_paths:
        raise RuntimeError(f"no input images under {input_rgb_dir}")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    mean = torch.as_tensor([0.485, 0.456, 0.406], device=device)[:, None, None]
    stdv = torch.as_tensor([0.229, 0.224, 0.225], device=device)[:, None, None]
    resize_in = transforms.Resize((512, 512), antialias=True)
    resize_out = transforms.Resize((args.height, args.width), antialias=True)
    depth_model = OmnidataModel(
        "depth",
        str(hislam2_root / "pretrained_models" / "omnidata_dpt_depth_v2.ckpt"),
        device=device,
    )

    for idx, image_path in enumerate(image_paths):
        out_path = output_dir / f"{idx:05d}.npy"
        if out_path.exists() and not args.overwrite:
            continue

        image = np.array(Image.open(image_path).convert("RGB"), copy=True)
        tensor = torch.from_numpy(image).permute(2, 0, 1).float().to(device) / 255.0
        tensor = resize_out(tensor)
        tensor = tensor.sub(mean).div(stdv)
        input_size = tensor.shape[-2:]
        resized = resize_in(tensor).unsqueeze(0)
        depth = depth_model(resized)
        depth = depth[None] * 50.0
        depth = F.interpolate(depth, input_size, mode="bicubic", align_corners=False)
        depth = depth.float().squeeze().detach().cpu().numpy()
        depth = np.clip(depth, 0.0, None).astype(np.float32)
        np.save(out_path, depth)

        if (idx + 1) % 25 == 0 or idx + 1 == len(image_paths):
            print(f"[make_wildgs_mono_priors_omnidata] depth {idx + 1}/{len(image_paths)}")

    print(f"[make_wildgs_mono_priors_omnidata] done output={output_dir}")


if __name__ == "__main__":
    main()
