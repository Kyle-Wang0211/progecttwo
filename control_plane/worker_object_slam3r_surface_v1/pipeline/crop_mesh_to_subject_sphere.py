"""Crop the reconstructed mesh to a sphere around the inferred subject.

Why this exists
---------------

PocketWorld's dome-style capture flow orbits the camera around the user's
intended subject. VGGT + Poisson MVS reconstructs the full visible scene
— subject + everything else the camera saw — and the resulting GLB
carries far-field carcasses (background walls, distant furniture, floor
reflections, etc.) that contribute nothing but visual noise and storage
cost.

The client-side dome 118-cell quality gate (sharpness / angular / brightness /
elevation hard rejects) ensures every captured frame is high-quality and
covers the orbit, but does NOT control which pixels in each frame belong
to the subject vs the background. Reaching that requires either:

  • A frame-level mask (the Phase B SAM path, work in progress), OR
  • Spatial bounds on the reconstruction (this file)

This is the spatial-bound approach. Cheap, server-side only, applies to
every existing capture (including legacy ones uploaded before this
landed). Pairs well with the SAM path when it ships: SAM gives
pixel-precise subject boundaries on each frame, this geometry crop
guarantees the mesh stays inside the orbit shell.

How we infer the subject
------------------------

The dome flow orbits the camera around the subject at roughly constant
radius from a `lockOrigin` anchor (default 1.0 m ahead of where the user
tapped lock). So:

  • center = mean of camera positions (centroid of the orbit)
  • orbit_radius = mean distance from each camera to the centroid

We then crop the mesh to a sphere centered at `center` with radius
`orbit_radius × AETHER_CROP_RADIUS_RATIO`. The default ratio is 0.65 —
empirically this nips far-field carcasses while leaving room for typical
hand-held subjects (cup, plant, small statue, chair) whose extent is
substantially smaller than the orbit radius.

Set `AETHER_CROP_RADIUS_RATIO=0` (or any value ≥ 1.0) to disable the
crop entirely without redeploying.

Coordinate frame
----------------

VGGT outputs cameras in its own world frame (the frame in which the
reconstructed point cloud / mesh sits). We compute centroid + radius in
that same frame from VGGT's `extrinsic` matrices, so the crop sphere
naturally aligns with the mesh — no separate ARKit↔VGGT alignment
needed.

Inputs / outputs
----------------

  inputs:
    output/vggt/vggt_raw.npz                 (extrinsic shape [S, 3, 4])
    delivery_dir/default_mesh.glb            (the deliverable mesh)
  outputs (overwrites in place):
    delivery_dir/default_mesh.glb            (cropped)
    delivery_dir/crop_mesh_to_subject_sphere.json   (sidecar diagnostic)

The stage is a no-op (logs + returns) when either input file is missing
or when the env ratio is out of range, so it's safe to drop into the
pipeline before all backends produce both files.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import numpy as np
import trimesh

from ..context import JobContext

_LOG = logging.getLogger(__name__)


def crop_mesh_to_subject_sphere(ctx: JobContext) -> None:
    crop_ratio_str = os.environ.get("AETHER_CROP_RADIUS_RATIO", "0.65")
    try:
        crop_ratio = float(crop_ratio_str)
    except ValueError:
        _LOG.warning(
            "crop_mesh_to_subject_sphere: invalid AETHER_CROP_RADIUS_RATIO=%r, disabling",
            crop_ratio_str,
        )
        return
    if crop_ratio <= 0 or crop_ratio >= 1.0:
        _LOG.info(
            "crop_mesh_to_subject_sphere: ratio=%.3f outside (0, 1.0) → no-op",
            crop_ratio,
        )
        return

    assert ctx.delivery_dir is not None, "ctx.delivery_dir must be set"
    assert ctx.output_dir is not None, "ctx.output_dir must be set"

    glb_path = ctx.delivery_dir / "default_mesh.glb"
    if not glb_path.exists():
        _LOG.info(
            "crop_mesh_to_subject_sphere: %s missing → no-op (no mesh to crop)",
            glb_path,
        )
        return

    vggt_npz_path = ctx.output_dir / "vggt" / "vggt_raw.npz"
    if not vggt_npz_path.exists():
        _LOG.info(
            "crop_mesh_to_subject_sphere: %s missing → no-op (can't infer subject center)",
            vggt_npz_path,
        )
        return

    # ── Infer subject sphere from VGGT camera positions ────────────────
    npz = np.load(vggt_npz_path)
    extrinsic = npz["extrinsic"]  # (S, 3, 4) — world→cam OpenCV convention
    if extrinsic.ndim != 3 or extrinsic.shape[1:] != (3, 4):
        _LOG.warning(
            "crop_mesh_to_subject_sphere: extrinsic shape %s != (S, 3, 4), no-op",
            extrinsic.shape,
        )
        return

    # Camera position in VGGT world frame: solve from world→cam matrix.
    # extrinsic[i] = [R | t], cam_in_world = -R^T @ t.
    S = extrinsic.shape[0]
    cam_positions = np.zeros((S, 3), dtype=np.float64)
    for i in range(S):
        R = extrinsic[i, :, :3]
        t = extrinsic[i, :, 3]
        cam_positions[i] = -R.T @ t

    center = cam_positions.mean(axis=0)
    distances = np.linalg.norm(cam_positions - center, axis=1)
    orbit_radius = float(distances.mean())
    orbit_radius_std = float(distances.std())
    crop_radius = orbit_radius * crop_ratio

    _LOG.info(
        "crop_mesh_to_subject_sphere: %d cameras, center=[%.3f, %.3f, %.3f], "
        "orbit_radius=%.3f ± %.3f, crop_radius=%.3f (ratio=%.2f)",
        S,
        center[0], center[1], center[2],
        orbit_radius, orbit_radius_std,
        crop_radius, crop_ratio,
    )

    # Sanity check: orbit radius near 0 means cameras barely moved —
    # probably a static capture where dome curate failed. Skip rather
    # than crop everything.
    if orbit_radius < 0.1:
        _LOG.warning(
            "crop_mesh_to_subject_sphere: orbit_radius %.3f m < 0.1 m, "
            "cameras barely moved — skipping crop to avoid destroying mesh",
            orbit_radius,
        )
        return

    # ── Load mesh + crop ──────────────────────────────────────────────
    scene = trimesh.load(str(glb_path), force=None)

    total_faces_before = 0
    total_faces_after = 0

    if isinstance(scene, trimesh.Scene):
        # GLB normally loads as Scene with named geometries. Crop each
        # and rebuild the scene so material / texture / node transforms
        # are preserved by trimesh's exporter.
        new_scene = trimesh.Scene()
        for name, geom in list(scene.geometry.items()):
            if not isinstance(geom, trimesh.Trimesh):
                # Lines / point clouds — keep them untouched.
                new_scene.add_geometry(geom, geom_name=name)
                continue
            total_faces_before += len(geom.faces)
            cropped = _crop_trimesh(geom, center, crop_radius)
            if cropped is None or len(cropped.faces) == 0:
                _LOG.warning(
                    "crop_mesh_to_subject_sphere: geom '%s' entirely outside crop sphere "
                    "(%d faces), dropping",
                    name, len(geom.faces),
                )
                continue
            total_faces_after += len(cropped.faces)
            new_scene.add_geometry(cropped, geom_name=name)
        if total_faces_after == 0:
            _LOG.error(
                "crop_mesh_to_subject_sphere: ALL faces fell outside crop sphere — "
                "keeping original mesh, this is almost certainly a center/radius bug",
            )
            return
        # Atomic overwrite: write to tmp then rename, so a crash mid-write
        # leaves the original mesh intact.
        tmp_path = glb_path.with_suffix(".glb.crop_tmp")
        new_scene.export(str(tmp_path))
        tmp_path.replace(glb_path)
    elif isinstance(scene, trimesh.Trimesh):
        total_faces_before = len(scene.faces)
        cropped = _crop_trimesh(scene, center, crop_radius)
        if cropped is None or len(cropped.faces) == 0:
            _LOG.error(
                "crop_mesh_to_subject_sphere: mesh entirely outside crop sphere — "
                "keeping original",
            )
            return
        total_faces_after = len(cropped.faces)
        tmp_path = glb_path.with_suffix(".glb.crop_tmp")
        cropped.export(str(tmp_path))
        tmp_path.replace(glb_path)
    else:
        _LOG.warning(
            "crop_mesh_to_subject_sphere: unrecognized mesh type %s, no-op",
            type(scene).__name__,
        )
        return

    kept_ratio = total_faces_after / total_faces_before if total_faces_before > 0 else 0.0
    _LOG.info(
        "crop_mesh_to_subject_sphere: faces %d → %d (kept %.1f%%)",
        total_faces_before, total_faces_after, kept_ratio * 100.0,
    )

    summary = {
        "camera_count": S,
        "center_xyz": center.tolist(),
        "orbit_radius_m": orbit_radius,
        "orbit_radius_std_m": orbit_radius_std,
        "crop_radius_m": crop_radius,
        "crop_radius_ratio": crop_ratio,
        "faces_before": total_faces_before,
        "faces_after": total_faces_after,
        "kept_ratio": kept_ratio,
    }
    (ctx.delivery_dir / "crop_mesh_to_subject_sphere.json").write_text(
        json.dumps(summary, indent=2)
    )


def _crop_trimesh(
    mesh: trimesh.Trimesh,
    center: np.ndarray,
    radius: float,
) -> trimesh.Trimesh | None:
    """Keep only faces whose all 3 vertices are inside the sphere.

    Why all-3 (not centroid): face centroids on a partially-clipped face
    can fall inside the sphere even when most of the face's surface area
    is outside, producing visible "spikes" poking out of the crop. The
    stricter all-vertex test gives a clean boundary at the cost of
    losing a sliver of border faces. In dome captures the subject always
    sits well inside the orbit so that sliver is invisible.

    trimesh's `submesh([face_indices], append=True)` does the right
    thing with material / UVs — vertex attributes propagate, faces
    re-indexed, texture coordinates carried over per-vertex.
    """
    verts = mesh.vertices  # (V, 3)
    distances = np.linalg.norm(verts - center, axis=1)
    inside = distances < radius  # (V,)
    if not inside.any():
        return None
    # All 3 vertices of each face must be inside.
    face_mask = np.all(inside[mesh.faces], axis=1)  # (F,)
    if not face_mask.any():
        return None
    keep_face_indices = np.where(face_mask)[0]
    out = mesh.submesh([keep_face_indices], append=True)
    if isinstance(out, list):
        # Older trimesh versions sometimes return list even with append=True.
        if not out:
            return None
        out = out[0]
    return out
