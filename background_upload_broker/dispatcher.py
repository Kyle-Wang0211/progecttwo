from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from models import ArtifactPayload, JobRecord, JobState, WorkerAssignment


RuntimeUpdater = Callable[[str, dict], None]


@dataclass
class BrokerConfig:
    ssh_host: str = os.environ.get("BROKER_DANISH_SSH_HOST", "ssh7.vast.ai")
    ssh_port: int = int(os.environ.get("BROKER_DANISH_SSH_PORT", "24151"))
    ssh_user: str = os.environ.get("BROKER_DANISH_SSH_USER", "root")
    remote_input_directory: str = os.environ.get(
        "BROKER_DANISH_INPUT_DIR", "/root/donor_whitebox/inputs/real_videos"
    )
    remote_output_directory: str = os.environ.get(
        "BROKER_DANISH_OUTPUT_DIR", "/root/donor_whitebox/outputs"
    )
    remote_scripts_directory: str = os.environ.get(
        "BROKER_DANISH_SCRIPTS_DIR", "/root/donor_whitebox/scripts"
    )
    remote_logs_directory: str = os.environ.get(
        "BROKER_DANISH_LOGS_DIR", "/root/donor_whitebox/logs"
    )
    ssh_connect_timeout_sec: int = int(os.environ.get("BROKER_SSH_CONNECT_TIMEOUT_SEC", "20"))
    remote_start_ack_timeout_sec: int = int(os.environ.get("BROKER_REMOTE_START_ACK_TIMEOUT_SEC", "12"))
    poll_interval_sec: float = float(os.environ.get("BROKER_POLL_INTERVAL_SEC", "5"))
    stale_runtime_sec: int = int(os.environ.get("BROKER_STALE_RUNTIME_SEC", "120"))
    remote_upload_retry_count: int = int(os.environ.get("BROKER_REMOTE_UPLOAD_RETRY_COUNT", "3"))
    remote_upload_retry_backoff_sec: float = float(os.environ.get("BROKER_REMOTE_UPLOAD_RETRY_BACKOFF_SEC", "2"))
    ssh_command_retry_count: int = int(os.environ.get("BROKER_SSH_COMMAND_RETRY_COUNT", "5"))
    ssh_command_retry_backoff_sec: float = float(os.environ.get("BROKER_SSH_COMMAND_RETRY_BACKOFF_SEC", "2"))
    artifact_format: str = "ply"

    @property
    def ssh_target(self) -> str:
        return f"{self.ssh_user}@{self.ssh_host}"

    @property
    def start_script(self) -> str:
        return f"{self.remote_scripts_directory}/remote_start_realvideo_autofallback_run.sh"


class GPUDispatcher:
    """
    Broker-side dispatcher that uploads local files to the Danish worker via SSH,
    starts the remote run, and mirrors runtime status back into the broker store.
    """

    def __init__(self, config: Optional[BrokerConfig] = None) -> None:
        self.config = config or BrokerConfig()
        self._ssh_lock = threading.Lock()

    def assign(self, job_id: str) -> WorkerAssignment:
        return WorkerAssignment(
            worker_id=f"danish-{uuid.uuid4().hex[:8]}",
            queue_name="danish-golden-default",
        )

    def run_job(
        self,
        job: JobRecord,
        update_job: RuntimeUpdater,
        build_public_url: Callable[[str], str],
    ) -> None:
        update_job(
            job.job_id,
            {
                "state": JobState.QUEUED,
                "title": "后台已接收任务",
                "detail": "视频已落到 broker，正在准备发送到丹麦 5090。",
                "progress_fraction": 0.12,
                "progress_basis": "broker_dispatch",
            },
        )

        try:
            remote_input_path = self._upload_to_remote(job, update_job)
            update_job(
                job.job_id,
                {
                    "remote_input_path": remote_input_path,
                    "state": JobState.QUEUED,
                    "title": "已发往丹麦 5090",
                    "detail": "远端正在接收并启动本轮任务。",
                    "progress_fraction": 0.20,
                    "progress_basis": "broker_dispatch",
                },
            )

            remote_run_name = self._start_remote_job(job, remote_input_path)
            update_job(
                job.job_id,
                {
                    "remote_run_name": remote_run_name,
                    "remote_output_root": f"{self.config.remote_output_directory}/hislam2_{remote_run_name}",
                    "remote_started_at_epoch": time.time(),
                    "state": JobState.QUEUED,
                    "title": "丹麦 5090 已接单",
                    "detail": "远端已经开始跑闭环脚本。",
                    "progress_fraction": 0.24,
                    "progress_basis": "remote_dispatch",
                },
            )

            self._poll_until_terminal(job.job_id, remote_run_name, update_job, build_public_url)
        except subprocess.CalledProcessError as error:
            update_job(
                job.job_id,
                {
                    "state": JobState.FAILED,
                    "title": "远端生成失败",
                    "detail": self._format_subprocess_error(error),
                    "failure_reason": "broker_dispatch_failed",
                },
            )
        except Exception as error:  # pragma: no cover - best effort broker
            update_job(
                job.job_id,
                {
                    "state": JobState.FAILED,
                    "title": "远端生成失败",
                    "detail": str(error),
                    "failure_reason": "broker_runtime_error",
                },
            )

    def cancel_remote_job(self, job: JobRecord) -> None:
        if not job.remote_run_name:
            return
        pid_file = f"{self.config.remote_logs_directory}/{job.remote_run_name}.pid"
        cancel_marker = (
            f"{self.config.remote_output_directory}/hislam2_{job.remote_run_name}/summaries/CANCELLED.json"
        )
        command = f"""
set -e
mkdir -p $(dirname {self._shell_quote(cancel_marker)})
if [ -f {self._shell_quote(pid_file)} ]; then
  pid=$(cat {self._shell_quote(pid_file)} 2>/dev/null || true)
  if [ -n "$pid" ]; then
    pkill -TERM -P "$pid" >/dev/null 2>&1 || true
    kill -TERM "$pid" >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -P "$pid" >/dev/null 2>&1 || true
    kill -KILL "$pid" >/dev/null 2>&1 || true
  fi
fi
printf '{{"state":"cancelled","reason":"cancelled_by_user"}}\\n' > {self._shell_quote(cancel_marker)}
"""
        self._run_ssh(command, timeout=15)

    def ensure_local_artifact(self, job: JobRecord) -> Optional[Path]:
        if job.artifact_path and job.artifact_path.exists():
            return job.artifact_path
        if not job.remote_artifact_path:
            return None

        if not job.local_artifact_path:
            return None
        artifact_path = Path(job.local_artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._run_ssh(
            f"cat {self._shell_quote(job.remote_artifact_path)}",
            capture_output=True,
            binary=True,
            timeout=120,
        )
        artifact_path.write_bytes(payload)
        return artifact_path

    def _upload_to_remote(self, job: JobRecord, update_job: RuntimeUpdater) -> str:
        local_path = Path(job.local_upload_path or "")
        if not local_path.exists():
            raise RuntimeError("broker_missing_local_upload")

        remote_name = f"{job.job_id}{local_path.suffix.lower() or '.mov'}"
        remote_path = f"{self.config.remote_input_directory}/{remote_name}"
        remote_tmp_path = f"{remote_path}.uploading"
        self._run_ssh(f"mkdir -p {self._shell_quote(self.config.remote_input_directory)}", timeout=15)

        total = local_path.stat().st_size
        last_error: Exception | None = None
        for attempt in range(1, self.config.remote_upload_retry_count + 1):
            sent = 0
            remote_command = (
                f"rm -f {self._shell_quote(remote_tmp_path)} && "
                f"cat > {self._shell_quote(remote_tmp_path)}"
            )
            command = [
                "ssh",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                f"ConnectTimeout={self.config.ssh_connect_timeout_sec}",
                "-p",
                str(self.config.ssh_port),
                self.config.ssh_target,
                f"bash -lc {self._shell_quote(remote_command)}",
            ]
            try:
                with local_path.open("rb") as source:
                    with self._ssh_lock:
                        with subprocess.Popen(
                            command,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                        ) as process:
                            assert process.stdin is not None
                            while True:
                                chunk = source.read(4 * 1024 * 1024)
                                if not chunk:
                                    break
                                try:
                                    process.stdin.write(chunk)
                                except BrokenPipeError as error:
                                    stderr = b""
                                    stdout = b""
                                    try:
                                        process.stdin.close()
                                    except Exception:
                                        pass
                                    try:
                                        stdout, stderr = process.communicate(timeout=5)
                                    except Exception:
                                        process.kill()
                                        stdout, stderr = process.communicate()
                                    raise subprocess.CalledProcessError(
                                        process.returncode or 1,
                                        command,
                                        output=stdout,
                                        stderr=stderr or str(error).encode("utf-8"),
                                    )
                                sent += len(chunk)
                                retry_suffix = "" if attempt == 1 else f"（重试 {attempt}/{self.config.remote_upload_retry_count}）"
                                update_job(
                                    job.job_id,
                                    {
                                        "state": JobState.UPLOADING,
                                        "title": "正在上传到 broker 后端",
                                        "detail": (
                                            f"broker 正在把视频发送到丹麦 5090"
                                            f"{retry_suffix}（{sent / 1_048_576:.1f} MB / {total / 1_048_576:.1f} MB）。"
                                        ),
                                        "progress_fraction": min(0.18, 0.18 * (sent / max(total, 1))),
                                        "progress_basis": "broker_remote_upload_bytes",
                                    },
                                )
                            process.stdin.close()
                            stdout, stderr = process.communicate(timeout=max(60, int(total / 1_048_576)))
                            if process.returncode != 0:
                                raise subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=stderr)
                self._run_ssh(
                    f"mv {self._shell_quote(remote_tmp_path)} {self._shell_quote(remote_path)}",
                    timeout=30,
                )
                return remote_path
            except (BrokenPipeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                last_error = error
                try:
                    self._run_ssh(f"rm -f {self._shell_quote(remote_tmp_path)}", timeout=15)
                except Exception:
                    pass
                if attempt >= self.config.remote_upload_retry_count:
                    break
                update_job(
                    job.job_id,
                    {
                        "state": JobState.UPLOADING,
                        "title": "正在重新建立上传连接",
                        "detail": f"到丹麦 5090 的上传连接中断，正在重试第 {attempt + 1} 次。",
                        "progress_fraction": 0.10,
                        "progress_basis": "broker_remote_upload_retry",
                    },
                )
                time.sleep(self.config.remote_upload_retry_backoff_sec * attempt)
        if isinstance(last_error, subprocess.CalledProcessError):
            raise last_error
        if isinstance(last_error, subprocess.TimeoutExpired):
            raise RuntimeError("broker_remote_upload_timeout")
        if last_error is not None:
            raise last_error
        return remote_path

    def _start_remote_job(self, job: JobRecord, remote_input_path: str) -> str:
        run_name = f"mobile_{job.job_id}_official_default_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
        command = (
            f"bash {self._shell_quote(self.config.start_script)} "
            f"{self._shell_quote(run_name)} {self._shell_quote(remote_input_path)}"
        )
        self._run_ssh(command, timeout=30)
        accepted = self._confirm_remote_start_accepted(run_name)
        if not accepted:
            raise RuntimeError("remote_start_not_acknowledged")
        return run_name

    def _confirm_remote_start_accepted(self, run_name: str) -> bool:
        pid_file = f"{self.config.remote_logs_directory}/{run_name}.pid"
        launch_log = f"{self.config.remote_logs_directory}/{run_name}.launch.log"
        output_root = f"{self.config.remote_output_directory}/hislam2_{run_name}"
        command = f"""
deadline=$((SECONDS+{self.config.remote_start_ack_timeout_sec}))
while [ "$SECONDS" -lt "$deadline" ]; do
  if [ -s {self._shell_quote(pid_file)} ] || [ -f {self._shell_quote(launch_log)} ] || [ -d {self._shell_quote(output_root)} ]; then
    printf 'accepted\\n'
    exit 0
  fi
  sleep 1
done
printf 'not_accepted\\n'
exit 9
"""
        try:
            self._run_ssh(command, timeout=self.config.remote_start_ack_timeout_sec + 6)
            return True
        except subprocess.CalledProcessError:
            return False

    def _poll_until_terminal(
        self,
        job_id: str,
        run_name: str,
        update_job: RuntimeUpdater,
        build_public_url: Callable[[str], str],
    ) -> None:
        while True:
            snapshot = self._poll_remote_snapshot(run_name)
            payload = self._snapshot_to_update(job_id, snapshot, build_public_url)
            update_job(job_id, payload)
            state = snapshot["state"]
            if state in {"completed", "failed"}:
                return

            time.sleep(self.config.poll_interval_sec)

    def refresh_job(
        self,
        job_id: str,
        run_name: str,
        update_job: RuntimeUpdater,
        build_public_url: Callable[[str], str],
    ) -> Dict[str, object]:
        snapshot = self._poll_remote_snapshot(run_name)
        payload = self._snapshot_to_update(job_id, snapshot, build_public_url)
        update_job(job_id, payload)
        return payload

    def _snapshot_to_update(
        self,
        job_id: str,
        snapshot: dict,
        build_public_url: Callable[[str], str],
    ) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "title": snapshot["title"],
            "detail": snapshot["detail"],
            "progress_fraction": max(0.0, min(1.0, float(snapshot["progress"]) / 100.0)),
            "estimated_remaining_seconds": snapshot.get("estimated_remaining_sec"),
            "progress_basis": snapshot.get("progress_basis"),
            "elapsed_seconds": snapshot.get("elapsed_sec"),
            "last_runtime_seen_at_epoch": time.time(),
            "remote_artifact_path": snapshot.get("artifactPath"),
            "remote_summary_path": snapshot.get("summaryPath"),
            "remote_verdict_path": snapshot.get("verdictPath"),
        }
        state = snapshot["state"]
        payload["state"] = {
            "pending": JobState.QUEUED,
            "processing": {
                "sfm": JobState.RECONSTRUCTING,
                "train": JobState.TRAINING,
                "export": JobState.PACKAGING,
                "downloading": JobState.DOWNLOADING,
            }.get(snapshot.get("stage"), JobState.QUEUED),
            "completed": JobState.COMPLETED,
            "failed": JobState.FAILED,
        }[state]

        if state == "completed":
            payload["artifact"] = ArtifactPayload(
                download_url=build_public_url(f"/v1/mobile-jobs/{job_id}/artifact"),
                format=self.config.artifact_format,
            )
        if state == "failed":
            payload["failure_reason"] = snapshot.get("reason")
        return payload

    def _poll_remote_snapshot(self, run_name: str) -> dict:
        output_root = f"{self.config.remote_output_directory}/hislam2_{run_name}"
        launch_log = f"{self.config.remote_logs_directory}/{run_name}.launch.log"
        logs_root = f"{self.config.remote_logs_directory}/{run_name}"
        pid_file = f"{self.config.remote_logs_directory}/{run_name}.pid"
        script = f"""
python3 - <<'PY'
import json
import pathlib
import subprocess
import time

output_root = pathlib.Path({output_root!r})
summaries = output_root / "summaries"
launch_log = pathlib.Path({launch_log!r})
logs_root = pathlib.Path({logs_root!r})
pid_file = pathlib.Path({pid_file!r})
tiers = ["official_default"]

def load_json(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None

def extract_failure_detail(tier):
    log_path = logs_root / f"{{tier}}_prep.log"
    if not log_path.exists():
        return None
    text = log_path.read_text(errors="ignore")
    lowered = text.lower()
    if "no images with matches found in the database" in lowered:
        if "cameras.txt" in lowered or "failed to create sparse model" in lowered:
            return "没有建立出足够的图像匹配，未生成可用 sparse / cameras.txt"
        return "没有建立出足够的图像匹配"
    if "feature_extractor" in lowered and "sigkill" in lowered:
        return "COLMAP feature_extractor 被 SIGKILL 中止，疑似内存压力"
    if "failed to create sparse model" in lowered:
        return "未能建立可用的 sparse model"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else None

def summarize_all_tier_failures():
    compact = []
    details = []
    for tier in tiers:
        failure = load_json(summaries / f"{{tier}}_prep_failure.json")
        if not failure:
            continue
        reason = failure.get("reason") or "prep_failed"
        detail = extract_failure_detail(tier) or reason
        compact.append(f"{{tier}}={{reason}}")
        details.append(f"- {{tier}}: {{detail}}")
    if not compact:
        return ("all_tiers_failed", "远端尝试了多个 tier，但这次没有生成可用结果。")
    return ("all_tiers_failed:" + ";".join(compact), "\\n".join(["这次失败发生在远端预处理阶段："] + details))

def normalize_runtime_status(data):
    if not isinstance(data, dict):
        return None
    progress = data.get("progress")
    if isinstance(progress, (int, float)):
        progress = max(0.0, min(100.0, float(progress)))
    else:
        progress = None
    eta = data.get("estimated_remaining_sec")
    if not isinstance(eta, (int, float)) or eta < 0:
        eta = None
    elapsed = data.get("elapsed_sec")
    if not isinstance(elapsed, (int, float)) or elapsed < 0:
        elapsed = None
    return {{
        "state": data.get("state"),
        "stage": data.get("stage"),
        "detail": data.get("detail"),
        "reason": data.get("reason"),
        "progress": progress,
        "estimated_remaining_sec": int(eta) if eta is not None else None,
        "elapsed_sec": int(elapsed) if elapsed is not None else None,
        "progress_basis": data.get("progress_basis") or "runtime_status",
    }}

def read_ps_rows():
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid=,ppid=,command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows

def read_runner_pid():
    if not pid_file.exists():
        return None
    try:
        text = pid_file.read_text().strip()
        return int(text) if text else None
    except Exception:
        return None

selected = None
success = load_json(summaries / "SUCCESS.json") if summaries.exists() else None
if success:
    artifact_path_raw = str(success.get("artifact_path") or "").strip()
    artifact_path = pathlib.Path(artifact_path_raw) if artifact_path_raw else None
    if artifact_path and artifact_path.exists():
        summary_path_raw = str(success.get("summary_path") or "").strip()
        verdict_path_raw = str(success.get("verdict_path") or "").strip()
        selected = {{
            "artifactPath": str(artifact_path),
            "summaryPath": summary_path_raw if summary_path_raw and pathlib.Path(summary_path_raw).exists() else None,
            "verdictPath": verdict_path_raw if verdict_path_raw and pathlib.Path(verdict_path_raw).exists() else None,
        }}

if selected is None:
    for tier in tiers:
        summary = load_json(summaries / f"{{tier}}_full_summary.json")
        verdict = load_json(summaries / f"{{tier}}_full_verdict.json")
        candidate = output_root / f"{{tier}}_out" / "3dgs_final.ply"
        if summary and summary.get("has_3dgs_final") and candidate.exists():
            selected = {{
                "artifactPath": str(candidate),
                "summaryPath": str(summaries / f"{{tier}}_full_summary.json"),
                "verdictPath": str(summaries / f"{{tier}}_full_verdict.json") if verdict else None,
            }}
            break

cancelled = load_json(summaries / "CANCELLED.json") if summaries.exists() else None
summary_runtime = load_json(summaries / "RUNTIME_STATUS.json") if summaries.exists() else None
runtime_snapshot = normalize_runtime_status(summary_runtime)
runtime_status_age_sec = None
summary_runtime_path = summaries / "RUNTIME_STATUS.json"
if summary_runtime_path.exists():
    try:
        runtime_status_age_sec = max(0, int(time.time() - summary_runtime_path.stat().st_mtime))
    except OSError:
        runtime_status_age_sec = None

prep_dirs = [p for p in output_root.glob("*_prep") if p.is_dir()] if output_root.exists() else []
out_dirs = [p for p in output_root.glob("*_out") if p.is_dir()] if output_root.exists() else []

timestamps = []
for path in [launch_log, output_root, summaries]:
    if path.exists():
        try:
            timestamps.append(path.stat().st_mtime)
        except OSError:
            pass
for candidate in prep_dirs + out_dirs:
    try:
        timestamps.append(candidate.stat().st_mtime)
    except OSError:
        pass
if summaries.exists():
    for candidate in summaries.glob("*.json"):
        try:
            timestamps.append(candidate.stat().st_mtime)
        except OSError:
            pass

elapsed_sec = max(0, int(time.time() - min(timestamps))) if timestamps else None
last_activity_age_sec = max(0, int(time.time() - max(timestamps))) if timestamps else None
runner_pid = read_runner_pid()
runner_alive = False
job_worker_commands = []
rows = read_ps_rows()
children_by_parent = {{}}
for pid, ppid, command in rows:
    children_by_parent.setdefault(ppid, []).append((pid, command))

descendant_pids = set()
if runner_pid is not None:
    stack = [runner_pid]
    while stack:
        parent = stack.pop()
        for child_pid, _ in children_by_parent.get(parent, []):
            if child_pid in descendant_pids:
                continue
            descendant_pids.add(child_pid)
            stack.append(child_pid)

for pid, ppid, command in rows:
    is_probe_script = "<<'PY'" in command or "output_root = pathlib.Path" in command or "read_ps_rows()" in command
    if runner_pid is not None and pid == runner_pid:
        runner_alive = True
    if is_probe_script:
        continue
    if {run_name!r} in command or pid in descendant_pids:
        job_worker_commands.append(command)

payload = {{
    "state": "pending",
    "stage": "queued",
    "detail": "远端已经接收任务，正在准备下一阶段。",
    "reason": None,
    "progress": 24.0,
    "estimated_remaining_sec": None,
    "elapsed_sec": elapsed_sec,
    "progress_basis": "stage_budget",
    "artifactPath": None,
    "summaryPath": None,
    "verdictPath": None,
    "title": "任务排队中",
    "runtime_status_age_sec": runtime_status_age_sec,
}}

if selected is not None and pathlib.Path(selected["artifactPath"]).exists():
    payload.update({{
        "state": "completed",
        "stage": "complete",
        "title": "结果已完成",
        "detail": "远端训练完成，结果已经可下载。",
        "progress": 100.0,
        "estimated_remaining_sec": 0,
        "progress_basis": "completed",
        **selected,
    }})
elif cancelled is not None:
    payload.update({{
        "state": "failed",
        "stage": "cancelled",
        "title": "任务已取消",
        "detail": "这次远端任务已被取消。",
        "reason": cancelled.get("reason") or "cancelled",
        "progress": 42.0,
        "progress_basis": "cancelled",
    }})
elif (summaries / "all_tiers_failed.json").exists():
    reason, detail = summarize_all_tier_failures()
    payload.update({{
        "state": "failed",
        "stage": "failed",
        "title": "远端生成失败",
        "detail": detail,
        "reason": reason,
        "progress": 42.0,
        "progress_basis": "summary_failure",
    }})
elif runtime_snapshot is not None:
    stage_map = {{
        "queued": ("pending", "queued", "任务排队中"),
        "sfm": ("processing", "sfm", "正在做相机重建"),
        "train": ("processing", "train", "远端正在训练 3D 模型"),
        "export": ("processing", "export", "正在导出并整理结果"),
        "complete": ("completed", "complete", "结果已完成"),
        "failed": ("failed", "failed", "远端生成失败"),
    }}
    state, stage, title = stage_map.get(runtime_snapshot.get("stage"), ("processing", "queued", "远端正在处理中"))
    phase_name = runtime_snapshot.get("phase_name")
    if stage == "sfm" and phase_name == "audit":
        title = "正在做视角审核和筛选"
    payload.update({{
        "state": state,
        "stage": stage,
        "title": title,
        "detail": runtime_snapshot.get("detail") or payload["detail"],
        "reason": runtime_snapshot.get("reason"),
        "progress": runtime_snapshot.get("progress") if runtime_snapshot.get("progress") is not None else payload["progress"],
        "estimated_remaining_sec": runtime_snapshot.get("estimated_remaining_sec"),
        "elapsed_sec": runtime_snapshot.get("elapsed_sec") if runtime_snapshot.get("elapsed_sec") is not None else elapsed_sec,
        "progress_basis": runtime_snapshot.get("progress_basis"),
    }})
elif prep_dirs:
    payload.update({{
        "state": "processing",
        "stage": "sfm",
        "title": "正在做相机重建",
        "detail": "正在做相机重建和视角对齐。",
        "progress": 32.0,
        "progress_basis": "stage_prep_dir",
    }})

active_workers = [
    command for command in job_worker_commands
    if (
        "prepare_real_video_owndata.py" in command
        or "preprocess_owndata.py" in command
        or "audit_real_video_frames.py" in command
        or "demo.py" in command
        or "colmap " in command
        or "feature_extractor" in command
        or "mapper" in command
        or "bundle_adjuster" in command
        or "model_converter" in command
    )
]

runtime_stale = runtime_status_age_sec is not None and runtime_status_age_sec > {self.config.stale_runtime_sec}
worker_stalled = last_activity_age_sec is not None and last_activity_age_sec > {self.config.stale_runtime_sec}

runner_only_stale = (
    runner_alive
    and not active_workers
    and last_activity_age_sec is not None
    and last_activity_age_sec > ({self.config.stale_runtime_sec} * 2)
)

if payload["state"] == "processing" and not active_workers and ((not runner_alive and (runtime_stale or worker_stalled)) or runner_only_stale):
    payload.update({{
        "state": "failed",
        "stage": "failed",
        "title": "远端生成失败",
        "detail": "远端已经没有活跃的 prep/train worker，而且状态超过阈值没有更新，这次任务已经卡住。",
        "reason": "worker_stalled_or_runtime_stale",
        "progress_basis": "stalled_runner_shell",
        "estimated_remaining_sec": 0,
    }})

print(json.dumps(payload, ensure_ascii=False))
PY
"""
        payload = json.loads(self._run_ssh(script, timeout=30))
        return payload

    def _run_ssh(
        self,
        remote_command: str,
        *,
        capture_output: bool = True,
        binary: bool = False,
        timeout: int = 30,
    ) -> str | bytes:
        command = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.config.ssh_connect_timeout_sec}",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-p",
            str(self.config.ssh_port),
            self.config.ssh_target,
            f"bash -lc {self._shell_quote(remote_command)}",
        ]
        last_error: subprocess.CalledProcessError | subprocess.TimeoutExpired | None = None
        for attempt in range(1, self.config.ssh_command_retry_count + 1):
            try:
                with self._ssh_lock:
                    result = subprocess.run(
                        command,
                        capture_output=capture_output,
                        check=True,
                        timeout=timeout,
                    )
                if not capture_output:
                    return b"" if binary else ""
                return result.stdout if binary else result.stdout.decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired as error:
                last_error = error
            except subprocess.CalledProcessError as error:
                last_error = error
                if not self._is_transient_ssh_failure(error):
                    raise
            if attempt >= self.config.ssh_command_retry_count:
                break
            time.sleep(self.config.ssh_command_retry_backoff_sec * attempt)

        if last_error is not None:
            raise last_error
        raise RuntimeError("broker_ssh_command_failed_without_error")

    def _shell_quote(self, value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

    def _format_subprocess_error(self, error: subprocess.CalledProcessError) -> str:
        stderr = ""
        stdout = ""
        if isinstance(error.stderr, bytes):
            stderr = error.stderr.decode("utf-8", errors="replace").strip()
        elif isinstance(error.stderr, str):
            stderr = error.stderr.strip()
        if isinstance(error.output, bytes):
            stdout = error.output.decode("utf-8", errors="replace").strip()
        elif isinstance(error.output, str):
            stdout = error.output.strip()
        return stderr or stdout or f"subprocess_failed:{error.returncode}"

    def _is_transient_ssh_failure(self, error: subprocess.CalledProcessError) -> bool:
        text = self._format_subprocess_error(error).lower()
        transient_markers = [
            "kex_exchange_identification",
            "connection reset by peer",
            "connection closed by remote host",
            "connection closed",
            "broken pipe",
            "ssh_exchange_identification",
            "banner exchange",
            "resource temporarily unavailable",
        ]
        return any(marker in text for marker in transient_markers)
