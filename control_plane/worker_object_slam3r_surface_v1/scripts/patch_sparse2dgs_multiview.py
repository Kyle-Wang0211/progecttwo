#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def patch_train_py(path: Path) -> bool:
    text = path.read_text()
    if "n_views=len(scene.getTrainCameras().copy())" in text:
        return False

    old = (
        '                         mask=gaussians.view_indexs[get_view_idx(scene.getTrainCameras().copy(), viewpoint_cam.image_name)].squeeze() > 0.5) * opt.lambda_dr\n'
    )
    new = (
        '                         mask=gaussians.view_indexs[get_view_idx(scene.getTrainCameras().copy(), viewpoint_cam.image_name)].squeeze() > 0.5,\n'
        '                         n_views=len(scene.getTrainCameras().copy())) * opt.lambda_dr\n'
    )
    if old not in text:
        raise RuntimeError(f"expected Sparse2DGS train.py pattern not found in {path}")
    path.write_text(text.replace(old, new))
    return True


def patch_loss_utils_py(path: Path) -> bool:
    text = path.read_text()
    if "object_slam3r_surface_multiview_guard" in text:
        return False

    old = (
        "def get_disk_reg_loss(gaussians, view_ref, view_src, surf_normal, mask, patch_num=7, n_views=3):\n"
        "    sample_num = patch_num**2\n"
        "    rand_mask, mask_sig = generate_binary_mask(n=mask.shape[0]//n_views, k=1024*10, n_views=n_views, device=mask.device)\n"
        "    mask = torch.logical_and(rand_mask, mask)\n"
        "    scales = gaussians.get_scaling[mask]#n 2\n"
        "    scales = torch.cat([scales, 0*torch.ones_like(scales[:,:1])], dim=-1) #n 3\n"
        "    gs_rotation = build_rotation(gaussians.get_rotation[mask]) #n 3 3\n"
        "    gs_normal = torch.nn.functional.normalize(gs_rotation[:, :, 2], p=2.0, dim=-1, eps=1e-12, out=None)#n 3\n"
        "    rend_normal = surf_normal.reshape(3, -1).permute(1, 0)[mask_sig] # 3 h w\n"
    )
    new = (
        "def get_disk_reg_loss(gaussians, view_ref, view_src, surf_normal, mask, patch_num=7, n_views=3):\n"
        "    sample_num = patch_num**2\n"
        "    # object_slam3r_surface_multiview_guard: Sparse2DGS upstream assumes 3 views.\n"
        "    # Our bridge can pass many more views, so align the regularizer mask lengths.\n"
        "    n_views = max(int(n_views), 1)\n"
        "    effective_n = int(mask.shape[0]) // n_views\n"
        "    if effective_n <= 0:\n"
        "        return gaussians.get_scaling.sum() * 0.0\n"
        "    effective_total = effective_n * n_views\n"
        "    if effective_total != mask.shape[0]:\n"
        "        mask = mask[:effective_total]\n"
        "    rand_mask, mask_sig = generate_binary_mask(n=effective_n, k=1024*10, n_views=n_views, device=mask.device)\n"
        "    if rand_mask.shape[0] != mask.shape[0]:\n"
        "        usable = min(rand_mask.shape[0], mask.shape[0])\n"
        "        rand_mask = rand_mask[:usable]\n"
        "        mask = mask[:usable]\n"
        "    mask = torch.logical_and(rand_mask, mask)\n"
        "    scales = gaussians.get_scaling[mask]#n 2\n"
        "    if scales.shape[0] == 0:\n"
        "        return gaussians.get_scaling.sum() * 0.0\n"
        "    scales = torch.cat([scales, 0*torch.ones_like(scales[:,:1])], dim=-1) #n 3\n"
        "    gs_rotation = build_rotation(gaussians.get_rotation[mask]) #n 3 3\n"
        "    gs_normal = torch.nn.functional.normalize(gs_rotation[:, :, 2], p=2.0, dim=-1, eps=1e-12, out=None)#n 3\n"
        "    rend_normal = surf_normal.reshape(3, -1).permute(1, 0)\n"
        "    if rend_normal.shape[0] != mask_sig.shape[0]:\n"
        "        usable = min(rend_normal.shape[0], mask_sig.shape[0])\n"
        "        rend_normal = rend_normal[:usable]\n"
        "        mask_sig = mask_sig[:usable]\n"
        "    rend_normal = rend_normal[mask_sig] # 3 h w\n"
    )
    if old not in text:
        raise RuntimeError(f"expected Sparse2DGS loss_utils.py pattern not found in {path}")
    path.write_text(text.replace(old, new))
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_sparse2dgs_multiview.py <repo_dir>", file=sys.stderr)
        return 64

    repo_dir = Path(sys.argv[1]).resolve()
    train_py = repo_dir / "train.py"
    loss_utils_py = repo_dir / "utils" / "loss_utils.py"
    if not train_py.is_file() or not loss_utils_py.is_file():
        print(f"sparse2dgs_patch_target_missing: {repo_dir}", file=sys.stderr)
        return 2

    changed = False
    changed |= patch_train_py(train_py)
    changed |= patch_loss_utils_py(loss_utils_py)
    print("sparse2dgs_multiview_patch=applied" if changed else "sparse2dgs_multiview_patch=already_applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
