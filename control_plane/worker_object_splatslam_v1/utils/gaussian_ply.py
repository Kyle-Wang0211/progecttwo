from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from plyfile import PlyData, PlyElement


def load_ply_vertices(path: Path) -> tuple[PlyData, np.ndarray]:
    ply = PlyData.read(str(path))
    vertices = ply["vertex"]
    xyz = np.stack(
        (
            np.asarray(vertices["x"], dtype=np.float64),
            np.asarray(vertices["y"], dtype=np.float64),
            np.asarray(vertices["z"], dtype=np.float64),
        ),
        axis=1,
    )
    return ply, xyz


def save_ply(path: Path, ply: PlyData) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ply.write(str(path))


def apply_transform_to_vertices(
    ply: PlyData,
    rotation: np.ndarray,
    translation: np.ndarray,
) -> None:
    vertices = ply["vertex"]
    xyz = np.stack(
        (
            np.asarray(vertices["x"], dtype=np.float64),
            np.asarray(vertices["y"], dtype=np.float64),
            np.asarray(vertices["z"], dtype=np.float64),
        ),
        axis=1,
    )
    transformed = (rotation @ xyz.T).T + translation
    vertices["x"] = transformed[:, 0].astype(np.float32)
    vertices["y"] = transformed[:, 1].astype(np.float32)
    vertices["z"] = transformed[:, 2].astype(np.float32)

    if all(name in vertices.data.dtype.names for name in ("nx", "ny", "nz")):
        normals = np.stack(
            (
                np.asarray(vertices["nx"], dtype=np.float64),
                np.asarray(vertices["ny"], dtype=np.float64),
                np.asarray(vertices["nz"], dtype=np.float64),
            ),
            axis=1,
        )
        rotated_normals = (rotation @ normals.T).T
        vertices["nx"] = rotated_normals[:, 0].astype(np.float32)
        vertices["ny"] = rotated_normals[:, 1].astype(np.float32)
        vertices["nz"] = rotated_normals[:, 2].astype(np.float32)

    rotation_names = [name for name in vertices.data.dtype.names if name.startswith("rot_")]
    if len(rotation_names) == 4:
        quaternions = np.stack([np.asarray(vertices[name], dtype=np.float64) for name in rotation_names], axis=1)
        transform_quaternion = quaternion_from_rotation_matrix(rotation)
        transformed_quaternions = quaternion_multiply(
            np.repeat(transform_quaternion[None, :], len(quaternions), axis=0),
            quaternions,
        )
        for index, name in enumerate(rotation_names):
            vertices[name] = transformed_quaternions[:, index].astype(np.float32)


def filter_vertices(ply: PlyData, keep_mask: np.ndarray) -> PlyData:
    vertex = ply["vertex"]
    filtered = vertex.data[keep_mask]
    described = PlyElement.describe(filtered, "vertex")
    return PlyData(
        [described],
        text=ply.text,
        byte_order=ply.byte_order,
        comments=list(getattr(ply, "comments", []) or []),
        obj_info=list(getattr(ply, "obj_info", []) or []),
    )


def quaternion_from_rotation_matrix(rotation: np.ndarray) -> np.ndarray:
    trace = float(rotation[0, 0] + rotation[1, 1] + rotation[2, 2])
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (rotation[2, 1] - rotation[1, 2]) / s
        y = (rotation[0, 2] - rotation[2, 0]) / s
        z = (rotation[1, 0] - rotation[0, 1]) / s
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        s = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2.0
        w = (rotation[2, 1] - rotation[1, 2]) / s
        x = 0.25 * s
        y = (rotation[0, 1] + rotation[1, 0]) / s
        z = (rotation[0, 2] + rotation[2, 0]) / s
    elif rotation[1, 1] > rotation[2, 2]:
        s = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2.0
        w = (rotation[0, 2] - rotation[2, 0]) / s
        x = (rotation[0, 1] + rotation[1, 0]) / s
        y = 0.25 * s
        z = (rotation[1, 2] + rotation[2, 1]) / s
    else:
        s = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2.0
        w = (rotation[1, 0] - rotation[0, 1]) / s
        x = (rotation[0, 2] + rotation[2, 0]) / s
        y = (rotation[1, 2] + rotation[2, 1]) / s
        z = 0.25 * s
    quaternion = np.asarray([w, x, y, z], dtype=np.float64)
    quaternion /= np.linalg.norm(quaternion)
    return quaternion


def quaternion_multiply(q1: np.ndarray, q2: np.ndarray) -> np.ndarray:
    w1, x1, y1, z1 = q1[:, 0], q1[:, 1], q1[:, 2], q1[:, 3]
    w2, x2, y2, z2 = q2[:, 0], q2[:, 1], q2[:, 2], q2[:, 3]
    return np.stack(
        (
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 + y1 * w2 + z1 * x2 - x1 * z2,
            w1 * z2 + z1 * w2 + x1 * y2 - y1 * x2,
        ),
        axis=1,
    )
