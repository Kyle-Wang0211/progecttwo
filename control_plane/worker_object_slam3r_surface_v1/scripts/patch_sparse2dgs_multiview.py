#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def patch_train_py(path: Path) -> bool:
    text = path.read_text()
    changed = False

    if "n_views=len(scene.getTrainCameras().copy())" not in text:
        old = (
            '                         mask=gaussians.view_indexs[get_view_idx(scene.getTrainCameras().copy(), viewpoint_cam.image_name)].squeeze() > 0.5) * opt.lambda_dr\n'
        )
        new = (
            '                         mask=gaussians.view_indexs[get_view_idx(scene.getTrainCameras().copy(), viewpoint_cam.image_name)].squeeze() > 0.5,\n'
            '                         n_views=len(scene.getTrainCameras().copy())) * opt.lambda_dr\n'
        )
        if old not in text:
            raise RuntimeError(f"expected Sparse2DGS train.py pattern not found in {path}")
        text = text.replace(old, new)
        changed = True

    if "object_slam3r_surface_feature_guard" not in text:
        old = '        fea_loss = get_fea_loss(render_pkg["feature_map"], viewpoint_cam.feature[0]) * opt.lambda_fea\n'
        new = (
            '        feature_map = render_pkg["feature_map"]\n'
            '        feature_ref = viewpoint_cam.feature[0]\n'
            '        # object_slam3r_surface_feature_guard: fallback gracefully when\n'
            '        # downgraded Sparse2DGS windows produce empty or mismatched feature maps.\n'
            '        if (\n'
            '            feature_map.ndim == 3\n'
            '            and feature_ref.ndim == 3\n'
            '            and feature_map.shape[0] > 0\n'
            '            and feature_map.shape == feature_ref.shape\n'
            '        ):\n'
            '            fea_loss = get_fea_loss(feature_map, feature_ref) * opt.lambda_fea\n'
            '        else:\n'
            '            fea_loss = image.sum() * 0.0\n'
        )
        if old not in text:
            raise RuntimeError(f"expected Sparse2DGS feature loss pattern not found in {path}")
        text = text.replace(old, new)
        changed = True

    if "object_slam3r_surface_feature_vis_guard" not in text:
        old = (
            '                mvsfea = transforms.ToPILImage()(visualize_feature_map(viewpoint_cam.feature)) \n'
            '                rendfea = transforms.ToPILImage()(visualize_feature_map(render_pkg["feature_map"][None])) \n'
            '                _, h, w = gt_image.shape\n'
        )
        new = (
            '                _, h, w = gt_image.shape\n'
            '                feature_vis_size = (w, h)\n'
            '                # object_slam3r_surface_feature_vis_guard: downgraded windows can\n'
            '                # legitimately produce empty feature channels. Skip debug PCA rather\n'
            '                # than aborting training for a visualization-only path.\n'
            '                def _safe_feature_vis(feature_tensor):\n'
            '                    if feature_tensor is None or getattr(feature_tensor, "ndim", 0) < 3:\n'
            '                        return Image.new("RGB", feature_vis_size)\n'
            '                    if feature_tensor.shape[-3] <= 0:\n'
            '                        return Image.new("RGB", feature_vis_size)\n'
            '                    try:\n'
            '                        return transforms.ToPILImage()(visualize_feature_map(feature_tensor))\n'
            '                    except Exception:\n'
            '                        return Image.new("RGB", feature_vis_size)\n'
            '                mvsfea = _safe_feature_vis(viewpoint_cam.feature)\n'
            '                rendfea = _safe_feature_vis(render_pkg["feature_map"][None])\n'
        )
        if old not in text:
            raise RuntimeError(f"expected Sparse2DGS feature visualization pattern not found in {path}")
        text = text.replace(old, new)
        changed = True

    if changed:
        path.write_text(text)
    return changed


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


def patch_gaussian_model_py(path: Path) -> bool:
    text = path.read_text()
    changed = False

    if "object_slam3r_surface_multiview_update_points" not in text:
        old = (
            "            view_index = self.view_indexs[i].squeeze() > 0.5 # 3n\n"
            "            selected_pts_mask = torch.logical_and(ncc < points_ncc, ncc < 0.5)\n"
            "            selected_pts_mask_all = selected_pts_mask[None, :].repeat(3, 1).reshape(-1)# n -> 3n\n"
            "            selected_pts_mask_all = torch.logical_and(selected_pts_mask_all, view_index.cuda())\n"
        )
        new = (
            "            # object_slam3r_surface_multiview_update_points: upstream assumes exactly 3 views.\n"
            "            view_index = self.view_indexs[i].squeeze() > 0.5\n"
            "            selected_pts_mask = torch.logical_and(ncc < points_ncc, ncc < 0.5)\n"
            "            point_count = max(int(selected_pts_mask.shape[0]), 1)\n"
            "            total_count = int(view_index.shape[0])\n"
            "            repeat_count = max(total_count // point_count, 1)\n"
            "            selected_pts_mask_all = selected_pts_mask[None, :].repeat(repeat_count, 1).reshape(-1)\n"
            "            if selected_pts_mask_all.shape[0] < total_count:\n"
            "                pad = total_count - selected_pts_mask_all.shape[0]\n"
            "                selected_pts_mask_all = torch.cat([\n"
            "                    selected_pts_mask_all,\n"
            "                    torch.zeros(pad, device=selected_pts_mask.device, dtype=torch.bool),\n"
            "                ])\n"
            "            elif selected_pts_mask_all.shape[0] > total_count:\n"
            "                selected_pts_mask_all = selected_pts_mask_all[:total_count]\n"
            "            selected_pts_mask_all = torch.logical_and(selected_pts_mask_all, view_index.cuda())\n"
        )
        if old not in text:
            raise RuntimeError(f"expected Sparse2DGS gaussian_model.py pattern not found in {path}")
        text = text.replace(old, new)
        changed = True

    if "object_slam3r_surface_checkpoint_guard" not in text:
        old = (
            "    def capture(self):\n"
            "        return (\n"
            "            self.active_sh_degree,\n"
            "            self._xyz,\n"
            "            self._features_dc,\n"
            "            self._features_rest,\n"
            "            self._scaling,\n"
            "            self._rotation,\n"
            "            self._opacity,\n"
            "            self.max_radii2D,\n"
            "            self.xyz_gradient_accum,\n"
            "            self.denom,\n"
            "            self.optimizer.state_dict(),\n"
            "            self.spatial_lr_scale,\n"
            "        )\n"
            "    \n"
            "    def restore(self, model_args, training_args):\n"
        )
        new = (
            "    def capture(self):\n"
            "        # object_slam3r_surface_checkpoint_guard: some torch/optimizer combinations\n"
            "        # fail to serialize optimizer state mid-training. Preserve the model weights\n"
            "        # so resume can still continue from the latest checkpoint.\n"
            "        optimizer_state = None\n"
            "        if self.optimizer is not None:\n"
            "            try:\n"
            "                optimizer_state = self.optimizer.state_dict()\n"
            "            except Exception as exc:\n"
            "                print(f\"[Sparse2DGS] warning: optimizer checkpoint state unavailable: {exc}\")\n"
            "        return (\n"
            "            self.active_sh_degree,\n"
            "            self._xyz,\n"
            "            self._features_dc,\n"
            "            self._features_rest,\n"
            "            self._scaling,\n"
            "            self._rotation,\n"
            "            self._opacity,\n"
            "            self.max_radii2D,\n"
            "            self.xyz_gradient_accum,\n"
            "            self.denom,\n"
            "            optimizer_state,\n"
            "            self.spatial_lr_scale,\n"
            "        )\n"
            "    \n"
            "    def restore(self, model_args, training_args):\n"
        )
        if old not in text:
            raise RuntimeError(f"expected Sparse2DGS checkpoint capture pattern not found in {path}")
        text = text.replace(old, new)
        old_restore = (
            "        self.training_setup(training_args)\n"
            "        self.xyz_gradient_accum = xyz_gradient_accum\n"
            "        self.denom = denom\n"
            "        self.optimizer.load_state_dict(opt_dict)\n"
        )
        new_restore = (
            "        self.training_setup(training_args)\n"
            "        self.xyz_gradient_accum = xyz_gradient_accum\n"
            "        self.denom = denom\n"
            "        if opt_dict is not None:\n"
            "            self.optimizer.load_state_dict(opt_dict)\n"
            "        else:\n"
            "            print(\"[Sparse2DGS] resuming without optimizer state\")\n"
        )
        if old_restore not in text:
            raise RuntimeError(f"expected Sparse2DGS checkpoint restore pattern not found in {path}")
        text = text.replace(old_restore, new_restore)
        changed = True

    if changed:
        path.write_text(text)
    return changed


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_sparse2dgs_multiview.py <repo_dir>", file=sys.stderr)
        return 64

    repo_dir = Path(sys.argv[1]).resolve()
    train_py = repo_dir / "train.py"
    loss_utils_py = repo_dir / "utils" / "loss_utils.py"
    gaussian_model_py = repo_dir / "scene" / "gaussian_model.py"
    if not train_py.is_file() or not loss_utils_py.is_file() or not gaussian_model_py.is_file():
        print(f"sparse2dgs_patch_target_missing: {repo_dir}", file=sys.stderr)
        return 2

    changed = False
    changed |= patch_train_py(train_py)
    changed |= patch_loss_utils_py(loss_utils_py)
    changed |= patch_gaussian_model_py(gaussian_model_py)
    print("sparse2dgs_multiview_patch=applied" if changed else "sparse2dgs_multiview_patch=already_applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
