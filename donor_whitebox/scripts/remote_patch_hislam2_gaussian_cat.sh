#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/root/gs_refs/HI-SLAM2}"
FILE="${ROOT}/hislam2/gaussian/scene/gaussian_model.py"

python3 - <<'PY' "${FILE}"
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()

helper = """

def _safe_cat_dim0(a, b):
    try:
        return torch.cat((a, b), dim=0)
    except RuntimeError as exc:
        message = str(exc)
        if "invalid configuration argument" not in message.lower():
            raise
        device = a.device
        return torch.cat((a.detach().cpu(), b.detach().cpu()), dim=0).to(device)
"""

if "_safe_cat_dim0" not in text:
    marker = "\n\nclass GaussianModel:\n"
    if marker not in text:
        raise SystemExit(f"marker not found in {path}")
    text = text.replace(marker, helper + marker, 1)

repls = {
    """                stored_state["exp_avg"] = torch.cat(
                    (stored_state["exp_avg"], torch.zeros_like(extension_tensor)), dim=0
                )
                stored_state["exp_avg_sq"] = torch.cat(
                    (stored_state["exp_avg_sq"], torch.zeros_like(extension_tensor)),
                    dim=0,
                )
""": """                stored_state["exp_avg"] = _safe_cat_dim0(
                    stored_state["exp_avg"], torch.zeros_like(extension_tensor)
                )
                stored_state["exp_avg_sq"] = _safe_cat_dim0(
                    stored_state["exp_avg_sq"], torch.zeros_like(extension_tensor)
                )
""",
    """                group["params"][0] = nn.Parameter(
                    torch.cat(
                        (group["params"][0], extension_tensor), dim=0
                    ).requires_grad_(True)
                )
""": """                group["params"][0] = nn.Parameter(
                    _safe_cat_dim0(group["params"][0], extension_tensor).requires_grad_(True)
                )
""",
    """                group["params"][0] = nn.Parameter(
                    torch.cat(
                        (group["params"][0], extension_tensor), dim=0
                    ).requires_grad_(True)
                )
                optimizable_tensors[group["name"]] = group["params"][0]
""": """                group["params"][0] = nn.Parameter(
                    _safe_cat_dim0(group["params"][0], extension_tensor).requires_grad_(True)
                )
                optimizable_tensors[group["name"]] = group["params"][0]
""",
}

for old, new in repls.items():
    if old in text:
        text = text.replace(old, new)

path.write_text(text)
print(path)
PY

sed -n '1,120p' "${FILE}"
sed -n '400,440p' "${FILE}"
