#!/usr/bin/env bash
set -euo pipefail

cd /root/gs_refs/MonoGS

python3 - <<'PY'
from pathlib import Path
import re

path = Path("/root/gs_refs/MonoGS/utils/slam_frontend.py")
text = path.read_text()

old_init = """        self.pause = False\n"""
new_init = """        self.pause = False\n        self.last_kf_gate = None\n        self.kf_gate_stats = {\n            \"bootstrap_accept\": 0,\n            \"bootstrap_reject\": 0,\n            \"dist_only_accept\": 0,\n            \"overlap_min_accept\": 0,\n            \"reject_overlap\": 0,\n            \"reject_min_translation\": 0,\n            \"reject_translation\": 0,\n        }\n"""
if old_init in text and "self.kf_gate_stats" not in text:
    text = text.replace(old_init, new_init, 1)

old_is_keyframe = """    def is_keyframe(\n        self,\n        cur_frame_idx,\n        last_keyframe_idx,\n        cur_frame_visibility_filter,\n        occ_aware_visibility,\n    ):\n        kf_translation = self.config[\"Training\"][\"kf_translation\"]\n        kf_min_translation = self.config[\"Training\"][\"kf_min_translation\"]\n        kf_overlap = self.config[\"Training\"][\"kf_overlap\"]\n\n        curr_frame = self.cameras[cur_frame_idx]\n        last_kf = self.cameras[last_keyframe_idx]\n        pose_CW = getWorld2View2(curr_frame.R, curr_frame.T)\n        last_kf_CW = getWorld2View2(last_kf.R, last_kf.T)\n        last_kf_WC = torch.linalg.inv(last_kf_CW)\n        dist = torch.norm((pose_CW @ last_kf_WC)[0:3, 3])\n        dist_check = dist > kf_translation * self.median_depth\n        dist_check2 = dist > kf_min_translation * self.median_depth\n\n        union = torch.logical_or(\n            cur_frame_visibility_filter, occ_aware_visibility[last_keyframe_idx]\n        ).count_nonzero()\n        intersection = torch.logical_and(\n            cur_frame_visibility_filter, occ_aware_visibility[last_keyframe_idx]\n        ).count_nonzero()\n        point_ratio_2 = intersection / union\n        return (point_ratio_2 < kf_overlap and dist_check2) or dist_check\n"""
new_is_keyframe = """    def is_keyframe(\n        self,\n        cur_frame_idx,\n        last_keyframe_idx,\n        cur_frame_visibility_filter,\n        occ_aware_visibility,\n    ):\n        kf_translation = self.config[\"Training\"][\"kf_translation\"]\n        kf_min_translation = self.config[\"Training\"][\"kf_min_translation\"]\n        kf_overlap = self.config[\"Training\"][\"kf_overlap\"]\n\n        curr_frame = self.cameras[cur_frame_idx]\n        last_kf = self.cameras[last_keyframe_idx]\n        pose_CW = getWorld2View2(curr_frame.R, curr_frame.T)\n        last_kf_CW = getWorld2View2(last_kf.R, last_kf.T)\n        last_kf_WC = torch.linalg.inv(last_kf_CW)\n        dist = torch.norm((pose_CW @ last_kf_WC)[0:3, 3])\n        dist_check = dist > kf_translation * self.median_depth\n        dist_check2 = dist > kf_min_translation * self.median_depth\n\n        union = torch.logical_or(\n            cur_frame_visibility_filter, occ_aware_visibility[last_keyframe_idx]\n        ).count_nonzero()\n        intersection = torch.logical_and(\n            cur_frame_visibility_filter, occ_aware_visibility[last_keyframe_idx]\n        ).count_nonzero()\n        point_ratio_2 = intersection / union\n        overlap_check = point_ratio_2 < kf_overlap\n        decision = (overlap_check and dist_check2) or dist_check\n        self.last_kf_gate = {\n            \"dist_check\": bool(dist_check.item() if hasattr(dist_check, \"item\") else dist_check),\n            \"dist_check2\": bool(dist_check2.item() if hasattr(dist_check2, \"item\") else dist_check2),\n            \"overlap_check\": bool(overlap_check.item() if hasattr(overlap_check, \"item\") else overlap_check),\n            \"point_ratio\": float(point_ratio_2.item() if hasattr(point_ratio_2, \"item\") else point_ratio_2),\n            \"dist\": float(dist.item() if hasattr(dist, \"item\") else dist),\n            \"median_depth\": float(self.median_depth.item() if hasattr(self.median_depth, \"item\") else self.median_depth),\n        }\n        return decision\n"""
if old_is_keyframe in text and "self.last_kf_gate = {" not in text:
    text = text.replace(old_is_keyframe, new_is_keyframe, 1)

if 'self.kf_gate_stats["bootstrap_accept"] += 1' not in text:
    text = re.sub(
        r"""(                if len\(self\.current_window\) < self\.window_size:\n(?:                    .*\n)+?                    create_kf = \(\n                        check_time\n                        and point_ratio < self\.config\["Training"\]\["kf_overlap"\]\n                    \)\n)""",
        r"""\1                    if create_kf:
                        self.kf_gate_stats["bootstrap_accept"] += 1
                    else:
                        self.kf_gate_stats["bootstrap_reject"] += 1
""",
        text,
        count=1,
    )

if 'self.kf_gate_stats["dist_only_accept"] += 1' not in text:
    text = text.replace(
        """                if self.single_thread:\n                    create_kf = check_time and create_kf\n                if create_kf:\n""",
        """                if self.single_thread:\n                    create_kf = check_time and create_kf\n                if len(self.current_window) >= self.window_size and self.last_kf_gate is not None:\n                    gate = self.last_kf_gate\n                    if create_kf:\n                        if gate["dist_check"]:\n                            self.kf_gate_stats["dist_only_accept"] += 1\n                        elif gate["overlap_check"] and gate["dist_check2"]:\n                            self.kf_gate_stats["overlap_min_accept"] += 1\n                    else:\n                        if not gate["dist_check"]:\n                            self.kf_gate_stats["reject_translation"] += 1\n                        if gate["overlap_check"] and not gate["dist_check2"]:\n                            self.kf_gate_stats["reject_min_translation"] += 1\n                        if not gate["overlap_check"]:\n                            self.kf_gate_stats["reject_overlap"] += 1\n                if create_kf:\n""",
        1,
    )

old_break = """                    if self.save_results:\n                        eval_ate(\n                            self.cameras,\n                            self.kf_indices,\n                            self.save_dir,\n                            0,\n                            final=True,\n                            monocular=self.monocular,\n                        )\n                        save_gaussians(\n                            self.gaussians, self.save_dir, \"final\", final=True\n                        )\n                    break\n"""
new_break = """                    if self.save_results:\n                        eval_ate(\n                            self.cameras,\n                            self.kf_indices,\n                            self.save_dir,\n                            0,\n                            final=True,\n                            monocular=self.monocular,\n                        )\n                        save_gaussians(\n                            self.gaussians, self.save_dir, \"final\", final=True\n                        )\n                    Log(\"MonoGS frame summary:\", {\n                        \"camera_count\": len(self.cameras),\n                        \"camera_max_id\": max(self.cameras.keys()) if self.cameras else -1,\n                        \"kf_count\": len(self.kf_indices),\n                    })\n                    Log(\"MonoGS KF gate summary:\", self.kf_gate_stats)\n                    break\n"""
if old_break in text and "MonoGS KF gate summary:" not in text:
    text = text.replace(old_break, new_break, 1)

if "MonoGS frame summary:" not in text and "Log(\"MonoGS KF gate summary:\", self.kf_gate_stats)" in text:
    text = text.replace(
        """                    Log(\"MonoGS KF gate summary:\", self.kf_gate_stats)\n""",
        """                    Log(\"MonoGS frame summary:\", {\n                        \"camera_count\": len(self.cameras),\n                        \"camera_max_id\": max(self.cameras.keys()) if self.cameras else -1,\n                        \"kf_count\": len(self.kf_indices),\n                    })\n                    Log(\"MonoGS KF gate summary:\", self.kf_gate_stats)\n""",
        1,
    )

path.write_text(text)
PY
