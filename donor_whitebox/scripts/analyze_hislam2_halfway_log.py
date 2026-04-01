#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


KF_RE = re.compile(r"keyframe\s+(\d+)\s+gs\s+\d+")
FRACTION_RE = re.compile(r"(\d+)/(\d+)")


def main() -> None:
    p = argparse.ArgumentParser(description="Summarize halfway HI-SLAM2 keyframe density from a driver log.")
    p.add_argument("--log", required=True)
    p.add_argument("--halfway-frame", type=int, default=1000)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    path = Path(args.log)
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    latest_frame = None
    latest_total = None
    latest_kf = None
    kf_at_half = None
    frame_at_half = None

    for line in lines:
        # HI-SLAM2 logs sometimes print a plain "Processing keyframes: 12/2000"
        # line and sometimes only tqdm progress lines containing the trailing
        # frame fraction "... 2530/3000 [09:47<...". We take the last fraction
        # on the line to avoid matching keyframe ids or other counters earlier
        # in the message.
        fractions = FRACTION_RE.findall(line)
        if fractions:
            frame_s, total_s = fractions[-1]
            latest_frame = int(frame_s)
            latest_total = int(total_s)
        km = KF_RE.search(line)
        if km:
            latest_kf = int(km.group(1))
            if latest_frame is not None and latest_frame >= args.halfway_frame and kf_at_half is None:
                kf_at_half = latest_kf
                frame_at_half = latest_frame

    result = {
        "latest_frame": latest_frame,
        "latest_total_frames": latest_total,
        "latest_keyframe": latest_kf,
        "halfway_frame_target": args.halfway_frame,
        "halfway_frame_observed": frame_at_half,
        "halfway_keyframe": kf_at_half,
        "halfway_keyframe_density": None if kf_at_half is None or frame_at_half in (None, 0) else float(kf_at_half) / float(frame_at_half),
    }

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
