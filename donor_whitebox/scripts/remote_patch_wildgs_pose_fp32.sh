#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root/gs_refs/WildGS-SLAM.clean}"
TARGET="${ROOT_DIR}/src/utils/pose_utils.py"

python3 - <<'PY'
from pathlib import Path
import os

path = Path(os.environ.get("TARGET", "/root/gs_refs/WildGS-SLAM.clean/src/utils/pose_utils.py"))
text = path.read_text(encoding="utf-8")

old_v = """def V(theta):\n    dtype = theta.dtype\n    device = theta.device\n    I = torch.eye(3, device=device, dtype=dtype)\n    W = skew_sym_mat(theta)\n    W2 = W @ W\n    angle = torch.norm(theta)\n    if angle < 1e-5:\n        V = I + 0.5 * W + (1.0 / 6.0) * W2\n    else:\n        V = (\n            I\n            + W * ((1.0 - torch.cos(angle)) / (angle**2))\n            + W2 * ((angle - torch.sin(angle)) / (angle**3))\n        )\n    return V\n\n\ndef SE3_exp(tau):\n    dtype = tau.dtype\n    device = tau.device\n\n    rho = tau[:3]\n    theta = tau[3:]\n    R = SO3_exp(theta)\n    t = V(theta) @ rho\n\n    T = torch.eye(4, device=device, dtype=dtype)\n    T[:3, :3] = R\n    T[:3, 3] = t\n    return T\n\n\ndef update_pose(camera, converged_threshold=1e-4):\n    tau = torch.cat([camera.cam_trans_delta, camera.cam_rot_delta], axis=0)\n\n    T_w2c = torch.eye(4, device=tau.device)\n    T_w2c[0:3, 0:3] = camera.R\n    T_w2c[0:3, 3] = camera.T\n\n    new_w2c = SE3_exp(tau) @ T_w2c\n\n    new_R = new_w2c[0:3, 0:3]\n    new_T = new_w2c[0:3, 3]\n\n    converged = tau.norm() < converged_threshold\n    camera.update_RT(new_R, new_T)\n\n    camera.cam_rot_delta.data.fill_(0)\n    camera.cam_trans_delta.data.fill_(0)\n    return converged\n"""

new_v = """def V(theta):\n    dtype = theta.dtype\n    device = theta.device\n    I = torch.eye(3, device=device, dtype=dtype)\n    W = skew_sym_mat(theta)\n    W2 = W @ W\n    angle = torch.norm(theta)\n    if angle < 1e-5:\n        V = I + 0.5 * W + (1.0 / 6.0) * W2\n    else:\n        V = (\n            I\n            + W * ((1.0 - torch.cos(angle)) / (angle**2))\n            + W2 * ((angle - torch.sin(angle)) / (angle**3))\n        )\n    return V\n\n\ndef _matvec3_safe(mat, vec, out_dtype):\n    mat_f = mat.float().reshape(3, 3)\n    vec_f = vec.float().reshape(3)\n    out_f = torch.stack([(mat_f[i] * vec_f).sum() for i in range(3)], dim=0)\n    return out_f.to(out_dtype)\n\n\ndef SE3_exp(tau):\n    dtype = tau.dtype\n    device = tau.device\n\n    with torch.autocast(device_type=\"cuda\", enabled=False):\n        tau_f = tau.float()\n        rho = tau_f[:3]\n        theta = tau_f[3:]\n        R = SO3_exp(theta)\n        t = _matvec3_safe(V(theta), rho, torch.float32)\n\n        T = torch.eye(4, device=device, dtype=torch.float32)\n        T[:3, :3] = R\n        T[:3, 3] = t\n    return T.to(dtype)\n\n\ndef update_pose(camera, converged_threshold=1e-4):\n    tau = torch.cat([camera.cam_trans_delta, camera.cam_rot_delta], axis=0)\n\n    with torch.autocast(device_type=\"cuda\", enabled=False):\n        tau_f = tau.float()\n        T_w2c = torch.eye(4, device=tau.device, dtype=torch.float32)\n        T_w2c[0:3, 0:3] = camera.R.float()\n        T_w2c[0:3, 3] = camera.T.float()\n        new_w2c = torch.matmul(SE3_exp(tau_f).float(), T_w2c)\n\n    new_R = new_w2c[0:3, 0:3].to(camera.R.dtype)\n    new_T = new_w2c[0:3, 3].to(camera.T.dtype)\n\n    converged = tau.norm() < converged_threshold\n    camera.update_RT(new_R, new_T)\n\n    camera.cam_rot_delta.data.fill_(0)\n    camera.cam_trans_delta.data.fill_(0)\n    return converged\n"""

if old_v not in text:
    raise SystemExit(f"target block not found in {path}")

text = text.replace(old_v, new_v)
path.write_text(text, encoding="utf-8")
print(f"[remote_patch_wildgs_pose_fp32] patched {path}")
PY
