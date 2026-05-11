"""Apply per-frame MobileSAM subject masks to curated frames.

Stage placement: between curate (curate_from_client / curate_frames) and
vggt_geometry. For each curated frame whose manifest entry carries a
`subject_mask` field, decode the RLE mask, upsample to frame resolution,
and white-out background pixels in `ctx.curated_dir/<frame_uuid>.jpg`.
This shrinks VGGT's foreground/background ambiguity and keeps the
reconstructed mesh focused on the user-aimed object.

Wire format (must stay in lockstep with the Flutter producer at
lib/capture/sam/subject_mask_data.dart):

    "subject_mask": {
        "width": 256,
        "height": 256,
        "rle_b64": "<base64 of run-length-encoded binary mask>",
        "centerProb": 0.92,
        "fillRatio": 0.18,
        "mask_uuid": "msk-708"
    }

RLE format: row-major scan starts on background (0). Each run is a
little-endian uint16 length. A run length of 0 is a continuation marker
(same color, no flip) — used when a single run would overflow uint16.

Env gate:
- `AETHER_USE_SUBJECT_MASK=1` → stage runs
- otherwise → stage is a no-op (default off during Phase A rollout while
  the Flutter capture-side SAM loop ships behind the native pixel-buffer
  bridge in Phase B)

Backward / forward compat:
- Old client (no subject_mask field on any frame) → stage iterates the
  manifest, finds 0 entries with masks, exits cleanly.
- New client + old worker (this stage absent) → no harm, frames just
  aren't masked; reconstruction proceeds as before.
- New client + new worker + env off → stage no-ops, frames not masked;
  reconstruction proceeds as before.

Frames are masked in-place under `ctx.curated_dir`. We deliberately do
NOT keep an unmasked backup directory — that would double disk usage on
the worker's ephemeral storage. If A/B comparison is needed, rerun with
`AETHER_USE_SUBJECT_MASK=0`.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import numpy as np
from PIL import Image

from ..context import JobContext

_LOG = logging.getLogger(__name__)

_MANIFEST_FILENAME = "curated_manifest_passthrough.json"

# Pixels outside the mask are filled with this color before the frame is
# re-saved. White (255, 255, 255) chosen because:
#   - VGGT depth confidence drops on flat-color regions → it learns to
#     ignore them
#   - texrecon would mis-project background colors into the mesh atlas;
#     white is at least uniform and visually obvious if it leaks
_BACKGROUND_FILL = (255, 255, 255)


def apply_subject_mask(ctx: JobContext) -> None:
    """Walk curated frames, apply matching subject mask in place.

    Reads the manifest at `ctx.output_dir / curated_manifest_passthrough.json`
    (written by curate_from_client) for the per-frame subject_mask blocks.
    For each frame_uuid present, opens `ctx.curated_dir / <frame_uuid>.jpg`,
    composites the mask with white background, writes back.

    Frames without a subject_mask entry, or where the JPEG is missing, are
    skipped silently (a single info log gives the count). Decoder errors on
    a single mask are logged at WARNING and that frame is skipped — we do
    NOT fail the whole stage because one bad mask should not kill the job.
    """
    if not _is_enabled():
        _LOG.info("apply_subject_mask: AETHER_USE_SUBJECT_MASK!=1, no-op")
        return

    assert ctx.output_dir is not None, "ctx.output_dir must be set"
    assert ctx.curated_dir is not None, "ctx.curated_dir must be set"

    manifest_path = ctx.output_dir / _MANIFEST_FILENAME
    if not manifest_path.exists():
        _LOG.info(
            "apply_subject_mask: %s not found, no-op (likely a non-client-curated job)",
            manifest_path.name,
        )
        return

    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        _LOG.warning("apply_subject_mask: manifest unreadable (%s), no-op", exc)
        return

    frames = manifest.get("frames") or []
    masked_count = 0
    skipped_no_mask = 0
    skipped_no_jpeg = 0
    skipped_decode_err = 0

    for frame in frames:
        mask_block = frame.get("subject_mask")
        if not mask_block:
            skipped_no_mask += 1
            continue

        frame_uuid = frame.get("frame_uuid")
        if not frame_uuid:
            skipped_decode_err += 1
            continue

        jpeg_path = _find_curated_jpeg(ctx.curated_dir, frame_uuid)
        if jpeg_path is None:
            skipped_no_jpeg += 1
            continue

        try:
            _apply_mask_to_jpeg(jpeg_path, mask_block)
            masked_count += 1
        except Exception as exc:
            _LOG.warning(
                "apply_subject_mask: frame=%s failed (%s), keeping original",
                frame_uuid,
                exc,
            )
            skipped_decode_err += 1

    _LOG.info(
        "apply_subject_mask: masked=%d, no_mask_in_manifest=%d, jpeg_missing=%d, decode_err=%d",
        masked_count,
        skipped_no_mask,
        skipped_no_jpeg,
        skipped_decode_err,
    )

    summary_path = ctx.output_dir / "apply_subject_mask.json"
    summary_path.write_text(
        json.dumps(
            {
                "masked_count": masked_count,
                "skipped_no_mask_in_manifest": skipped_no_mask,
                "skipped_jpeg_missing": skipped_no_jpeg,
                "skipped_decode_err": skipped_decode_err,
                "total_frames": len(frames),
            },
            indent=2,
        )
    )


def _is_enabled() -> bool:
    return os.environ.get("AETHER_USE_SUBJECT_MASK", "0") == "1"


def _find_curated_jpeg(curated_dir: Path, frame_uuid: str) -> Path | None:
    """curate_from_client / curate_frames may name files <uuid>.jpg or
    <uuid>.jpeg; tolerate both."""
    for ext in (".jpg", ".jpeg", ".png"):
        candidate = curated_dir / f"{frame_uuid}{ext}"
        if candidate.exists():
            return candidate
    return None


def _apply_mask_to_jpeg(jpeg_path: Path, mask_block: dict) -> None:
    """Decode mask, resize to JPEG dims, white-out background pixels."""
    width = int(mask_block["width"])
    height = int(mask_block["height"])
    rle_b64 = mask_block["rle_b64"]

    binary_mask = _decode_rle(rle_b64, width, height)

    img = Image.open(jpeg_path).convert("RGB")
    target_w, target_h = img.size

    # Upsample mask to JPEG resolution. Nearest-neighbor preserves the
    # binary edges sharply — bilinear would create gray fringes the
    # threshold has to deal with again.
    if (width, height) != (target_w, target_h):
        mask_img = Image.fromarray(binary_mask * 255, mode="L")
        mask_img = mask_img.resize((target_w, target_h), Image.NEAREST)
        binary_mask = (np.array(mask_img) > 127).astype(np.uint8)

    rgb = np.array(img, dtype=np.uint8)
    bg = np.array(_BACKGROUND_FILL, dtype=np.uint8)
    rgb[binary_mask == 0] = bg

    Image.fromarray(rgb, mode="RGB").save(jpeg_path, format="JPEG", quality=92)


def _decode_rle(rle_b64: str, width: int, height: int) -> np.ndarray:
    """Inverse of subject_mask_data.dart::SubjectMaskData._encodeRle.

    Returns a uint8 ndarray of shape (height, width), values in {0, 1}.
    """
    rle_bytes = base64.b64decode(rle_b64)
    if len(rle_bytes) % 2 != 0:
        raise ValueError(f"rle_bytes length {len(rle_bytes)} not divisible by 2")

    runs = np.frombuffer(rle_bytes, dtype="<u2")  # little-endian uint16
    out = np.zeros(width * height, dtype=np.uint8)

    cursor = 0
    color = 0  # encoder starts on background
    for run_length in runs:
        if run_length == 0:
            # Continuation marker — same color, no flip
            continue
        end = cursor + int(run_length)
        if end > out.size:
            raise ValueError(
                f"rle decoded past end: cursor={cursor} run={run_length} size={out.size}"
            )
        if color == 1:
            out[cursor:end] = 1
        cursor = end
        color = 1 - color

    if cursor != out.size:
        raise ValueError(
            f"rle did not fill mask: cursor={cursor} expected={out.size}"
        )

    return out.reshape((height, width))
