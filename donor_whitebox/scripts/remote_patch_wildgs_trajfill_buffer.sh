#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${1:-/root/gs_refs/WildGS-SLAM.clean}"
TARGET="${ROOT_DIR}/src/trajectory_filler.py"
export TARGET

python3 - <<'PY'
from pathlib import Path
import os
import re

path = Path(os.environ["TARGET"])
text = path.read_text()

pattern = re.compile(
    r"(?ms)^    @torch\.no_grad\(\)\n"
    r"    def __call__\(self, image_stream:BaseDataset\):\n"
    r".*?"
    r"^        # stitch pose segments together\n"
    r"^        return lietorch\.cat\(pose_list, dim=0\), dino_feats\n?"
)

new = """    @torch.no_grad()
    def __call__(self, image_stream:BaseDataset):
        \"\"\" fill in poses of non-keyframe images. \"\"\"

        # store all camera poses
        pose_list = []
        dino_feats = None
        if self.uncertainty_aware:
            dino_feats = []

        timestamps = []
        images = []
        intrinsics = []
        dino_features = []

        self.printer.print(\"Filling full trajectory ...\",FontColor.INFO)
        intrinsic = image_stream.get_intrinsic()
        buffer_capacity = int(self.video.timestamp.shape[0])

        def flush_batch():
            nonlocal timestamps, images, intrinsics, dino_features, pose_list, dino_feats
            if len(timestamps) == 0:
                return
            pose_list += self.__fill(timestamps, images, None, intrinsics, dino_features)
            if dino_features is not None:
                dino_feats += dino_features
            timestamps, images, intrinsics, dino_features = [], [], [], []

        for (timestamp, image, _ , _)  in tqdm(image_stream):
            timestamps.append(timestamp)
            images.append(image)
            intrinsics.append(intrinsic)
            if self.uncertainty_aware:
                dino_feature = predict_img_features(self.feat_extractor,
                                                    timestamp,image,
                                                    self.cfg,
                                                    self.device,
                                                    save_feat=False)
                dino_features.append(dino_feature)
            else:
                dino_features = None

            current_count = int(self.video.counter.value)
            temp_slots = max(1, buffer_capacity - current_count)
            chunk_size = min(16, temp_slots)
            if len(timestamps) >= chunk_size:
                flush_batch()

        flush_batch()

        # stitch pose segments together
        return lietorch.cat(pose_list, dim=0), dino_feats
"""

new_text, count = pattern.subn(new, text, count=1)
if count != 1:
    raise SystemExit(f"expected __call__ block not found in {path}")

path.write_text(new_text)
print(f"patched {path}")
PY
