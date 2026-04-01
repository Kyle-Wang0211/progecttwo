#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

import numpy as np
from PIL import Image

from postprocess_3dgs_ply import (
    Camera,
    find_matching_image,
    load_cameras_json,
    load_surface_points,
    project_points,
)


def ensure_sam2_importable(repo_path: Path | None) -> None:
    if repo_path is None:
        return
    repo_str = str(repo_path.resolve())
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)


def numeric_frame_names(images_dir: Path) -> list[str]:
    frame_names = [
        path.name
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}
    ]
    try:
        frame_names.sort(key=lambda name: int(Path(name).stem))
    except Exception:
        frame_names.sort()
    return frame_names


def make_seed_mask(camera: Camera, surface_points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pixels, _ = project_points(surface_points, camera)
    seed_mask = np.zeros((camera.height, camera.width), dtype=np.uint8)
    if pixels.shape[0] > 0:
        seed_mask[pixels[:, 1], pixels[:, 0]] = 1
    return seed_mask, pixels


def pick_seed_frame(
    cameras: list[Camera],
    images_dir: Path,
    surface_points: np.ndarray,
) -> tuple[Camera | None, Path | None, np.ndarray, np.ndarray]:
    best_camera: Camera | None = None
    best_image: Path | None = None
    best_seed_mask = np.zeros((0, 0), dtype=np.uint8)
    best_pixels = np.empty((0, 2), dtype=np.int32)
    best_score = -1
    for camera in cameras:
        image_path = find_matching_image(Path(camera.image_name).stem, images_dir)
        if image_path is None:
            continue
        seed_mask, pixels = make_seed_mask(camera, surface_points)
        score = int(pixels.shape[0])
        if score > best_score:
            best_score = score
            best_camera = camera
            best_image = image_path
            best_seed_mask = seed_mask
            best_pixels = pixels
    return best_camera, best_image, best_seed_mask, best_pixels


def seed_bbox(seed_pixels: np.ndarray, width: int, height: int, pad: int = 16) -> np.ndarray | None:
    if seed_pixels.shape[0] == 0:
        return None
    xs = seed_pixels[:, 0]
    ys = seed_pixels[:, 1]
    x0 = max(0, int(xs.min()) - pad)
    y0 = max(0, int(ys.min()) - pad)
    x1 = min(width - 1, int(xs.max()) + pad)
    y1 = min(height - 1, int(ys.max()) + pad)
    if x1 <= x0 or y1 <= y0:
        return None
    return np.asarray([x0, y0, x1, y1], dtype=np.float32)


def seed_overlap_score(mask: np.ndarray, seed_mask: np.ndarray, predicted_iou: float) -> float:
    overlap = float(np.count_nonzero(mask & seed_mask))
    coverage = 0.0
    seed_count = int(np.count_nonzero(seed_mask))
    if seed_count > 0:
        coverage = overlap / float(seed_count)
    return overlap + coverage * 10_000.0 + float(predicted_iou) * 1_000.0


def save_binary_mask(path: Path, mask: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((mask.astype(np.uint8)) * 255, mode="L").save(path)


def inference_context(device: str):
    import torch

    if device == "cuda" and torch.cuda.is_available():
        return torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.bfloat16)
    return torch.inference_mode(), nullcontext()


def build_models(
    *,
    repo_path: Path | None,
    model_id: str,
    config_name: str,
    checkpoint_path: str,
    device: str,
    prompt_mode: str,
):
    ensure_sam2_importable(repo_path)

    from sam2.sam2_image_predictor import SAM2ImagePredictor
    from sam2.sam2_video_predictor import SAM2VideoPredictor

    if checkpoint_path:
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        from sam2.build_sam import build_sam2, build_sam2_video_predictor

        image_model = build_sam2(config_name, checkpoint_path, device=device)
        image_predictor = SAM2ImagePredictor(image_model)
        mask_generator = None
        if prompt_mode == "auto":
            mask_generator = SAM2AutomaticMaskGenerator(
                image_model,
                points_per_side=32,
                pred_iou_thresh=0.80,
                stability_score_thresh=0.95,
                output_mode="binary_mask",
                min_mask_region_area=64,
            )
        video_predictor = build_sam2_video_predictor(config_name, checkpoint_path, device=device)
        return image_predictor, mask_generator, video_predictor

    image_predictor = SAM2ImagePredictor.from_pretrained(model_id, device=device)
    mask_generator = None
    if prompt_mode == "auto":
        from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator

        mask_generator = SAM2AutomaticMaskGenerator.from_pretrained(
            model_id,
            points_per_side=32,
            pred_iou_thresh=0.80,
            stability_score_thresh=0.95,
            output_mode="binary_mask",
            min_mask_region_area=64,
            device=device,
        )
    video_predictor = SAM2VideoPredictor.from_pretrained(model_id, device=device)
    return image_predictor, mask_generator, video_predictor


def select_mask_box_prompt(image_predictor, image_rgb: np.ndarray, bbox: np.ndarray, seed_mask: np.ndarray) -> tuple[np.ndarray | None, dict[str, float]]:
    image_predictor.set_image(image_rgb)
    masks, ious, _ = image_predictor.predict(
        box=bbox,
        multimask_output=True,
        normalize_coords=False,
    )
    if masks.size == 0:
        return None, {"candidate_count": 0}
    best_mask = None
    best_score = float("-inf")
    best_iou = 0.0
    for idx in range(masks.shape[0]):
        mask = np.asarray(masks[idx] > 0, dtype=bool)
        score = seed_overlap_score(mask, seed_mask > 0, float(ious[idx]))
        if score > best_score:
            best_score = score
            best_mask = mask
            best_iou = float(ious[idx])
    return best_mask, {
        "candidate_count": int(masks.shape[0]),
        "selected_predicted_iou": best_iou,
        "selection_score": float(best_score if np.isfinite(best_score) else 0.0),
    }


def select_mask_automatic(mask_generator, image_rgb: np.ndarray, seed_mask: np.ndarray) -> tuple[np.ndarray | None, dict[str, float]]:
    annotations = mask_generator.generate(image_rgb)
    if not annotations:
        return None, {"candidate_count": 0}
    best_mask = None
    best_score = float("-inf")
    best_iou = 0.0
    for annotation in annotations:
        segmentation = annotation.get("segmentation")
        if segmentation is None:
            continue
        mask = np.asarray(segmentation > 0, dtype=bool)
        score = seed_overlap_score(mask, seed_mask > 0, float(annotation.get("predicted_iou", 0.0)))
        if score > best_score:
            best_score = score
            best_mask = mask
            best_iou = float(annotation.get("predicted_iou", 0.0))
    return best_mask, {
        "candidate_count": int(len(annotations)),
        "selected_predicted_iou": best_iou,
        "selection_score": float(best_score if np.isfinite(best_score) else 0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate official SAM2 masks for an image sequence.")
    parser.add_argument("--images-dir", required=True)
    parser.add_argument("--surface-ply", required=True)
    parser.add_argument("--cameras-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--sam2-repo", default="")
    parser.add_argument("--sam2-model-id", default="facebook/sam2.1-hiera-large")
    parser.add_argument("--sam2-config", default="configs/sam2.1/sam2.1_hiera_l.yaml")
    parser.add_argument("--sam2-checkpoint", default="")
    parser.add_argument("--device", default="")
    parser.add_argument("--prompt-mode", choices=("box", "auto"), default="box")
    args = parser.parse_args()

    images_dir = Path(args.images_dir)
    surface_path = Path(args.surface_ply)
    cameras_path = Path(args.cameras_json)
    output_dir = Path(args.output_dir)
    summary_path = Path(args.summary_json)
    repo_path = Path(args.sam2_repo) if args.sam2_repo else None
    if args.device:
        device = args.device
    else:
        try:
            import torch

            device = "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "applied": False,
        "reason": None,
        "images_dir": str(images_dir),
        "surface_ply": str(surface_path),
        "cameras_json": str(cameras_path),
        "output_dir": str(output_dir),
        "prompt_mode": args.prompt_mode,
        "sam2_model_id": args.sam2_model_id,
        "sam2_config": args.sam2_config,
        "sam2_checkpoint": args.sam2_checkpoint or None,
        "device": device,
    }

    if not images_dir.is_dir():
        payload["reason"] = "missing_images_dir"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not surface_path.is_file():
        payload["reason"] = "missing_surface_ply"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if not cameras_path.is_file():
        payload["reason"] = "missing_cameras_json"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    cameras = load_cameras_json(cameras_path)
    surface_points = load_surface_points(surface_path)
    if not cameras:
        payload["reason"] = "empty_cameras"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    if surface_points.shape[0] == 0:
        payload["reason"] = "empty_surface_points"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    frame_names = numeric_frame_names(images_dir)
    frame_index_by_stem = {Path(name).stem: index for index, name in enumerate(frame_names)}
    seed_camera, seed_image_path, seed_mask, seed_pixels = pick_seed_frame(cameras, images_dir, surface_points)
    if seed_camera is None or seed_image_path is None or seed_pixels.shape[0] < 32:
        payload["reason"] = "insufficient_projected_seed"
        payload["seed_pixels"] = int(seed_pixels.shape[0])
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    seed_bbox_xyxy = seed_bbox(seed_pixels, seed_camera.width, seed_camera.height)
    if seed_bbox_xyxy is None:
        payload["reason"] = "invalid_seed_bbox"
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    seed_stem = seed_image_path.stem
    seed_frame_idx = frame_index_by_stem.get(seed_stem)
    if seed_frame_idx is None:
        payload["reason"] = "seed_frame_missing_from_sequence"
        payload["seed_stem"] = seed_stem
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    image_rgb = np.asarray(Image.open(seed_image_path).convert("RGB"), dtype=np.uint8)

    try:
        image_predictor, mask_generator, video_predictor = build_models(
            repo_path=repo_path,
            model_id=args.sam2_model_id,
            config_name=args.sam2_config,
            checkpoint_path=args.sam2_checkpoint,
            device=device,
            prompt_mode=args.prompt_mode,
        )
    except Exception as exc:
        payload["reason"] = "sam2_init_failed"
        payload["error"] = str(exc)
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    try:
        inference_mode, autocast_mode = inference_context(device)
        with inference_mode, autocast_mode:
            if args.prompt_mode == "auto":
                if mask_generator is None:
                    raise RuntimeError("automatic mask generator unavailable")
                seed_mask_refined, selection_stats = select_mask_automatic(mask_generator, image_rgb, seed_mask)
            else:
                seed_mask_refined, selection_stats = select_mask_box_prompt(
                    image_predictor,
                    image_rgb,
                    seed_bbox_xyxy,
                    seed_mask,
                )
            if seed_mask_refined is None:
                raise RuntimeError("failed to select SAM2 seed mask")

            inference_state = video_predictor.init_state(
                video_path=str(images_dir),
                async_loading_frames=False,
            )
            video_predictor.add_new_mask(
                inference_state=inference_state,
                frame_idx=int(seed_frame_idx),
                obj_id=1,
                mask=seed_mask_refined,
            )

            saved_frames = 0
            for out_frame_idx, _, out_mask_logits in video_predictor.propagate_in_video(inference_state):
                if out_frame_idx < 0 or out_frame_idx >= len(frame_names):
                    continue
                frame_name = frame_names[out_frame_idx]
                mask = (out_mask_logits[0] > 0.0).detach().cpu().numpy()
                save_binary_mask(output_dir / f"{Path(frame_name).stem}.png", mask)
                saved_frames += 1
    except Exception as exc:
        payload["reason"] = "sam2_inference_failed"
        payload["error"] = str(exc)
        summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return

    seed_overlap_pixels = int(np.count_nonzero(seed_mask_refined & (seed_mask > 0)))
    payload.update(
        {
            "applied": True,
            "generated": saved_frames,
            "seed_frame": seed_stem,
            "seed_frame_idx": int(seed_frame_idx),
            "seed_pixels": int(seed_pixels.shape[0]),
            "seed_bbox": [float(value) for value in seed_bbox_xyxy.tolist()],
            "seed_overlap_pixels": seed_overlap_pixels,
            "selection": selection_stats,
        }
    )
    summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
