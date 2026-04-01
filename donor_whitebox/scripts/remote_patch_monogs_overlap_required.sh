#!/usr/bin/env bash
set -euo pipefail

cd /root/gs_refs/MonoGS

python3 - <<'PY'
from pathlib import Path

path = Path("/root/gs_refs/MonoGS/utils/slam_frontend.py")
text = path.read_text()

old = '        decision = (overlap_check and dist_check2) or dist_check\n'
new = '        decision = overlap_check and (dist_check2 or dist_check)\n'
if old in text:
    text = text.replace(old, new, 1)

old_init = '            "dist_only_accept": 0,\n            "overlap_min_accept": 0,\n'
new_init = '            "dist_only_accept": 0,\n            "overlap_dist_accept": 0,\n            "overlap_min_accept": 0,\n'
if old_init in text and '"overlap_dist_accept"' not in text:
    text = text.replace(old_init, new_init, 1)

old_stats = """                    if create_kf:
                        if gate["dist_check"]:
                            self.kf_gate_stats["dist_only_accept"] += 1
                        elif gate["overlap_check"] and gate["dist_check2"]:
                            self.kf_gate_stats["overlap_min_accept"] += 1
                    else:
                        if not gate["dist_check"]:
                            self.kf_gate_stats["reject_translation"] += 1
                        if gate["overlap_check"] and not gate["dist_check2"]:
                            self.kf_gate_stats["reject_min_translation"] += 1
                        if not gate["overlap_check"]:
                            self.kf_gate_stats["reject_overlap"] += 1
"""

new_stats = """                    if create_kf:
                        if gate["overlap_check"] and gate["dist_check2"]:
                            self.kf_gate_stats["overlap_min_accept"] += 1
                        elif gate["overlap_check"] and gate["dist_check"]:
                            self.kf_gate_stats["overlap_dist_accept"] += 1
                    else:
                        if not gate["overlap_check"]:
                            self.kf_gate_stats["reject_overlap"] += 1
                        if gate["overlap_check"] and not gate["dist_check2"]:
                            self.kf_gate_stats["reject_min_translation"] += 1
                        if gate["overlap_check"] and not gate["dist_check"]:
                            self.kf_gate_stats["reject_translation"] += 1
"""
if old_stats in text:
    text = text.replace(old_stats, new_stats, 1)

path.write_text(text)
PY
