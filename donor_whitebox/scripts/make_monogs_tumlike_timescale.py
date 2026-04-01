#!/usr/bin/env python3
import argparse
import os
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--time-scale", type=float, required=True)
    return parser.parse_args()


def rewrite_index(src_path: Path, dst_path: Path, scale: float):
    lines = []
    with src_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                lines.append(line)
                continue
            fields = line.split()
            fields[0] = f"{float(fields[0]) * scale:.6f}"
            lines.append(" ".join(fields))
    dst_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_symlink(dst: Path, target: Path):
    if dst.is_symlink():
        if dst.resolve() == target.resolve():
            return
        dst.unlink()
    elif dst.exists():
        raise RuntimeError(f"refusing to overwrite non-symlink path: {dst}")
    os.symlink(target, dst)


def main():
    args = parse_args()
    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    scale = args.time_scale

    if scale <= 0:
        raise RuntimeError("--time-scale must be > 0")

    output_dir.mkdir(parents=True, exist_ok=True)
    ensure_symlink(output_dir / "rgb", source_dir / "rgb")
    ensure_symlink(output_dir / "depth", source_dir / "depth")

    for name in ("rgb.txt", "depth.txt", "groundtruth.txt"):
        rewrite_index(source_dir / name, output_dir / name, scale)

    calib_src = source_dir / "calib.txt"
    if calib_src.exists():
        (output_dir / "calib.txt").write_text(calib_src.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"[make_monogs_tumlike_timescale] source={source_dir}")
    print(f"[make_monogs_tumlike_timescale] output={output_dir}")
    print(f"[make_monogs_tumlike_timescale] time_scale={scale}")


if __name__ == "__main__":
    main()
