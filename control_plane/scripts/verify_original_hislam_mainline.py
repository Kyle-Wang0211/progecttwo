#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


ROOT = Path("/Users/kaidongwang/Documents/progecttwo")
HI_SLAM2_ROOT = ROOT / "donor_whitebox/third_party/HI-SLAM2"
ALLOWED_HISLAM2_DIFFS = {
    "hislam2/midas/base_model.py",
    "hislam2/midas/omnidata.py",
    "setup.py",
    "scripts/preprocess_owndata.py",
    "thirdparty/diff-gaussian-rasterization/cuda_rasterizer/forward.cu",
    "thirdparty/diff-gaussian-rasterization/cuda_rasterizer/rasterizer_impl.h",
}


def check_contains(path: Path, needle: str) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    ok = needle in text
    return ok, f"{path}: expected to contain {needle!r}"


def check_not_contains(path: Path, needle: str) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    ok = needle not in text
    return ok, f"{path}: expected not to contain {needle!r}"


def run_remote(remote: str, command: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["ssh", *shlex.split(remote), command],
        text=True,
        capture_output=True,
    )
    ok = proc.returncode == 0
    detail = proc.stdout.strip() if proc.stdout.strip() else proc.stderr.strip()
    return ok, detail


def check_hislam2_diff_allowlist() -> tuple[bool, str]:
    proc = subprocess.run(
        ["git", "-C", str(HI_SLAM2_ROOT), "diff", "--name-only"],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "git diff failed"
        return False, f"{HI_SLAM2_ROOT}: {detail}"
    changed = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    unexpected = sorted(changed - ALLOWED_HISLAM2_DIFFS)
    if unexpected:
        return False, f"{HI_SLAM2_ROOT}: unexpected diffs remain: {', '.join(unexpected)}"
    return True, f"{HI_SLAM2_ROOT}: allowed diffs only ({', '.join(sorted(changed)) or 'clean'})"


def local_checks() -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []
    checks.append(
        check_contains(
            ROOT / "control_plane/worker_agent/config.py",
            'os.environ.get("AETHER_AUTOFALLBACK_POLICY", "official_default")',
        )
    )
    checks.append(
        check_contains(
            ROOT / "control_plane/worker_agent/main.py",
            'name="official_default"',
        )
    )
    checks.append(
        check_contains(
            ROOT / "control_plane/app/main.py",
            '"tier_name": "official_default"',
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "control_plane/app/main.py",
            "global100_seq",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "control_plane/app/main.py",
            "global67_seq",
        )
    )
    checks.append(
        check_contains(
            ROOT / "control_plane/scripts/setup_worker_node.sh",
            'AETHER_AUTOFALLBACK_POLICY="${AETHER_AUTOFALLBACK_POLICY:-official_default}"',
        )
    )
    checks.append(
        check_contains(
            ROOT / "control_plane/scripts/update_danish_worker_endpoints.sh",
            '"AETHER_AUTOFALLBACK_POLICY=": "AETHER_AUTOFALLBACK_POLICY=official_default"',
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh",
            'export AETHER_AUTOFALLBACK_POLICY="${AETHER_AUTOFALLBACK_POLICY:-official_default}"',
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
            'AUTOFALLBACK_POLICY="${AETHER_AUTOFALLBACK_POLICY:-official_default}"',
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
            "remote_run_hislam2_realvideo_prep_audit_phase.sh",
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
            "remote_run_hislam2_realvideo_train_phase.sh",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
            "postprocess_3dgs_ply.py",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
            "generate_sam2_video_masks.py",
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
            "scripts/preprocess_owndata.py",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
            "prepare_real_video_owndata.py",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
            "audit_real_video_frames.py",
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
            "demo.py",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
            "postprocess_3dgs_ply.py",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
            "generate_sam2_video_masks.py",
        )
    )
    checks.append(
        check_not_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
            "segcut_3dgs_ply.py",
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh",
            'USE_DYNAMIC_POSITION_LR_STEPS="${HI_SLAM2_DYNAMIC_POSITION_LR_STEPS:-0}"',
        )
    )
    checks.append(
        check_contains(
            ROOT / "donor_whitebox/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh",
            "default_steps = 26000",
        )
    )
    checks.append(check_hislam2_diff_allowlist())
    return checks


def remote_checks(remote: str) -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []
    remote_map = {
        "env_policy": "egrep -n 'AETHER_AUTOFALLBACK_POLICY=official_default' /root/control_plane/.env.worker",
        "env_dynamic": "egrep -n 'HI_SLAM2_DYNAMIC_POSITION_LR_STEPS=0' /root/control_plane/.env.worker",
        "worker_config": "grep -n 'official_default' /root/control_plane/worker_agent/config.py",
        "worker_main_tier": "grep -n 'name=\"official_default\"' /root/control_plane/worker_agent/main.py",
        "start_script": "grep -n 'AETHER_AUTOFALLBACK_POLICY:-official_default' /root/donor_whitebox/scripts/remote_start_realvideo_autofallback_run.sh",
        "autofallback_default": "grep -n 'AETHER_AUTOFALLBACK_POLICY:-official_default' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
        "prep_uses_official": "grep -n 'scripts/preprocess_owndata.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
        "train_uses_demo": "grep -n 'demo.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
        "runtime_steps_flag": "grep -n 'HI_SLAM2_DYNAMIC_POSITION_LR_STEPS:-0' /root/donor_whitebox/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh",
        "runtime_26000": "grep -n 'default_steps = 26000' /root/donor_whitebox/scripts/remote_run_hislam2_room3x3_official_owndata_runtime.sh",
    }
    for name, command in remote_map.items():
        ok, detail = run_remote(remote, command)
        checks.append((ok, f"{name}: {detail}"))
    for name, command in {
        "autofallback_postprocess_absent": "grep -n 'postprocess_3dgs_ply.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_autofallback.sh",
        "prep_custom_prepare_absent": "grep -n 'prepare_real_video_owndata.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
        "prep_audit_absent": "grep -n 'audit_real_video_frames.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_prep_audit_phase.sh",
        "train_segcut_absent": "grep -n 'segcut_3dgs_ply.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
        "train_postprocess_absent": "grep -n 'postprocess_3dgs_ply.py' /root/donor_whitebox/scripts/remote_run_hislam2_realvideo_train_phase.sh",
    }.items():
        ok, detail = run_remote(remote, command)
        checks.append((not ok, f"{name}: {detail or 'absent'}"))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", help="ssh target like root@host or -p via ~/.ssh config alias")
    args = parser.parse_args()

    checks = [("local", *item) for item in local_checks()]
    if args.remote:
        checks.extend(("remote", *item) for item in remote_checks(args.remote))

    failures = [(scope, detail) for scope, ok, detail in checks if not ok]
    for scope, ok, detail in checks:
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {scope} {detail}")

    if failures:
        print(f"\n{len(failures)} check(s) failed.", file=sys.stderr)
        return 1
    print("\nAll original-mainline checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
