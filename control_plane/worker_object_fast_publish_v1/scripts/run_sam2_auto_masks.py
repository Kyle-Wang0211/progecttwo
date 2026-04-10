#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np


def ensure_importable(repo_path: Path) -> None:
    repo_str = str(repo_path.resolve())
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)


def score_mask(mask: np.ndarray, predicted_iou: float, stability_score: float) -> float:
    height, width = mask.shape
    ys, xs = np.nonzero(mask)
    if xs.size == 0 or ys.size == 0:
        return float("-inf")
    cx = float(xs.mean()) / float(width)
    cy = float(ys.mean()) / float(height)
    center_distance = ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5
    center_score = max(0.0, 1.0 - center_distance * 1.8)
    area_ratio = float(mask.sum()) / float(width * height)
    area_score = 1.0 - min(abs(area_ratio - 0.18), 0.18) / 0.18
    return (predicted_iou * 4.0) + (stability_score * 3.0) + (center_score * 3.0) + max(area_score, 0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run official SAM2 auto masks on curated object images.")
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--sam2-repo", required=True)
    parser.add_argument("--sam2-checkpoint", required=True)
    parser.add_argument("--sam2-config", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--points-per-side", type=int, default=16)
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = Path(args.summary_json)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    ensure_importable(Path(args.sam2_repo))

    import torch
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
    from sam2.build_sam import build_sam2

    device = args.device
    if device == "cuda" and not torch.cuda.is_available():
        device = "cpu"

    image_model = build_sam2(args.sam2_config, args.sam2_checkpoint, device=device)
    generator = SAM2AutomaticMaskGenerator(
        image_model,
        points_per_side=max(8, args.points_per_side),
        pred_iou_thresh=0.72,
        stability_score_thresh=0.88,
        output_mode="binary_mask",
        min_mask_region_area=128,
    )

    payload: dict[str, object] = {
        "backend": "sam2",
        "device": device,
        "sam2_repo": args.sam2_repo,
        "sam2_checkpoint": args.sam2_checkpoint,
        "sam2_config": args.sam2_config,
        "images": [],
    }

    image_paths = sorted(
        [path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    )
    if not image_paths:
        raise RuntimeError("sam2_no_images")

    for image_path in image_paths:
        bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if bgr is None:
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        annotations = generator.generate(rgb)
        best_mask = None
        best_score = float("-inf")
        best_iou = 0.0
        best_stability = 0.0
        for annotation in annotations:
            segmentation = annotation.get("segmentation")
            if segmentation is None:
                continue
            mask = np.asarray(segmentation > 0, dtype=np.uint8)
            predicted_iou = float(annotation.get("predicted_iou", 0.0))
            stability_score = float(annotation.get("stability_score", 0.0))
            score = score_mask(mask, predicted_iou, stability_score)
            if score > best_score:
                best_score = score
                best_mask = mask
                best_iou = predicted_iou
                best_stability = stability_score

        if best_mask is None:
            continue

        mask_path = output_dir / f"{image_path.stem}.png"
        cv2.imwrite(str(mask_path), best_mask * 255)
        payload["images"].append(
            {
                "image": image_path.name,
                "mask": mask_path.name,
                "candidate_count": len(annotations),
                "predicted_iou": round(best_iou, 4),
                "stability_score": round(best_stability, 4),
                "selection_score": round(best_score, 4),
            }
        )

    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
