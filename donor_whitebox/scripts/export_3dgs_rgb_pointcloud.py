#!/usr/bin/env python3
import argparse
import math
import struct
from pathlib import Path


C0 = 0.28209479177387814
VERTEX_STRUCT = struct.Struct("<17f")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--min-alpha", type=float, default=0.5)
    p.add_argument("--max-scale", type=float, default=0.05)
    return p.parse_args()


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def sh_to_rgb(v: float) -> int:
    rgb = max(0.0, min(1.0, v * C0 + 0.5))
    return int(round(rgb * 255.0))


def read_header(fp):
    vertex_count = None
    while True:
        line = fp.readline()
        if not line:
            raise RuntimeError("invalid ply: missing end_header")
        text = line.decode("ascii", "ignore").strip()
        if text.startswith("element vertex "):
            vertex_count = int(text.split()[-1])
        if text == "end_header":
            break
    if vertex_count is None:
        raise RuntimeError("invalid ply: missing vertex count")
    return vertex_count


def main():
    args = parse_args()
    src = Path(args.input)
    dst = Path(args.output)
    kept = []

    with src.open("rb") as f:
        vertex_count = read_header(f)
        for _ in range(vertex_count):
            vals = VERTEX_STRUCT.unpack(f.read(VERTEX_STRUCT.size))
            alpha = sigmoid(vals[9])
            scale = math.exp((vals[10] + vals[11] + vals[12]) / 3.0)
            if alpha < args.min_alpha or scale > args.max_scale:
                continue
            kept.append(
                (
                    vals[0],
                    vals[1],
                    vals[2],
                    sh_to_rgb(vals[6]),
                    sh_to_rgb(vals[7]),
                    sh_to_rgb(vals[8]),
                    int(round(alpha * 255.0)),
                )
            )

    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {len(kept)}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property uchar red\n"
        "property uchar green\n"
        "property uchar blue\n"
        "property uchar alpha\n"
        "end_header\n"
    ).encode("ascii")

    out_struct = struct.Struct("<fffBBBB")
    with dst.open("wb") as f:
        f.write(header)
        for row in kept:
            f.write(out_struct.pack(*row))

    print(
        {
            "input_vertices": vertex_count,
            "kept_vertices": len(kept),
            "min_alpha": args.min_alpha,
            "max_scale": args.max_scale,
            "output": str(dst),
        }
    )


if __name__ == "__main__":
    main()
