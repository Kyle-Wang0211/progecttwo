#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-/root/gs_refs/WildGS-SLAM.clean}"
PYTHON_BIN="${PYTHON_BIN:-/venv/hislam2/bin/python}"

"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import re

path = Path("/root/gs_refs/WildGS-SLAM.clean/src/utils/mono_priors/img_feature_extractors.py")
text = path.read_text()

pattern = re.compile(
    r"    def get_intermediate_layers\([\s\S]*?^        return tuple\(outputs\)\n",
    re.MULTILINE,
)

new = """    def get_intermediate_layers(
        self,
        x: torch.Tensor,
        n=1,
        reshape: bool = False,
        return_prefix_tokens: bool = False,
        return_class_token: bool = False,
        norm: bool = True,
    ):
        import inspect

        def _normalize_outputs(result):
            if isinstance(result, torch.Tensor):
                outputs = (result,)
            else:
                result = tuple(result)
                if not result:
                    outputs = tuple()
                elif isinstance(result[0], torch.Tensor):
                    outputs = result
                elif (
                    isinstance(result[0], (tuple, list))
                    and len(result[0]) == 2
                    and isinstance(result[0][0], torch.Tensor)
                ):
                    if return_prefix_tokens or return_class_token:
                        return tuple((feat, prefix) for feat, prefix in result)
                    outputs = tuple(feat for feat, _ in result)
                else:
                    raise TypeError(f"Unsupported intermediate layer result type: {type(result[0])}")
            return tuple(outputs)

        if hasattr(self.model, "get_intermediate_layers"):
            sig = inspect.signature(self.model.get_intermediate_layers)
            kwargs = {}
            candidate_kwargs = {
                "n": n,
                "reshape": reshape,
                "return_prefix_tokens": return_prefix_tokens,
                "return_class_token": return_class_token,
                "norm": norm,
            }
            for key, value in candidate_kwargs.items():
                if key in sig.parameters:
                    kwargs[key] = value

            public_result = self.model.get_intermediate_layers(x, **kwargs)
            public_result = _normalize_outputs(public_result)

            if return_prefix_tokens or return_class_token:
                return public_result
            return public_result

        outputs = self.model._intermediate_layers(x, n)
        if norm:
            outputs = [self.model.norm(out) for out in outputs]
        if return_class_token:
            prefix_tokens = [out[:, 0] for out in outputs]
        else:
            prefix_tokens = [
                out[:, 0 : self.model.num_prefix_tokens] for out in outputs
            ]
        outputs = [out[:, self.model.num_prefix_tokens :] for out in outputs]

        if reshape:
            B, C, H, W = x.shape
            grid_size = (
                (H - self.model.patch_embed.patch_size[0])
                // self.model.patch_embed.proj.stride[0]
                + 1,
                (W - self.model.patch_embed.patch_size[1])
                // self.model.patch_embed.proj.stride[1]
                + 1,
            )
            outputs = [
                out.reshape(x.shape[0], grid_size[0], grid_size[1], -1)
                .permute(0, 3, 1, 2)
                .contiguous()
                for out in outputs
            ]

        if return_prefix_tokens or return_class_token:
            return tuple(zip(outputs, prefix_tokens))
        return tuple(outputs)
"""

text, count = pattern.subn(new, text, count=1)
if count != 1:
    raise SystemExit("img_feature_extractors Fit3D wrapper block not found")

path.write_text(text)
print("patched", path)
PY
