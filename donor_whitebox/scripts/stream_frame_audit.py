#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--src-root", required=True)
    p.add_argument("--dst-root", required=True)
    p.add_argument("--poll-interval", type=float, default=0.5)
    p.add_argument("--stable-seconds", type=float, default=1.0)
    p.add_argument("--min-laplacian-var", type=float, default=8.0)
    p.add_argument("--min-mean-diff", type=float, default=2.5)
    p.add_argument("--force-keep-gap", type=int, default=6)
    p.add_argument("--min-brightness", type=float, default=20.0)
    p.add_argument("--max-brightness", type=float, default=235.0)
    p.add_argument("--downsample-width", type=int, default=160)
    p.add_argument("--depth-audit-backend", choices=["none", "unik3d"], default="none")
    p.add_argument("--min-depth-p50", type=float, default=0.0)
    p.add_argument("--max-near-ratio-05m", type=float, default=1.0)
    p.add_argument("--max-near-ratio-1m", type=float, default=1.0)
    p.add_argument("--max-near-ratio-2m", type=float, default=1.0)
    p.add_argument("--unik3d-repo", type=str, default="/root/gs_refs/UniK3D")
    p.add_argument("--unik3d-backbone", choices=["vits", "vitb", "vitl"], default="vitl")
    p.add_argument("--unik3d-device", type=str, default="cuda")
    p.add_argument("--unik3d-resolution-level", type=int, default=7)
    return p.parse_args()


def ensure_link(src: Path, dst: Path):
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def load_gray_small(path: Path, target_width: int):
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    if image.shape[1] > target_width:
        scale = target_width / float(image.shape[1])
        target_height = max(1, int(round(image.shape[0] * scale)))
        image = cv2.resize(image, (target_width, target_height), interpolation=cv2.INTER_AREA)
    return image


def list_final_jpgs(images_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in images_dir.glob("*.jpg")
        if not path.name.endswith(".tmp.jpg")
    )


class UniK3DDepthAudit:
    def __init__(self, args):
        self.args = args
        repo = Path(args.unik3d_repo)
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        import torch  # noqa: PLC0415
        from unik3d.models import UniK3D  # noqa: PLC0415

        device = args.unik3d_device
        if device == "cuda" and not torch.cuda.is_available():
            device = "cpu"
        self._torch = torch
        self.device = torch.device(device)
        self.model = UniK3D.from_pretrained(f"lpiccinelli/unik3d-{args.unik3d_backbone}")
        self.model.resolution_level = args.unik3d_resolution_level
        self.model = self.model.to(self.device).eval()

    def infer_stats(self, path: Path):
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        rgb_tensor = self._torch.from_numpy(rgb).permute(2, 0, 1)
        with self._torch.inference_mode():
            outputs = self.model.infer(rgb=rgb_tensor, camera=None, normalize=True, rays=None)
        depth = outputs["depth"].detach().float().squeeze().cpu().numpy()
        valid = np.isfinite(depth) & (depth > 0)
        if not np.any(valid):
            return None
        depth_valid = depth[valid]
        return {
            "depth_p50": float(np.percentile(depth_valid, 50)),
            "depth_p95": float(np.percentile(depth_valid, 95)),
            "near_ratio_05m": float(np.mean(depth_valid < 0.5)),
            "near_ratio_1m": float(np.mean(depth_valid < 1.0)),
            "near_ratio_2m": float(np.mean(depth_valid < 2.0)),
        }


def main():
    args = parse_args()
    src_root = Path(args.src_root)
    dst_root = Path(args.dst_root)
    src_images = src_root / "images"
    dst_images = dst_root / "images"
    dst_images.mkdir(parents=True, exist_ok=True)

    audit_log = dst_root / "audit_log.jsonl"
    summary_path = dst_root / "audit_summary.json"
    src_complete = src_root / ".complete"
    dst_complete = dst_root / ".complete"

    processed = set()
    kept_gap = 0
    kept_count = 0
    reject_count = 0
    last_kept_gray = None
    reject_reasons = {}
    depth_stats_history = []
    depth_auditor = None
    if args.depth_audit_backend == "unik3d":
        depth_auditor = UniK3DDepthAudit(args)

    while True:
        if (src_root / "calib.txt").exists() and not (dst_root / "calib.txt").exists():
            shutil.copy2(src_root / "calib.txt", dst_root / "calib.txt")

        now = time.time()
        candidates = list_final_jpgs(src_images)
        ready = []
        for path in candidates:
            if path.name in processed:
                continue
            try:
                if now - path.stat().st_mtime < args.stable_seconds:
                    continue
            except FileNotFoundError:
                continue
            ready.append(path)

        with audit_log.open("a", encoding="utf-8") as log_fp:
            for path in ready:
                processed.add(path.name)
                gray = load_gray_small(path, args.downsample_width)
                if gray is None:
                    reject_count += 1
                    reject_reasons["read_failed"] = reject_reasons.get("read_failed", 0) + 1
                    log_fp.write(json.dumps({"frame": path.name, "accepted": False, "reason": "read_failed"}) + "\n")
                    continue

                lap_var = float(cv2.Laplacian(gray, cv2.CV_32F).var())
                brightness = float(gray.mean())
                mean_diff = None
                accepted = False
                reason = "bootstrap"
                depth_stats = None

                if last_kept_gray is None:
                    accepted = True
                else:
                    mean_diff = float(np.mean(np.abs(gray.astype(np.float32) - last_kept_gray.astype(np.float32))))
                    if brightness < args.min_brightness:
                        reason = "too_dark"
                    elif brightness > args.max_brightness:
                        reason = "too_bright"
                    elif lap_var < args.min_laplacian_var and kept_gap < args.force_keep_gap:
                        reason = "too_blurry"
                    elif mean_diff < args.min_mean_diff and kept_gap < args.force_keep_gap:
                        reason = "too_similar"
                    else:
                        accepted = True
                        reason = "accepted"

                if accepted and depth_auditor is not None:
                    depth_stats = depth_auditor.infer_stats(path)
                    if depth_stats is None:
                        accepted = False
                        reason = "depth_read_failed"
                    elif depth_stats["depth_p50"] < args.min_depth_p50:
                        accepted = False
                        reason = "depth_too_near"
                    elif depth_stats["near_ratio_05m"] > args.max_near_ratio_05m:
                        accepted = False
                        reason = "too_much_near_05m"
                    elif depth_stats["near_ratio_1m"] > args.max_near_ratio_1m:
                        accepted = False
                        reason = "too_much_near_1m"
                    elif depth_stats["near_ratio_2m"] > args.max_near_ratio_2m:
                        accepted = False
                        reason = "too_much_near_2m"

                record = {
                    "frame": path.name,
                    "accepted": accepted,
                    "laplacian_var": round(lap_var, 4),
                    "brightness": round(brightness, 4),
                    "mean_diff": None if mean_diff is None else round(mean_diff, 4),
                    "reason": reason,
                }
                if depth_stats is not None:
                    record.update({
                        "depth_backend": args.depth_audit_backend,
                        "depth_p50": round(depth_stats["depth_p50"], 4),
                        "depth_p95": round(depth_stats["depth_p95"], 4),
                        "near_ratio_05m": round(depth_stats["near_ratio_05m"], 6),
                        "near_ratio_1m": round(depth_stats["near_ratio_1m"], 6),
                        "near_ratio_2m": round(depth_stats["near_ratio_2m"], 6),
                    })
                log_fp.write(json.dumps(record) + "\n")

                if accepted:
                    ensure_link(path, dst_images / path.name)
                    last_kept_gray = gray
                    kept_gap = 0
                    kept_count += 1
                    if depth_stats is not None:
                        depth_stats_history.append(depth_stats)
                else:
                    kept_gap += 1
                    reject_count += 1
                    reject_reasons[reason] = reject_reasons.get(reason, 0) + 1

        if src_complete.exists():
            remaining = [p for p in list_final_jpgs(src_images) if p.name not in processed]
            if not remaining:
                dst_complete.write_text("ok\n", encoding="utf-8")
                summary = {
                    "accepted": kept_count,
                    "rejected": reject_count,
                    "reject_reasons": reject_reasons,
                }
                if depth_stats_history:
                    summary["depth_audit_backend"] = args.depth_audit_backend
                    summary["depth_p50_mean"] = float(np.mean([x["depth_p50"] for x in depth_stats_history]))
                    summary["depth_p95_mean"] = float(np.mean([x["depth_p95"] for x in depth_stats_history]))
                    summary["near_ratio_05m_mean"] = float(np.mean([x["near_ratio_05m"] for x in depth_stats_history]))
                    summary["near_ratio_1m_mean"] = float(np.mean([x["near_ratio_1m"] for x in depth_stats_history]))
                    summary["near_ratio_2m_mean"] = float(np.mean([x["near_ratio_2m"] for x in depth_stats_history]))
                summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
                break

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
