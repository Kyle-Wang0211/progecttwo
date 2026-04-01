#!/usr/bin/env python3
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class AuditDecision:
    accepted: bool
    reason: str
    stage: str
    score: float
    laplacian_var: float
    brightness: float
    mean_diff: float | None
    depth_p50: float | None
    depth_p95: float | None
    near_ratio_05m: float | None
    near_ratio_1m: float | None
    near_ratio_2m: float | None
    too_dark: bool
    too_bright: bool
    too_blurry: bool
    too_similar: bool
    depth_too_near: bool
    too_much_near_05m: bool
    too_much_near_1m: bool
    too_much_near_2m: bool

    def to_record(self, frame_name: str):
        record = {
            "frame": frame_name,
            "accepted": self.accepted,
            "reason": self.reason,
            "stage": self.stage,
            "score": round(self.score, 6),
            "laplacian_var": round(self.laplacian_var, 4),
            "brightness": round(self.brightness, 4),
            "mean_diff": None if self.mean_diff is None else round(self.mean_diff, 4),
            "too_dark": self.too_dark,
            "too_bright": self.too_bright,
            "too_blurry": self.too_blurry,
            "too_similar": self.too_similar,
            "depth_too_near": self.depth_too_near,
            "too_much_near_05m": self.too_much_near_05m,
            "too_much_near_1m": self.too_much_near_1m,
            "too_much_near_2m": self.too_much_near_2m,
        }
        if self.depth_p50 is not None:
            record.update(
                {
                    "depth_p50": round(self.depth_p50, 4),
                    "depth_p95": round(self.depth_p95, 4),
                    "near_ratio_05m": round(self.near_ratio_05m, 6),
                    "near_ratio_1m": round(self.near_ratio_1m, 6),
                    "near_ratio_2m": round(self.near_ratio_2m, 6),
                }
            )
        return record


def load_gray_small_from_bgr(image_bgr: np.ndarray, target_width: int):
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    if gray.shape[1] > target_width:
        scale = target_width / float(gray.shape[1])
        target_height = max(1, int(round(gray.shape[0] * scale)))
        gray = cv2.resize(gray, (target_width, target_height), interpolation=cv2.INTER_AREA)
    return gray


def stage_from_progress(progress: float, stage_intervals: list[dict] | None):
    if stage_intervals:
        for item in stage_intervals:
            if progress < float(item.get("end_ratio", 1.0)) + 1e-9:
                return str(item.get("name", "global_anchor"))
    if progress < 0.30:
        return "global_anchor"
    if progress < 0.76:
        return "local_refine"
    return "closure"


class UniK3DDepthAudit:
    def __init__(self, repo: str, backbone: str, device: str, resolution_level: int):
        repo_path = Path(repo)
        if str(repo_path) not in sys.path:
            sys.path.insert(0, str(repo_path))

        import torch  # noqa: PLC0415
        from unik3d.models import UniK3D  # noqa: PLC0415

        runtime_device = device
        if runtime_device == "cuda" and not torch.cuda.is_available():
            runtime_device = "cpu"
        self._torch = torch
        self.device = torch.device(runtime_device)
        self.model = UniK3D.from_pretrained(f"lpiccinelli/unik3d-{backbone}")
        self.model.resolution_level = resolution_level
        self.model = self.model.to(self.device).eval()

    def infer_stats_bgr(self, image_bgr: np.ndarray):
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
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


class StageAwareAuditCore:
    def __init__(
        self,
        *,
        downsample_width: int = 160,
        min_laplacian_var: float = 6.0,
        min_mean_diff: float = 2.5,
        min_brightness: float = 20.0,
        max_brightness: float = 235.0,
        min_depth_p50: float = 0.8,
        max_near_ratio_05m: float = 0.40,
        max_near_ratio_1m: float = 0.85,
        max_near_ratio_2m: float = 1.0,
        depth_auditor: UniK3DDepthAudit | None = None,
    ):
        self.downsample_width = downsample_width
        self.min_laplacian_var = min_laplacian_var
        self.min_mean_diff = min_mean_diff
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.min_depth_p50 = min_depth_p50
        self.max_near_ratio_05m = max_near_ratio_05m
        self.max_near_ratio_1m = max_near_ratio_1m
        self.max_near_ratio_2m = max_near_ratio_2m
        self.depth_auditor = depth_auditor
        self.stage_profiles = {
            "global_anchor": {
                "score_threshold": 0.46,
                "force_keep_gap": 12,
                "min_diff_scale": 1.00,
                "min_lap_scale": 0.75,
                "brightness_weight": 0.08,
                "similarity_weight": 0.06,
            },
            "local_refine": {
                "score_threshold": 0.40,
                "force_keep_gap": 8,
                "min_diff_scale": 0.18,
                "min_lap_scale": 1.15,
                "brightness_weight": 0.06,
                "similarity_weight": 0.02,
            },
            "revisit": {
                "score_threshold": 0.44,
                "force_keep_gap": 10,
                "min_diff_scale": 0.45,
                "min_lap_scale": 0.95,
                "brightness_weight": 0.07,
                "similarity_weight": 0.04,
            },
            "closure": {
                "score_threshold": 0.46,
                "force_keep_gap": 12,
                "min_diff_scale": 0.55,
                "min_lap_scale": 0.85,
                "brightness_weight": 0.07,
                "similarity_weight": 0.05,
            },
        }

    def catastrophic_exposure(self, brightness: float):
        return brightness < 4.0 or brightness > 252.0

    def catastrophic_blur(self, laplacian_var: float):
        return laplacian_var < 0.9

    def evaluate(
        self,
        *,
        frame_bgr: np.ndarray,
        last_kept_gray: np.ndarray | None,
        kept_gap: int,
        progress: float,
        stage_intervals: list[dict] | None = None,
    ):
        gray = load_gray_small_from_bgr(frame_bgr, self.downsample_width)
        stage = stage_from_progress(progress, stage_intervals)
        profile = self.stage_profiles.get(stage, self.stage_profiles["global_anchor"])

        lap_var = float(cv2.Laplacian(gray, cv2.CV_32F).var())
        brightness = float(gray.mean())
        mean_diff = None
        if last_kept_gray is not None:
            mean_diff = float(np.mean(np.abs(gray.astype(np.float32) - last_kept_gray.astype(np.float32))))

        depth_stats = None
        if self.depth_auditor is not None:
            depth_stats = self.depth_auditor.infer_stats_bgr(frame_bgr)

        too_dark = brightness < self.min_brightness
        too_bright = brightness > self.max_brightness
        too_blurry = lap_var < (self.min_laplacian_var * profile["min_lap_scale"])
        too_similar = mean_diff is not None and mean_diff < (self.min_mean_diff * profile["min_diff_scale"])
        depth_too_near = False
        too_much_near_05m = False
        too_much_near_1m = False
        too_much_near_2m = False
        if depth_stats is not None:
            depth_too_near = depth_stats["depth_p50"] < self.min_depth_p50
            too_much_near_05m = depth_stats["near_ratio_05m"] > self.max_near_ratio_05m
            too_much_near_1m = depth_stats["near_ratio_1m"] > self.max_near_ratio_1m
            too_much_near_2m = depth_stats["near_ratio_2m"] > self.max_near_ratio_2m

        score = 1.0
        if too_dark:
            score -= profile["brightness_weight"]
        if too_bright:
            score -= profile["brightness_weight"]
        if too_blurry:
            score -= 0.22
        if too_similar:
            score -= profile["similarity_weight"]
        if depth_too_near:
            score -= 0.10
        if too_much_near_05m:
            score -= 0.10
        if too_much_near_1m:
            score -= 0.08
        if too_much_near_2m:
            score -= 0.04

        force_keep = kept_gap >= int(profile["force_keep_gap"])
        catastrophic = self.catastrophic_exposure(brightness) or self.catastrophic_blur(lap_var)
        if depth_stats is not None:
            catastrophic = catastrophic or depth_stats["near_ratio_05m"] > 0.92

        if catastrophic and not force_keep:
            accepted = False
            reason = "catastrophic_quality"
        elif score >= float(profile["score_threshold"]) or force_keep:
            accepted = True
            reason = "accepted_forced" if force_keep and score < float(profile["score_threshold"]) else "accepted"
        else:
            accepted = False
            if too_blurry:
                reason = "too_blurry"
            elif too_similar:
                reason = "too_similar"
            elif depth_too_near or too_much_near_05m or too_much_near_1m or too_much_near_2m:
                reason = "depth_policy"
            elif too_bright:
                reason = "too_bright_soft"
            elif too_dark:
                reason = "too_dark_soft"
            else:
                reason = "low_score"

        return AuditDecision(
            accepted=accepted,
            reason=reason,
            stage=stage,
            score=float(score),
            laplacian_var=lap_var,
            brightness=brightness,
            mean_diff=mean_diff,
            depth_p50=None if depth_stats is None else depth_stats["depth_p50"],
            depth_p95=None if depth_stats is None else depth_stats["depth_p95"],
            near_ratio_05m=None if depth_stats is None else depth_stats["near_ratio_05m"],
            near_ratio_1m=None if depth_stats is None else depth_stats["near_ratio_1m"],
            near_ratio_2m=None if depth_stats is None else depth_stats["near_ratio_2m"],
            too_dark=too_dark,
            too_bright=too_bright,
            too_blurry=too_blurry,
            too_similar=too_similar,
            depth_too_near=depth_too_near,
            too_much_near_05m=too_much_near_05m,
            too_much_near_1m=too_much_near_1m,
            too_much_near_2m=too_much_near_2m,
        ), gray
