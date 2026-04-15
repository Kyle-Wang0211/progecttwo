from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import traceback
import time

from .config import config
from .claim_loop import run_once
from .runtime import ControlPlaneClient
from .storage_client import ObjectStorageClient


_STALE_ROOT_DUPLICATES = (
    "contract_bridge_adapter.py",
    "curate_frames.py",
    "default_delivery_adapter.py",
    "run_optimize_default_mesh.py",
    "run_sparse2dgs_surface.py",
    "sparse2dgs_adapter.py",
)


def _hf_token_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("OBJECT_SLAM3R_SURFACE_HF_TOKEN_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    hf_home = os.environ.get("HF_HOME", "").strip()
    if hf_home:
        candidates.append(Path(hf_home).expanduser() / "token")
    candidates.extend(
        [
            Path.home() / ".cache" / "huggingface" / "token",
            Path.home() / ".huggingface" / "token",
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            deduped.append(path)
            seen.add(key)
    return deduped


def _hf_token_status() -> dict[str, object]:
    for env_name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_HUB_TOKEN"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            return {"configured": True, "source": f"env:{env_name}"}
    for path in _hf_token_candidates():
        if path.is_file():
            try:
                if path.read_text(encoding="utf-8").strip():
                    return {"configured": True, "source": f"file:{path}"}
            except OSError:
                continue
    return {"configured": False, "source": "missing"}


def _startup_layout_status() -> dict[str, object]:
    package_root = Path(__file__).resolve().parent
    stale_duplicates = [name for name in _STALE_ROOT_DUPLICATES if (package_root / name).exists()]
    return {
        "package_root": str(package_root),
        "adapters_dir": str(package_root / "adapters"),
        "pipeline_dir": str(package_root / "pipeline"),
        "stale_root_duplicates": stale_duplicates,
    }


def _probe_slam3r_runtime() -> dict[str, object]:
    python_bin = os.environ.get("OBJECT_SLAM3R_SURFACE_SLAM3R_PYTHON_BIN", "").strip()
    repo_dir = config.slam3r_repo.strip()
    if not python_bin:
        return {"python_bin": None, "ok": False, "error": "missing_python_bin"}
    if not repo_dir:
        return {"python_bin": python_bin, "ok": False, "error": "missing_repo_dir"}
    script = r"""
import contextlib
import importlib.util
import io
import json
import pathlib
import sys

repo_dir = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo_dir))
payload = {}

import torch

payload["torch"] = torch.__version__
payload["cuda"] = torch.version.cuda
payload["cuda_available"] = bool(torch.cuda.is_available())
if torch.cuda.is_available():
    payload["device_name"] = torch.cuda.get_device_name(0)
    payload["device_capability"] = list(torch.cuda.get_device_capability(0))

payload["xformers_installed"] = bool(importlib.util.find_spec("xformers"))
payload["curope_module"] = bool(importlib.util.find_spec("curope"))

capture = io.StringIO()
with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
    from slam3r.pos_embed import RoPE2D

payload["rope_impl"] = f"{RoPE2D.__module__}.{RoPE2D.__name__}"
payload["rope_warning"] = capture.getvalue().strip()
print(json.dumps(payload, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [python_bin, "-c", script, repo_dir],
            capture_output=True,
            text=True,
            timeout=45,
            env=dict(os.environ),
            check=False,
        )
    except Exception as exc:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": f"probe_failed:{type(exc).__name__}:{exc}",
        }
    if completed.returncode != 0:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": f"probe_exit_{completed.returncode}",
            "stderr": (completed.stderr or "").strip(),
            "stdout": (completed.stdout or "").strip(),
        }
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": "probe_no_output",
        }
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": "probe_invalid_json",
            "stdout": (completed.stdout or "").strip(),
        }
    payload["python_bin"] = python_bin
    payload["repo_dir"] = repo_dir
    payload["ok"] = True
    missing: list[str] = []
    device_capability = payload.get("device_capability")
    xformers_installed = bool(payload.get("xformers_installed"))
    xformers_required = False
    xformers_reason = "not_installed"
    if xformers_installed:
        xformers_reason = "installed"
    if isinstance(device_capability, list) and len(device_capability) >= 2:
        major = int(device_capability[0])
        minor = int(device_capability[1])
        if (major, minor) <= (9, 0):
            xformers_required = True
        else:
            xformers_reason = "disabled_for_slam3r_float32_on_cc_gt_90"
    payload["xformers_required_for_slam3r"] = xformers_required
    payload["xformers_usable_for_slam3r"] = bool(xformers_installed and xformers_required)
    payload["xformers_reason"] = xformers_reason
    if xformers_required and not xformers_installed:
        missing.append("xformers")
    rope_impl = str(payload.get("rope_impl") or "")
    if ".curope." not in rope_impl:
        missing.append("cuda_rope2d")
    payload["missing_accelerators"] = missing
    return payload


def _probe_matcha_runtime() -> dict[str, object]:
    package_root = Path(__file__).resolve().parent
    root_dir = package_root.parent.parent
    python_bin = os.environ.get("OBJECT_SLAM3R_SURFACE_MATCHA_PYTHON_BIN", "").strip()
    if not python_bin:
        python_bin = os.environ.get("OBJECT_SLAM3R_SURFACE_SLAM3R_PYTHON_BIN", "").strip()
    repo_dir = root_dir / "third_party" / "MAtCha"
    if not python_bin:
        return {"python_bin": None, "ok": False, "error": "missing_python_bin"}
    if not repo_dir.is_dir():
        return {
            "python_bin": python_bin,
            "repo_dir": str(repo_dir),
            "ok": False,
            "error": "missing_repo_dir",
        }
    script = r"""
import json
import pathlib
import sys

repo_dir = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo_dir / "2d-gaussian-splatting"))

payload = {}
checks = {
    "pytorch3d": "import pytorch3d as m; payload['pytorch3d_module'] = m.__file__",
    "pytorch3d.runtime": "from pytorch3d.renderer import FoVPerspectiveCameras, TexturesVertex, TexturesUV; from pytorch3d.ops import knn_points; from pytorch3d.structures import Meshes, join_meshes_as_scene; payload['pytorch3d_runtime_ok'] = True",
    "open3d": "import open3d as m; payload['open3d_module'] = m.__file__",
    "skimage": "import skimage as m; payload['skimage_module'] = m.__file__",
    "diff_surfel_rasterization": "import diff_surfel_rasterization as m; payload['diff_surfel_rasterization_module'] = m.__file__",
    "simple_knn._C": "import simple_knn._C as m; payload['simple_knn_module'] = m.__file__",
    "tetranerf_cpp_extension": "from tetranerf.utils.extension import tetranerf_cpp_extension as m; payload['tetranerf_module'] = m.__file__",
}
for name, code in checks.items():
    try:
        exec(code, {"payload": payload}, {"payload": payload})
        payload[name] = True
    except Exception as exc:
        payload[name] = False
        payload[f"{name}_error"] = f"{type(exc).__name__}: {exc}"

print(json.dumps(payload, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [python_bin, "-c", script, str(repo_dir)],
            capture_output=True,
            text=True,
            timeout=45,
            env=dict(os.environ),
            check=False,
        )
    except Exception as exc:
        return {
            "python_bin": python_bin,
            "repo_dir": str(repo_dir),
            "ok": False,
            "error": f"probe_failed:{type(exc).__name__}:{exc}",
        }
    if completed.returncode != 0:
        return {
            "python_bin": python_bin,
            "repo_dir": str(repo_dir),
            "ok": False,
            "error": f"probe_exit_{completed.returncode}",
            "stderr": (completed.stderr or "").strip(),
            "stdout": (completed.stdout or "").strip(),
        }
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        return {
            "python_bin": python_bin,
            "repo_dir": str(repo_dir),
            "ok": False,
            "error": "probe_no_output",
        }
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "python_bin": python_bin,
            "repo_dir": str(repo_dir),
            "ok": False,
            "error": "probe_invalid_json",
            "stdout": (completed.stdout or "").strip(),
        }
    payload["python_bin"] = python_bin
    payload["repo_dir"] = str(repo_dir)
    payload["ok"] = bool(
        payload.get("pytorch3d")
        and payload.get("pytorch3d.runtime")
        and payload.get("open3d")
        and payload.get("skimage")
        and payload.get("diff_surfel_rasterization")
        and payload.get("simple_knn._C")
        and payload.get("tetranerf_cpp_extension")
    )
    return payload


def _probe_sparse2dgs_runtime() -> dict[str, object]:
    python_bin = os.environ.get("OBJECT_SLAM3R_SURFACE_SPARSE2DGS_PYTHON_BIN", "").strip()
    repo_dir = config.sparse2dgs_repo.strip()
    if not python_bin:
        return {"python_bin": None, "ok": False, "error": "missing_python_bin"}
    if not repo_dir:
        return {"python_bin": python_bin, "ok": False, "error": "missing_repo_dir"}
    script = r"""
import importlib.util
import json
import pathlib
import sys

repo_dir = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo_dir))

payload = {}
for name in ("mediapy", "cv2", "matplotlib", "numpy", "torch"):
    payload[name] = bool(importlib.util.find_spec(name))

print(json.dumps(payload, ensure_ascii=False))
"""
    try:
        completed = subprocess.run(
            [python_bin, "-c", script, repo_dir],
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(os.environ),
            check=False,
        )
    except Exception as exc:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": f"probe_failed:{type(exc).__name__}:{exc}",
        }
    if completed.returncode != 0:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": f"probe_exit_{completed.returncode}",
            "stderr": (completed.stderr or "").strip(),
            "stdout": (completed.stdout or "").strip(),
        }
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": "probe_no_output",
        }
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {
            "python_bin": python_bin,
            "repo_dir": repo_dir,
            "ok": False,
            "error": "probe_invalid_json",
            "stdout": (completed.stdout or "").strip(),
        }
    payload["python_bin"] = python_bin
    payload["repo_dir"] = repo_dir
    payload["ok"] = bool(
        payload.get("mediapy")
        and payload.get("cv2")
        and payload.get("matplotlib")
        and payload.get("numpy")
        and payload.get("torch")
    )
    missing = [name for name in ("mediapy", "cv2", "matplotlib", "numpy", "torch") if not payload.get(name)]
    payload["missing_runtime_modules"] = missing
    return payload


def _startup_command_status() -> dict[str, object]:
    package_root = Path(__file__).resolve().parent
    scripts_dir = package_root / "scripts"

    def classify(name: str, template: str, expected_script: str, *, legacy_markers: tuple[str, ...] = ()) -> dict[str, object]:
        expected_path = str((scripts_dir / expected_script).resolve())
        raw = (template or "").strip()
        lowered = raw.lower()
        uses_expected_wrapper = expected_path in raw or expected_script in raw
        legacy_detected = any(marker in lowered for marker in legacy_markers)
        return {
            "name": name,
            "configured": bool(raw),
            "uses_expected_wrapper": uses_expected_wrapper,
            "legacy_detected": legacy_detected,
            "template": raw,
            "expected_wrapper": expected_path,
        }

    return {
        "slam3r": classify("slam3r", config.slam3r_command_template, "run_slam3r_official.sh"),
        "sparse2dgs": classify("sparse2dgs", config.sparse2dgs_command_template, "run_sparse2dgs_official.sh"),
        "matcha": classify(
            "matcha",
            config.matcha_command_template,
            "run_matcha_official.sh",
            legacy_markers=(
                "extract_mesh_adaptive_tsdf.py",
                "2d-gaussian-splatting/extract_mesh_adaptive_tsdf.py",
                "python 2d-gaussian-splatting",
            ),
        ),
    }


def _log_startup_self_check() -> None:
    layout = _startup_layout_status()
    hf_status = _hf_token_status()
    slam3r_runtime = _probe_slam3r_runtime()
    sparse2dgs_runtime = _probe_sparse2dgs_runtime()
    matcha_runtime = _probe_matcha_runtime()
    command_status = _startup_command_status()
    print(
        "[object_slam3r_surface_v1] startup_self_check "
        + json.dumps(
            {
                "layout": layout,
                "hf_token": hf_status,
                "command_status": command_status,
                "slam3r_runtime": slam3r_runtime,
                "sparse2dgs_runtime": sparse2dgs_runtime,
                "matcha_runtime": matcha_runtime,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )


def main() -> None:
    _log_startup_self_check()
    client = ControlPlaneClient(config.control_plane_base_url)
    storage = ObjectStorageClient()
    registration = client.register(worker_id=None)
    worker_id = registration["worker_id"]
    heartbeat_interval_sec = int(registration.get("heartbeat_interval_sec") or config.heartbeat_interval_sec)
    print(
        f"[object_slam3r_surface_v1] worker started worker_id={worker_id} base_url={config.control_plane_base_url} claim_enabled={config.claim_enabled}",
        flush=True,
    )
    if not config.claim_enabled:
        print(
            f"[object_slam3r_surface_v1] standby note: {config.standby_note}",
            flush=True,
        )

    last_heartbeat = 0.0
    while True:
        now = time.time()
        if now - last_heartbeat >= heartbeat_interval_sec:
            client.heartbeat(worker_id, state="idle", current_job_id=None)
            last_heartbeat = now
        try:
            claimed = run_once(client=client, storage=storage, worker_id=worker_id)
        except Exception:
            traceback.print_exc()
            print("[object_slam3r_surface_v1] worker loop exception", flush=True)
            claimed = False
            time.sleep(max(1.0, config.scheduler_tick_interval_sec))
        if not claimed:
            time.sleep(max(0.5, config.scheduler_tick_interval_sec))


if __name__ == "__main__":
    main()
