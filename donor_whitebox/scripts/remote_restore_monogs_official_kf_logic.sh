#!/usr/bin/env bash
set -euo pipefail

cd /root/gs_refs/MonoGS

python3 - <<'PY'
from pathlib import Path

p = Path("/root/gs_refs/MonoGS/utils/slam_frontend.py")
text = p.read_text()

text = text.replace(
    '        decision = overlap_check and (dist_check2 or dist_check)\n',
    '        decision = (overlap_check and dist_check2) or dist_check\n',
)
text = text.replace('            "overlap_dist_accept": 0,\n', '')

old_stats = """                    if create_kf:
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

new_stats = """                    if create_kf:
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

text = text.replace(old_stats, new_stats)
p.write_text(text)
print("RESTORED_OFFICIAL_KEYFRAME_LOGIC")
PY

nl -ba /root/gs_refs/MonoGS/utils/slam_frontend.py | sed -n "229,240p"
