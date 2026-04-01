import os
import json
import argparse
import numpy as np
import open3d as o3d
import cv2
import time
from glob import glob
from tqdm import trange
from scipy.spatial.transform import Rotation as R


def to_se3_matrix(pvec):
    pose = np.eye(4)
    pose[:3, :3] = R.from_quat(pvec[4:]).as_matrix()
    pose[:3, 3] = pvec[1:4]
    return pose


def load_calib(result):
    return np.load(f"{result}/intrinsics.npy")


def load_extrinsic_np(result, stamps):
    poses = np.loadtxt(f"{result}/traj_full.txt")
    return [np.linalg.inv(to_se3_matrix(poses[int(s)])) for s in stamps]


def load_intrinsic_extrinsic_tensor(result, stamps):
    c = load_calib(result)
    intrinsic = o3d.core.Tensor(
        [[c[0], 0, c[2]], [0, c[1], c[3]], [0, 0, 1]],
        dtype=o3d.core.Dtype.Float64,
    )
    poses = load_extrinsic_np(result, stamps)
    poses = [o3d.core.Tensor(x, dtype=o3d.core.Dtype.Float64) for x in poses]
    return intrinsic, poses


def make_legacy_intrinsic(calib, color_file_name):
    image = cv2.imread(color_file_name, cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"failed to read color image for intrinsic sizing: {color_file_name}")
    height, width = image.shape[:2]
    return o3d.camera.PinholeCameraIntrinsic(
        width,
        height,
        float(calib[0]),
        float(calib[1]),
        float(calib[2]),
        float(calib[3]),
    )


def integrate_tensor(depth_file_names, color_file_names, intrinsic, extrinsic, args):
    n_files = len(depth_file_names)
    device_name = os.environ.get("OPEN3D_TSDF_DEVICE", "cuda:0")
    print(f"[tsdf] using device {device_name}")
    device = o3d.core.Device(device_name)

    vbg = o3d.t.geometry.VoxelBlockGrid(
        attr_names=("tsdf", "weight", "color"),
        attr_dtypes=(o3d.core.float32, o3d.core.float32, o3d.core.float32),
        attr_channels=((1), (1), (3)),
        voxel_size=args.voxel_size,
        block_count=100000,
        device=device,
    )

    start = time.time()
    skipped_frames = []
    skipped_far = 0

    pbar = trange(n_files, desc="Integration progress")
    for i in pbar:
        pbar.set_description(f"Integration progress, frame {i+1}/{n_files}")
        depth = o3d.t.io.read_image(depth_file_names[i]).to(device)
        color = o3d.t.io.read_image(color_file_names[i]).to(device)
        pose = extrinsic[i]
        dep = cv2.imread(depth_file_names[i], cv2.IMREAD_ANYDEPTH)
        if dep is None:
            skipped_frames.append(
                {
                    "frame_index": int(i),
                    "depth_file": os.path.basename(depth_file_names[i]),
                    "color_file": os.path.basename(color_file_names[i]),
                    "reason": "cv2_imread_failed",
                }
            )
            continue
        dep = dep.astype(np.float32) / args.depth_scale
        if dep.min() >= args.depth_max:
            skipped_far += 1
            continue

        try:
            frustum_block_coords = vbg.compute_unique_block_coordinates(
                depth, intrinsic, pose, args.depth_scale, args.depth_max
            )
            vbg.integrate(
                frustum_block_coords,
                depth,
                color,
                intrinsic,
                pose,
                args.depth_scale,
                args.depth_max,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "No block is touched in TSDF volume" in message:
                positive = dep[dep > 0]
                skipped_frames.append(
                    {
                        "frame_index": int(i),
                        "depth_file": os.path.basename(depth_file_names[i]),
                        "color_file": os.path.basename(color_file_names[i]),
                        "reason": "no_block_touched",
                        "depth_min_m": float(dep.min()),
                        "depth_max_m": float(dep.max()),
                        "depth_mean_m": float(positive.mean()) if positive.size else None,
                    }
                )
                print(
                    f"[tsdf] skip frame {i} depth={os.path.basename(depth_file_names[i])} "
                    f"color={os.path.basename(color_file_names[i])} reason=no_block_touched"
                )
                continue
            raise

    dt = time.time() - start
    print(f"Integration took {dt:.2f} seconds")
    return vbg, skipped_frames, skipped_far


def integrate_legacy(depth_file_names, color_file_names, calib, extrinsic_np, args):
    n_files = len(depth_file_names)
    sdf_trunc = float(os.environ.get("OPEN3D_TSDF_SDF_TRUNC", max(args.voxel_size * 4.0, 0.04)))
    print(f"[tsdf] using legacy CPU TSDF volume sdf_trunc={sdf_trunc:.6f}")
    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=float(args.voxel_size),
        sdf_trunc=sdf_trunc,
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    intrinsic = make_legacy_intrinsic(calib, color_file_names[0])

    start = time.time()
    skipped_frames = []
    skipped_far = 0
    pbar = trange(n_files, desc="Legacy TSDF integration")
    for i in pbar:
        pbar.set_description(f"Legacy TSDF integration, frame {i+1}/{n_files}")
        dep = cv2.imread(depth_file_names[i], cv2.IMREAD_ANYDEPTH)
        if dep is None:
            skipped_frames.append(
                {
                    "frame_index": int(i),
                    "depth_file": os.path.basename(depth_file_names[i]),
                    "color_file": os.path.basename(color_file_names[i]),
                    "reason": "cv2_imread_failed",
                }
            )
            continue
        dep_m = dep.astype(np.float32) / args.depth_scale
        if dep_m.min() >= args.depth_max:
            skipped_far += 1
            continue
        color = o3d.io.read_image(color_file_names[i])
        depth = o3d.io.read_image(depth_file_names[i])
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            color,
            depth,
            depth_scale=float(args.depth_scale),
            depth_trunc=float(args.depth_max),
            convert_rgb_to_intensity=False,
        )
        volume.integrate(rgbd, intrinsic, extrinsic_np[i])
    dt = time.time() - start
    print(f"Legacy integration took {dt:.2f} seconds")
    return volume, skipped_frames, skipped_far


def write_skipped(args, skipped_frames, skipped_far):
    if skipped_frames:
        skipped_path = f"{args.result}/tsdf_skipped_frames.json"
        with open(skipped_path, "w") as handle:
            json.dump(skipped_frames, handle, indent=2)
        print(f"TSDF skipped {len(skipped_frames)} frames -> {skipped_path}")
    print(f"TSDF skipped_far_frames {skipped_far}")


def extract_tensor_mesh(vbg, args):
    extract_device = os.environ.get("OPEN3D_TSDF_DEVICE", "cuda:0").lower()
    if extract_device.startswith("cpu"):
        print("[tsdf] using existing CPU VBG for mesh extraction")
        vbg_extract = vbg
    elif hasattr(vbg, "cpu"):
        print("[tsdf] creating CPU VBG copy before mesh extraction")
        vbg_extract = vbg.cpu()
    else:
        print("[tsdf] moving VBG to CPU before mesh extraction")
        vbg_extract = vbg.to(o3d.core.Device("CPU:0"))
    for w in args.weight:
        mesh = vbg_extract.extract_triangle_mesh(weight_threshold=w)
        mesh = mesh.to_legacy()
        out = f"{args.result}/tsdf_mesh_w{w:.1f}.ply"
        o3d.io.write_triangle_mesh(out, mesh)
        print(f"TSDF saved to {out}")


def extract_legacy_mesh(volume, args):
    mesh = volume.extract_triangle_mesh()
    mesh.compute_vertex_normals()
    for w in args.weight:
        out = f"{args.result}/tsdf_mesh_w{w:.1f}.ply"
        o3d.io.write_triangle_mesh(out, mesh)
        print(f"TSDF saved to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Integrate depth maps into TSDF")
    parser.add_argument("--result", type=str, required=True, help="Path to the result folder")
    parser.add_argument("--voxel_size", type=float, default=0.03, help="Voxel size")
    parser.add_argument("--depth_scale", type=float, default=6553.5, help="Depth scale")
    parser.add_argument("--depth_max", type=float, default=5.0, help="Maximum depth")
    parser.add_argument("--weight", type=float, default=[1], nargs="+", help="Weight threshold")
    args = parser.parse_args()

    depth_file_names = sorted(glob(f"{args.result}/renders/depth_after_opt/*"))
    color_file_names = sorted(glob(f"{args.result}/renders/image_after_opt/*"))
    stamps = [float(os.path.basename(i)[:-4]) for i in color_file_names]
    print(f"Found {len(depth_file_names)} depth maps and {len(color_file_names)} color images")

    use_legacy = os.environ.get("OPEN3D_TSDF_LEGACY", "0").lower() in {"1", "true", "yes"}
    if use_legacy:
        calib = load_calib(args.result)
        extrinsic_np = load_extrinsic_np(args.result, stamps)
        volume, skipped_frames, skipped_far = integrate_legacy(
            depth_file_names, color_file_names, calib, extrinsic_np, args
        )
        write_skipped(args, skipped_frames, skipped_far)
        extract_legacy_mesh(volume, args)
    else:
        intrinsic, extrinsic = load_intrinsic_extrinsic_tensor(args.result, stamps)
        vbg, skipped_frames, skipped_far = integrate_tensor(
            depth_file_names, color_file_names, intrinsic, extrinsic, args
        )
        write_skipped(args, skipped_frames, skipped_far)
        extract_tensor_mesh(vbg, args)
