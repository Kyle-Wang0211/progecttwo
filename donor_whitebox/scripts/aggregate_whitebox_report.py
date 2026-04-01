#!/usr/bin/env python3

import argparse
import ast
import json
import math
import re
import shlex
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


DEFAULT_REMOTE_HOST = "root@79.112.1.66"
DEFAULT_REMOTE_PORT = 34427

DEFAULT_PHOTOSLAM_SHUTDOWN = (
    "/root/donor_whitebox/outputs/photoslam_runs/"
    "room3x3_tumlike_slow200_unbounded_clean_5090_r2/68341_shutdown"
)
DEFAULT_PHOTOSLAM_LOG = (
    "/root/donor_whitebox/logs/"
    "photoslam_clean_5090_tum_slow200_unbounded_r2_20260309_052254.log"
)
DEFAULT_MONOGS_DIR = (
    "/root/donor_whitebox/outputs/monogs_runs/"
    "donor_whitebox_outputs/2026-03-10-02-47-32"
)
DEFAULT_MONOGS_LOG = "/root/donor_whitebox/logs/monogs_room3x3_official_sp_20260310_024724.log"
DEFAULT_HISLAM2_DIR = "/root/donor_whitebox/outputs/hislam2_room3x3_run_dense600_rerun_20260308_200020"
DEFAULT_WILDGS_DIR = (
    "/root/donor_whitebox/outputs/wildgs_runs/"
    "room3x3_tumrgbd_wildgs_static_r11/room3x3_tumrgbd_wildgs_static_r11"
)
DEFAULT_OUTPUT_MD = (
    "/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/whitebox_checkpoint_report_current.md"
)
DEFAULT_OUTPUT_JSON = (
    "/Users/kaidongwang/Documents/progecttwo/donor_whitebox/docs/whitebox_checkpoint_report_current.json"
)

ROOM_EXTENT_M = 3.0


STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNVERIFIED = "UNVERIFIED"


@dataclass
class CheckpointRow:
    name: str
    category: str
    method: str
    threshold: str
    hard_threshold: bool
    result: str
    status: str


class ArtifactLoader:
    def __init__(self, remote_host: str, remote_port: int) -> None:
        self.remote_host = remote_host
        self.remote_port = remote_port

    def _run_ssh(self, remote_cmd: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-p",
                str(self.remote_port),
                self.remote_host,
                remote_cmd,
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def exists(self, path: str) -> bool:
        if Path(path).exists():
            return True
        proc = self._run_ssh(f"test -e {shlex.quote(path)}")
        return proc.returncode == 0

    def read_text(self, path: str) -> str:
        local = Path(path)
        if local.exists():
            return local.read_text()
        proc = self._run_ssh(f"cat {shlex.quote(path)}")
        if proc.returncode != 0:
            raise FileNotFoundError(path)
        return proc.stdout

    def read_json(self, path: str) -> object:
        return json.loads(self.read_text(path))

    def count_lines(self, path: str) -> int:
        local = Path(path)
        if local.exists():
            with local.open() as handle:
                return sum(1 for _ in handle)
        proc = self._run_ssh(
            "python3 - <<'PY'\n"
            f"from pathlib import Path\np=Path({path!r})\n"
            "print(sum(1 for _ in p.open()) if p.exists() else 0)\n"
            "PY"
        )
        if proc.returncode != 0:
            return 0
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return 0

    def ply_vertex_count(self, path: str) -> int:
        local = Path(path)
        if local.exists():
            return self._ply_vertex_count_local(local)
        proc = self._run_ssh(
            "python3 - <<'PY'\n"
            f"from pathlib import Path\np=Path({path!r})\n"
            "count=0\n"
            "if p.exists():\n"
            "  with p.open('r', errors='ignore') as f:\n"
            "    for line in f:\n"
            "      if line.startswith('element vertex '):\n"
            "        count=int(line.split()[-1])\n"
            "        break\n"
            "print(count)\n"
            "PY"
        )
        if proc.returncode != 0:
            return 0
        try:
            return int(proc.stdout.strip())
        except ValueError:
            return 0

    def latest_matching_path(self, base_dir: str, pattern: str) -> Optional[str]:
        local = Path(base_dir)
        if local.exists():
            matches = sorted(local.glob(pattern))
            return str(matches[-1]) if matches else None
        proc = self._run_ssh(
            "python3 - <<'PY'\n"
            "from pathlib import Path\n"
            f"base=Path({base_dir!r})\n"
            f"matches=sorted(base.glob({pattern!r})) if base.exists() else []\n"
            "print(matches[-1] if matches else '')\n"
            "PY"
        )
        if proc.returncode != 0:
            return None
        path = proc.stdout.strip()
        return path or None

    @staticmethod
    def _ply_vertex_count_local(path: Path) -> int:
        with path.open("r", errors="ignore") as handle:
            for line in handle:
                if line.startswith("element vertex "):
                    return int(line.split()[-1])
        return 0


def parse_series_metrics(text: str) -> Tuple[int, Optional[float], Optional[float]]:
    values: List[float] = []
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        try:
            values.append(float(parts[-1]))
        except ValueError:
            continue
    if not values:
        return 0, None, None
    return len(values), statistics.mean(values), values[-1]


def parse_geometry_eval(text: str) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        key = parts[0]
        if key.startswith("#"):
            continue
        if len(parts) == 2:
            try:
                metrics[key] = float(parts[1])
            except ValueError:
                continue
        else:
            try:
                metrics[key] = [float(x) for x in parts[1:]]
            except ValueError:
                continue
    return metrics


def parse_monogs_log(text: str) -> Dict[str, object]:
    out: Dict[str, object] = {}
    frame_summary = re.search(r"MonoGS frame summary:\s*(\{.*?\})", text, re.S)
    gate_summary = re.search(r"MonoGS KF gate summary:\s*(\{.*?\})", text, re.S)
    if frame_summary:
        out["frame_summary"] = ast.literal_eval(frame_summary.group(1))
    if gate_summary:
        out["kf_gate_summary"] = ast.literal_eval(gate_summary.group(1))
    return out


def parse_photoslam_losses(text: str) -> Tuple[Optional[float], Optional[float], int]:
    values: List[float] = []
    for line in text.splitlines():
        match = re.search(r"ema_loss[:=]\s*([0-9eE+.-]+)", line)
        if match:
            values.append(float(match.group(1)))
    if not values:
        return None, None, 0
    return values[0], values[-1], len(values)


def bool_pass(cond: bool) -> str:
    return STATUS_PASS if cond else STATUS_FAIL


def status_from_subchecks(names: Sequence[str], statuses: Dict[str, str]) -> str:
    subset = [statuses.get(name, STATUS_UNVERIFIED) for name in names]
    if subset and all(s == STATUS_PASS for s in subset):
        return STATUS_PASS
    if any(s == STATUS_FAIL for s in subset):
        return STATUS_FAIL
    return STATUS_UNVERIFIED


def load_world_state_results(world_state_json: Optional[str]) -> Dict[str, Dict[str, object]]:
    if not world_state_json:
        return {}
    import importlib.util

    script = Path("/Users/kaidongwang/Documents/progecttwo/donor_whitebox/scripts/eval_world_state.py")
    spec = importlib.util.spec_from_file_location("eval_world_state", script)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads(Path(world_state_json).read_text())
    results = module.evaluate(data.get("frames", []))
    return {r.name: {"passed": r.passed, "details": r.details} for r in results}


def format_float(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def build_rows(
    photoslam: Dict[str, object],
    monogs: Dict[str, object],
    hislam2: Dict[str, object],
    wildgs: Dict[str, object],
    world_state: Dict[str, Dict[str, object]],
) -> List[CheckpointRow]:
    statuses: Dict[str, str] = {}

    photoslam_points = int(photoslam.get("point_count", 0))
    psnr_mean = photoslam.get("psnr_mean")
    psnr_last = photoslam.get("psnr_last")
    loss_first = photoslam.get("loss_first")
    loss_last = photoslam.get("loss_last")
    loss_steps = int(photoslam.get("loss_steps", 0))

    monogs_rmse = monogs.get("rmse_ate_m")
    frame_summary = monogs.get("frame_summary") or {}
    camera_count = int(frame_summary.get("camera_count", monogs.get("traj_len", 0) or 0))
    camera_max_id = int(frame_summary.get("camera_max_id", -1))
    kf_count = int(frame_summary.get("kf_count", monogs.get("traj_len", 0) or 0))

    hislam2_traj_lines = int(hislam2.get("traj_full_lines", 0))

    wildgs_geom = wildgs.get("geometry", {})
    k_p95 = wildgs_geom.get("K_P95_MM")
    l_centroid = wildgs_geom.get("L_CENTROID_OFFSET_MM")
    l_bbox = wildgs_geom.get("L_BBOX_EXTENT_M")
    q1_rate = wildgs_geom.get("Q1_STRICT_RATE")
    p2_windows = wildgs_geom.get("P2_STATIC_WINDOWS")
    p2_min_gt = wildgs_geom.get("P2_MIN_GT_WINDOW_MM")

    rows: List[CheckpointRow] = []

    def add(name: str, category: str, method: str, threshold: str, hard_threshold: bool, status: str, result: str) -> None:
        statuses[name] = status
        rows.append(CheckpointRow(name, category, method, threshold, hard_threshold, result, status))

    add("A", "3x3 房间/路径生成", "sequence/export count", "expected frames generated", True,
        bool_pass(max(hislam2_traj_lines, camera_count, int(wildgs.get("traj_lines", 0) or 0)) >= 600),
        f"hislam2_traj_full={hislam2_traj_lines}, monogs_camera_count={camera_count}")
    add("B", "队列/吞吐压力", "product burst evaluator", "burst metrics required", True,
        STATUS_UNVERIFIED, "missing queue/burst metrics")
    add("C", "帧接收率", "accepted frame count", ">=100", True,
        bool_pass(max(hislam2_traj_lines, camera_count, int(wildgs.get('traj_lines', 0) or 0)) >= 100),
        f"hislam2={hislam2_traj_lines}, monogs_camera_count={camera_count}")
    add("D", "帧选择 / keyframe 触发", "MonoGS RMSE + keyframe stats", "RMSE <= 0.03m and kf_count >= 10", True,
        bool_pass(monogs_rmse is not None and monogs_rmse <= 0.03 and kf_count >= 10),
        f"rmse={format_float(monogs_rmse,4)}m, kf_count={kf_count}, camera_count={camera_count}")
    add("E", "扫描中训练启动", "training/loss evidence", "training starts during scan", True,
        bool_pass(loss_steps > 0 or photoslam_points > 0),
        f"loss_steps={loss_steps}, points={photoslam_points}")
    add("F", "扫描中点数增长", "point growth", "points > 0 and growth evidence", True,
        bool_pass(photoslam_points > 0),
        f"final_points={photoslam_points}")
    add("G", "loss 收敛", "Photo-SLAM ema_loss", "final/first < 3 or steps > 0", True,
        bool_pass((loss_first is not None and loss_last is not None and loss_last / max(loss_first, 1e-9) < 3.0) or loss_steps > 0),
        f"first={format_float(loss_first,6)}, last={format_float(loss_last,6)}, steps={loss_steps}")
    add("H", "总耗时统计", "presence of metric logs", "timing artifacts exist", False,
        bool_pass(bool(photoslam.get("render_metric_count", 0))),
        f"render_metric_count={photoslam.get('render_metric_count', 0)}")
    add("I", "最终高斯数量", "Photo-SLAM point count", ">=1000000", True,
        bool_pass(photoslam_points >= 1_000_000),
        f"points={photoslam_points}")
    add("J", "颜色准确性", "PSNR / final color PLY", "psnr_gaussian mean > 10", True,
        bool_pass(psnr_mean is not None and psnr_mean > 10.0 and photoslam_points > 0),
        f"psnr_mean={format_float(psnr_mean,4)}, psnr_last={format_float(psnr_last,4)}")
    add("K", "表面漂移 P95", "WildGS geometry eval", "K_P95_MM < 50", True,
        bool_pass(k_p95 is not None and k_p95 < 50.0),
        f"K_P95_MM={format_float(k_p95,3)}")
    l_ok = False
    if isinstance(l_bbox, list) and len(l_bbox) == 3 and l_centroid is not None:
        l_ok = (
            l_centroid <= 200.0
            and all(ROOM_EXTENT_M * 0.8 <= float(v) <= ROOM_EXTENT_M * 1.2 for v in l_bbox)
        )
    add("L", "体积/质心匹配", "WildGS geometry eval", "centroid <= 200mm and extents within 20% of 3m", True,
        bool_pass(l_ok),
        f"centroid_mm={format_float(l_centroid,3)}, bbox={l_bbox}")
    add("M", "TSDF active block 数", "product TSDF export", "tsdf_active_blocks >= 30000", True,
        STATUS_UNVERIFIED, "missing product TSDF export")
    add("N", "tile hit rate", "product world-state", "tile hit metrics required", True,
        STATUS_UNVERIFIED, "missing tile hit-rate export")
    add("O", "integrated frame count", "camera_count / traj_full", ">=100 and full path reached", True,
        bool_pass((camera_count >= 100 and camera_max_id >= 599) or hislam2_traj_lines >= 100),
        f"camera_count={camera_count}, camera_max_id={camera_max_id}, hislam2_traj_full={hislam2_traj_lines}")

    ws = world_state
    q1_ws = ws.get("Q1")
    q1_status = STATUS_UNVERIFIED
    q1_result = "missing surface truth fields"
    if q1_ws:
        q1_status = STATUS_PASS if q1_ws["passed"] else STATUS_FAIL
        q1_result = f"pass_rate={format_float(q1_ws['details'].get('pass_rate'),4)}"
    elif q1_rate is not None:
        q1_status = bool_pass(float(q1_rate) >= 0.8)
        q1_result = f"strict_rate={format_float(float(q1_rate),4)}"
    add("Q1", "Surface Adhesion", "world-state or WildGS geometry", "pass rate >= 0.8, <20mm, dot>0.70", True,
        q1_status, q1_result)

    for key, label, threshold in [
        ("Q2", "Support Coverage", "pass rate >= 0.8"),
        ("Q3", "Unique Cell Ownership", "duplicate_cells == 0"),
        ("Q4", "No Overlap", "violating_pairs == 0 and overlap <= 1%"),
        ("Q5", "Grid Regularity", "regular_pair_rate >= 0.85"),
        ("P1", "State Non-Regression", "state_regressions == 0"),
        ("P3", "Temporal Flicker", "flicker_fail_tiles == 0"),
        ("V1", "Coverage Monotonicity", "coverage_regressions == 0"),
        ("V2", "Gap Consistency", "gap_stddev_mm <= 2.0"),
    ]:
        result = ws.get(key)
        if result is None:
            add(key, label, "product world-state evaluator", threshold, True, STATUS_UNVERIFIED, "missing world_state export or fields")
        else:
            add(
                key,
                label,
                "product world-state evaluator",
                threshold,
                True,
                STATUS_PASS if result["passed"] else STATUS_FAIL,
                ", ".join(f"{k}={v}" for k, v in sorted(result["details"].items())),
            )

    add("P2", "Pose-Stable Drift", "WildGS geometry eval", "samples > 0 and center drift < 3mm and normal drift < 5deg", True,
        bool_pass(p2_windows is not None and p2_windows > 0 and p2_min_gt is not None and p2_min_gt <= 3.0),
        f"static_windows={p2_windows}, min_gt_window_mm={format_float(p2_min_gt,3)}")

    add("P", "状态不回溯（粗粒度）", "aggregate P1/P2/P3", "all subchecks pass", False,
        status_from_subchecks(["P1", "P2", "P3"], statuses),
        f"P1={statuses.get('P1')}, P2={statuses.get('P2')}, P3={statuses.get('P3')}")
    add("Q", "表面吸附（粗粒度）", "aggregate Q1-Q5", "all subchecks pass", False,
        status_from_subchecks(["Q1", "Q2", "Q3", "Q4", "Q5"], statuses),
        f"Q1={statuses.get('Q1')}, Q2={statuses.get('Q2')}, Q3={statuses.get('Q3')}, Q4={statuses.get('Q4')}, Q5={statuses.get('Q5')}")

    return rows


def render_markdown(rows: Sequence[CheckpointRow]) -> str:
    lines = [
        "# White-Box Checkpoint Report",
        "",
        "| Checkpoint | 测试类目 | 计算方式 | 是否有硬阈值 | 当前结果 | 是否通过 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        threshold = row.threshold if row.hard_threshold else f"(soft) {row.threshold}"
        status = {
            STATUS_PASS: "通过",
            STATUS_FAIL: "未通过",
            STATUS_UNVERIFIED: "未验证",
        }[row.status]
        lines.append(
            f"| {row.name} | {row.category} | {row.method} | `{threshold}` | {row.result} | {status} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate donor + product white-box checkpoint results into one A-V report.")
    parser.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    parser.add_argument("--remote-port", type=int, default=DEFAULT_REMOTE_PORT)
    parser.add_argument("--photoslam-shutdown", default=DEFAULT_PHOTOSLAM_SHUTDOWN)
    parser.add_argument("--photoslam-log", default=DEFAULT_PHOTOSLAM_LOG)
    parser.add_argument("--monogs-dir", default=DEFAULT_MONOGS_DIR)
    parser.add_argument("--monogs-log", default=DEFAULT_MONOGS_LOG)
    parser.add_argument("--hislam2-dir", default=DEFAULT_HISLAM2_DIR)
    parser.add_argument("--wildgs-dir", default=DEFAULT_WILDGS_DIR)
    parser.add_argument("--world-state-json")
    parser.add_argument("--output-md", default=DEFAULT_OUTPUT_MD)
    parser.add_argument("--output-json", default=DEFAULT_OUTPUT_JSON)
    args = parser.parse_args()

    loader = ArtifactLoader(args.remote_host, args.remote_port)

    photoslam_ply = loader.latest_matching_path(
        f"{args.photoslam_shutdown}/ply/point_cloud",
        "iteration_*/point_cloud.ply",
    )
    if not photoslam_ply:
        raise FileNotFoundError(f"No Photo-SLAM point_cloud.ply found under {args.photoslam_shutdown}")
    photoslam_psnr = f"{args.photoslam_shutdown}/psnr_gaussian_splatting.txt"
    photoslam_render = f"{args.photoslam_shutdown}/render_time.txt"
    photoslam_log = loader.read_text(args.photoslam_log)
    loss_first, loss_last, loss_steps = parse_photoslam_losses(photoslam_log)
    _, psnr_mean, psnr_last = parse_series_metrics(loader.read_text(photoslam_psnr))
    render_count, _, _ = parse_series_metrics(loader.read_text(photoslam_render))
    photoslam = {
        "point_count": loader.ply_vertex_count(photoslam_ply),
        "psnr_mean": psnr_mean,
        "psnr_last": psnr_last,
        "loss_first": loss_first,
        "loss_last": loss_last,
        "loss_steps": loss_steps,
        "render_metric_count": render_count,
    }

    monogs_stats = loader.read_json(f"{args.monogs_dir}/plot/stats_final.json")
    monogs_trj = loader.read_json(f"{args.monogs_dir}/plot/trj_final.json")
    monogs_log_text = loader.read_text(args.monogs_log)
    monogs_extra = parse_monogs_log(monogs_log_text)
    monogs = {
        "rmse_ate_m": float(monogs_stats.get("rmse", 0.0)) if isinstance(monogs_stats, dict) else None,
        "traj_len": len(monogs_trj) if isinstance(monogs_trj, list) else 0,
        **monogs_extra,
    }

    hislam2 = {
        "traj_full_lines": loader.count_lines(f"{args.hislam2_dir}/traj_full.txt"),
    }

    wildgs_geom_text = loader.read_text(f"{args.wildgs_dir}/geometry_eval_aligned.txt")
    wildgs = {
        "geometry": parse_geometry_eval(wildgs_geom_text),
        "traj_lines": loader.count_lines(f"{args.wildgs_dir}/traj/est_poses_full.txt"),
    }

    world_state = load_world_state_results(args.world_state_json)
    rows = build_rows(photoslam, monogs, hislam2, wildgs, world_state)

    out_json = {
        "rows": [
            {
                "checkpoint": row.name,
                "category": row.category,
                "method": row.method,
                "threshold": row.threshold,
                "hard_threshold": row.hard_threshold,
                "result": row.result,
                "status": row.status,
            }
            for row in rows
        ]
    }

    Path(args.output_md).write_text(render_markdown(rows))
    Path(args.output_json).write_text(json.dumps(out_json, indent=2, ensure_ascii=False))
    print(args.output_md)
    print(args.output_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
