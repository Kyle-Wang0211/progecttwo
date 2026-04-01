#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "https://api.aether-3d.com"
DEFAULT_SOURCE_VIDEO = (
    "/Users/kaidongwang/Documents/progecttwo/background_upload_broker/runtime/uploads/"
    "job_1744ed3710504fa7b805408c163358a4_66280ABA-7119-4AC7-8E31-FB639C2FDE97.MOV"
)
DEFAULT_CONTROL_PLANE_SSH_TARGET = "root@64.227.107.84"
DEFAULT_CONTROL_PLANE_SSH_PORT = 22
DEFAULT_WORKER_SSH_TARGET = "root@62.107.25.198"
DEFAULT_WORKER_SSH_PORT = 48601


class SmokeError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def run(
    cmd: list[str],
    *,
    input_text: str | None = None,
    timeout: float | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise SmokeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def curl_request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
) -> tuple[int, dict[str, str], str]:
    payload_text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) if payload is not None else None
    with tempfile.TemporaryDirectory(prefix="aether_api_") as temp_dir:
        headers_path = Path(temp_dir) / "headers.txt"
        body_path = Path(temp_dir) / "body.bin"
        cmd = [
            "curl",
            "-sS",
            "-D",
            str(headers_path),
            "-o",
            str(body_path),
            "-w",
            "%{http_code}",
            "--max-time",
            str(int(max(1, timeout))),
            "-X",
            method.upper(),
            url,
        ]
        if follow_redirects:
            cmd.append("-L")
        for key, value in (headers or {}).items():
            cmd.extend(["-H", f"{key}: {value}"])
        if payload_text is not None:
            cmd.extend(["-H", "Content-Type: application/json", "--data-binary", "@-"])
        result = run(cmd, input_text=payload_text, timeout=timeout + 5)
        status = int(result.stdout.strip() or "0")
        response_headers = parse_http_headers(headers_path.read_text(encoding="utf-8", errors="replace"))
        body_text = body_path.read_text(encoding="utf-8", errors="replace") if body_path.exists() else ""
        return status, response_headers, body_text


def api_json(method: str, url: str, payload: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    status, _headers, body = curl_request(method, url, payload=payload, timeout=timeout)
    if status >= 400:
        raise SmokeError(f"api {method} {url} failed with HTTP {status}: {body}")
    if not body:
        return {}
    return json.loads(body)


def parse_http_headers(raw_text: str) -> dict[str, str]:
    blocks = [block for block in raw_text.replace("\r\n", "\n").split("\n\n") if block.strip().startswith("HTTP/")]
    if not blocks:
        return {}
    headers: dict[str, str] = {}
    for line in blocks[-1].splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    return headers


def curl_put_file(file_path: Path, url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], str]:
    with tempfile.TemporaryDirectory(prefix="aether_upload_") as temp_dir:
        headers_path = Path(temp_dir) / "headers.txt"
        body_path = Path(temp_dir) / "body.bin"
        cmd = [
            "curl",
            "-sS",
            "-D",
            str(headers_path),
            "-o",
            str(body_path),
            "-w",
            "%{http_code}",
            "-X",
            "PUT",
            url,
        ]
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])
        cmd.extend(["-T", str(file_path)])
        result = run(cmd)
        status = int(result.stdout.strip() or "0")
        response_headers = parse_http_headers(headers_path.read_text(encoding="utf-8", errors="replace"))
        body_text = body_path.read_text(encoding="utf-8", errors="replace") if body_path.exists() else ""
        return status, response_headers, body_text


def ssh_bash(target: str, port: int, script: str, *args: str, timeout: float = 120.0) -> str:
    cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-p",
        str(port),
        target,
        "bash",
        "-s",
        "--",
        *args,
    ]
    return run(cmd, input_text=script, timeout=timeout).stdout


def ensure_clip(
    *,
    source_video: Path,
    clip_path: Path,
    start_sec: int,
    duration_sec: int,
    width_px: int,
    crf: int,
) -> Path:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_sec),
        "-i",
        str(source_video),
        "-t",
        str(duration_sec),
        "-vf",
        f"scale={width_px}:-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(clip_path),
    ]
    log(f"CLIP_BUILD path={clip_path} duration={duration_sec}s width={width_px} crf={crf}")
    run(cmd, timeout=1800)
    return clip_path


def create_mobile_job(base_url: str, file_path: Path, content_type: str, client_record_id: str) -> dict[str, Any]:
    payload = {
        "fileName": file_path.name,
        "fileSizeBytes": file_path.stat().st_size,
        "contentType": content_type,
        "captureOrigin": "photoLibrary",
        "clientRecordId": client_record_id,
    }
    return api_json("POST", f"{base_url.rstrip('/')}/v1/mobile-jobs", payload)


def upload_via_contract(
    base_url: str,
    job_id: str,
    contract: dict[str, Any],
    file_path: Path,
    content_type: str,
    *,
    limit_parts: int | None = None,
    complete_upload: bool = True,
) -> list[dict[str, Any]]:
    kind = str(contract.get("kind") or "single").strip().lower()
    if kind == "single":
        status, _headers, body = curl_put_file(file_path, str(contract["url"]), dict(contract.get("headers") or {}))
        if status not in {200, 204}:
            raise SmokeError(f"single upload failed for {job_id}: HTTP {status} body={body}")
        api_json(
            "POST",
            f"{base_url.rstrip('/')}/v1/jobs/{job_id}/upload-complete",
            {"etag": "", "size_bytes": file_path.stat().st_size},
        )
        return []

    if kind not in {"multipart", "chunked"}:
        raise SmokeError(f"unsupported upload kind: {kind}")

    part_size = int(contract.get("partSizeBytes") or contract.get("part_size_bytes") or 0)
    if part_size <= 0:
        raise SmokeError(f"invalid multipart part size for {job_id}: {part_size}")

    parts_payload: list[dict[str, Any]] = []
    parts = list(contract.get("parts") or [])
    with file_path.open("rb") as handle:
        for index, part in enumerate(parts, start=1):
            if limit_parts is not None and index > limit_parts:
                break
            part_number = int(part.get("partNumber") or part.get("part_number"))
            chunk = handle.read(part_size)
            if not chunk:
                raise SmokeError(f"missing data for multipart part {part_number} of {job_id}")

            with tempfile.NamedTemporaryFile(prefix=f"aether_part_{part_number}_", delete=False) as temp_handle:
                temp_handle.write(chunk)
                temp_part_path = Path(temp_handle.name)

            try:
                status, response_headers, body = curl_put_file(
                    temp_part_path,
                    str(part["url"]),
                    dict(part.get("headers") or {}),
                )
            finally:
                temp_part_path.unlink(missing_ok=True)

            if status not in {200, 204}:
                raise SmokeError(f"multipart part {part_number} failed for {job_id}: HTTP {status} body={body}")

            etag = response_headers.get("etag")
            if not etag:
                raise SmokeError(f"multipart part {part_number} missing etag for {job_id}")
            parts_payload.append({"partNumber": part_number, "etag": etag})

            part_ready_url = str(contract.get("partReadyURL") or contract.get("part_ready_url") or "")
            if part_ready_url:
                api_json(
                    "POST",
                    part_ready_url,
                    {
                        "uploadId": contract.get("uploadId") or contract.get("upload_id"),
                        "storageKey": contract.get("storageKey") or contract.get("storage_key"),
                        "partNumber": part_number,
                        "etag": etag,
                        "uploadedBytes": min(file_path.stat().st_size, part_number * part_size),
                        "completedPartCount": len(parts_payload),
                        "totalPartCount": len(parts),
                    },
                )

    if not complete_upload:
        return parts_payload

    complete_url = str(contract.get("completeURL") or contract.get("complete_url") or "")
    if not complete_url:
        raise SmokeError(f"multipart complete URL missing for {job_id}")

    api_json(
        "POST",
        complete_url,
        {
            "uploadId": contract.get("uploadId") or contract.get("upload_id"),
            "storageKey": contract.get("storageKey") or contract.get("storage_key"),
            "parts": parts_payload,
            "sizeBytes": file_path.stat().st_size,
        },
    )
    return parts_payload


def poll_mobile_job(
    base_url: str,
    job_id: str,
    *,
    timeout_sec: float,
    poll_interval_sec: float,
    stop_when,
) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last_signature: tuple[Any, ...] | None = None
    while time.time() < deadline:
        status = api_json("GET", f"{base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
        signature = (
            status.get("state"),
            status.get("title"),
            status.get("detail"),
            status.get("progress_fraction"),
            status.get("cancel_acknowledged"),
        )
        if signature != last_signature:
            log(
                "STATUS "
                f"job_id={job_id} state={status.get('state')} progress={status.get('progress_fraction')} "
                f"title={status.get('title')} detail={status.get('detail')}"
            )
            last_signature = signature
        if stop_when(status):
            return status
        time.sleep(poll_interval_sec)
    raise SmokeError(f"timed out waiting for job {job_id}")


def worker_snapshot(worker_target: str, worker_port: int, job_id: str) -> dict[str, Any]:
    script = r"""#!/usr/bin/env bash
set -euo pipefail
job_id="$1"
run_prefix="mobile_${job_id}_autofallback_"
echo PROCS
ps -axo pid=,args= | grep "$job_id" | grep -v "bash -s -- ${job_id}" | grep -v "grep ${job_id}" || true
echo END_PROCS
echo PATHS
ls -1d \
  /root/donor_whitebox/outputs/hislam2_${run_prefix}* \
  /root/donor_whitebox/logs/${run_prefix}* \
  /tmp/aether-control-worker/inputs/${job_id}* \
  /root/donor_whitebox/outputs/_retained_final_3dgs/${job_id}_* \
  2>/dev/null || true
echo END_PATHS
echo LIVENESS
cat /root/control_plane/runtime/worker_agent.liveness.json 2>/dev/null || true
echo END_LIVENESS
"""
    output = ssh_bash(worker_target, worker_port, script, job_id, timeout=120)
    sections: dict[str, list[str]] = {"PROCS": [], "PATHS": [], "LIVENESS": []}
    current: str | None = None
    for line in output.splitlines():
        if line in sections:
            current = line
            continue
        if line.startswith("END_"):
            current = None
            continue
        if current:
            sections[current].append(line)

    liveness = None
    liveness_text = "\n".join(sections["LIVENESS"]).strip()
    if liveness_text:
        try:
            liveness = json.loads(liveness_text)
        except json.JSONDecodeError:
            liveness = {"raw": liveness_text}

    return {
        "processes": [line for line in sections["PROCS"] if line.strip()],
        "paths": [line for line in sections["PATHS"] if line.strip()],
        "liveness": liveness,
    }


def list_spaces_prefix(control_plane_target: str, control_plane_port: int, prefix: str) -> list[str]:
    script = r"""#!/usr/bin/env bash
set -euo pipefail
prefix="$1"
set -a
. /root/control_plane/.env.runtime
set +a
/root/control_plane/.venv/bin/python - "$prefix" <<'PY'
import os
import sys

import boto3

client = boto3.client(
    "s3",
    region_name=os.environ["CONTROL_PLANE_OBJECT_STORAGE_REGION"],
    endpoint_url=os.environ["CONTROL_PLANE_OBJECT_STORAGE_ENDPOINT_URL"],
    aws_access_key_id=os.environ["CONTROL_PLANE_OBJECT_STORAGE_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["CONTROL_PLANE_OBJECT_STORAGE_SECRET_ACCESS_KEY"],
)

kwargs = {
    "Bucket": os.environ["CONTROL_PLANE_OBJECT_STORAGE_BUCKET"],
    "Prefix": sys.argv[1],
}

while True:
    response = client.list_objects_v2(**kwargs)
    for item in response.get("Contents") or []:
        key = item.get("Key")
        if key:
            print(key)
    if not response.get("IsTruncated"):
        break
    kwargs["ContinuationToken"] = response["NextContinuationToken"]
PY
"""
    output = ssh_bash(control_plane_target, control_plane_port, script, prefix, timeout=180)
    return [line.strip() for line in output.splitlines() if line.strip()]


def verify_artifact_download(download_url: str) -> int:
    with tempfile.TemporaryDirectory(prefix="aether_artifact_probe_") as temp_dir:
        headers_path = Path(temp_dir) / "headers.txt"
        body_path = Path(temp_dir) / "body.bin"
        cmd = [
            "curl",
            "-sS",
            "-L",
            "-D",
            str(headers_path),
            "-o",
            str(body_path),
            "-w",
            "%{http_code}",
            "-H",
            "Range: bytes=0-1023",
            download_url,
        ]
        result = run(cmd, timeout=65)
        status = int(result.stdout.strip() or "0")
        headers = parse_http_headers(headers_path.read_text(encoding="utf-8", errors="replace"))
        if status >= 400:
            body_text = body_path.read_text(encoding="utf-8", errors="replace") if body_path.exists() else ""
            raise SmokeError(f"artifact download failed with HTTP {status}: {body_text}")
        body_size = body_path.stat().st_size if body_path.exists() else 0
        if body_size <= 0:
            raise SmokeError(f"artifact download returned no bytes: {download_url}")
        content_length = headers.get("content-length")
        return int(content_length) if content_length and content_length.isdigit() else body_size


def verify_worker_cleanup(worker_target: str, worker_port: int, job_id: str, *, expect_retained_local_artifact: bool) -> dict[str, Any]:
    snapshot = worker_snapshot(worker_target, worker_port, job_id)
    unexpected_paths = list(snapshot["paths"])
    if expect_retained_local_artifact:
        unexpected_paths = [
            path for path in unexpected_paths if "/_retained_final_3dgs/" not in path
        ]
    if snapshot["processes"]:
        raise SmokeError(f"worker still has active processes for {job_id}: {snapshot['processes']}")
    if unexpected_paths:
        raise SmokeError(f"worker still has leftover paths for {job_id}: {unexpected_paths}")
    return snapshot


def create_sparse_smoke_video(size_bytes: int) -> Path:
    with tempfile.NamedTemporaryFile(prefix="aether_chunked_early_claim_", suffix=".mp4", delete=False) as handle:
        path = Path(handle.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.truncate(size_bytes)
    return path


def do_upload_only(args: argparse.Namespace) -> int:
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.exists():
        raise SmokeError(f"file does not exist: {file_path}")

    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    client_record_id = args.client_record_id or f"codex-upload-only-{int(time.time())}"
    response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
    job_id = str(response["jobId"])
    log(f"JOB_CREATED job_id={job_id} upload_kind={response['upload'].get('kind')}")
    upload_via_contract(args.base_url, job_id, response["upload"], file_path, content_type)
    status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
    log(f"UPLOAD_READY job_id={job_id} state={status.get('state')} assigned_worker_id={status.get('assigned_worker_id')}")
    print(job_id)
    return 0


def run_chunked_early_claim(args: argparse.Namespace) -> int:
    file_path: Path
    delete_after = False
    if args.file:
        file_path = Path(args.file).expanduser().resolve()
        if not file_path.exists():
            raise SmokeError(f"file does not exist: {file_path}")
    else:
        file_path = create_sparse_smoke_video(args.file_size_mib * 1024 * 1024)
        delete_after = True

    try:
        content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
        client_record_id = f"codex-chunked-early-claim-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
        job_id = str(response["jobId"])
        contract = response["upload"]
        log(f"JOB_CREATED job_id={job_id} upload_kind={contract.get('kind')} file={file_path}")
        if str(contract.get("kind") or "").strip().lower() != "chunked":
            raise SmokeError(f"expected chunked upload contract for {job_id}, got {contract.get('kind')!r}")

        part_size = int(contract.get("partSizeBytes") or contract.get("part_size_bytes") or 0)
        if part_size <= 0:
            raise SmokeError(f"invalid chunked part size for {job_id}: {part_size}")

        uploaded_parts = upload_via_contract(
            args.base_url,
            job_id,
            contract,
            file_path,
            content_type,
            limit_parts=args.visible_chunks,
            complete_upload=False,
        )
        visible_target_bytes = min(file_path.stat().st_size, len(uploaded_parts) * part_size)
        manifest = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}/chunk-manifest")
        manifest_visible_bytes = int(manifest.get("visible_bytes") or 0)
        if manifest_visible_bytes < visible_target_bytes:
            raise SmokeError(
                f"chunk manifest visible bytes too small for {job_id}: "
                f"got={manifest_visible_bytes} expected_at_least={visible_target_bytes}"
            )
        if manifest.get("upload_completed"):
            raise SmokeError(f"chunk manifest unexpectedly marked upload completed for {job_id}: {manifest}")

        deadline = time.time() + args.wait_claim_timeout_sec
        claimed_status: dict[str, Any] | None = None
        last_signature: tuple[Any, ...] | None = None
        while time.time() < deadline:
            status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}")
            signature = (
                status.get("state"),
                status.get("stage"),
                status.get("progress_basis"),
                status.get("detail"),
            )
            if signature != last_signature:
                log(
                    "RAW_STATUS "
                    f"job_id={job_id} state={status.get('state')} stage={status.get('stage')} "
                    f"progress_basis={status.get('progress_basis')} detail={status.get('detail')}"
                )
                last_signature = signature
            if (
                status.get("state") == "assigned"
                and status.get("progress_basis") == "worker_assigned_streaming_input"
            ):
                claimed_status = status
                break
            if status.get("state") in {"failed", "completed", "cancelled"}:
                raise SmokeError(f"job {job_id} reached unexpected terminal state before streaming claim: {status}")
            time.sleep(2.0)

        if claimed_status is None:
            raise SmokeError(f"worker never entered assigned/streaming_input before upload complete for {job_id}")

        delete_status = api_json("DELETE", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
        log(
            f"CANCEL_SENT job_id={job_id} state={delete_status.get('state')} "
            f"cancel_acknowledged={delete_status.get('cancel_acknowledged')}"
        )

        cancelled = poll_mobile_job(
            args.base_url,
            job_id,
            timeout_sec=args.wait_cancel_timeout_sec,
            poll_interval_sec=2.0,
            stop_when=lambda status: bool(status.get("cancel_acknowledged")),
        )
        if cancelled.get("state") != "cancelled" or not cancelled.get("cancel_acknowledged"):
            raise SmokeError(f"job {job_id} did not reach cancel acknowledgement after streaming claim: {cancelled}")

        worker_state = verify_worker_cleanup(
            args.worker_ssh_target,
            args.worker_ssh_port,
            job_id,
            expect_retained_local_artifact=False,
        )
        input_objects = list_spaces_prefix(
            args.control_plane_ssh_target,
            args.control_plane_ssh_port,
            f"uploads/tenant_demo/{job_id}/",
        )
        output_objects = list_spaces_prefix(
            args.control_plane_ssh_target,
            args.control_plane_ssh_port,
            f"outputs/{job_id}/",
        )
        if input_objects:
            raise SmokeError(f"chunked early-claim job still has input objects in Spaces: {input_objects}")
        if output_objects:
            raise SmokeError(f"chunked early-claim job still has output objects in Spaces: {output_objects}")

        log(
            f"PASS mode=chunked-early-claim job_id={job_id} "
            f"visible_bytes={manifest_visible_bytes} progress_basis={claimed_status.get('progress_basis')} "
            f"worker_state={worker_state.get('liveness', {}).get('state')}"
        )
        return 0
    finally:
        if delete_after:
            file_path.unlink(missing_ok=True)


def run_chunked_prewarm(args: argparse.Namespace) -> int:
    file_path = create_profile_clip(args, mode="chunked-prewarm")
    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    client_record_id = f"codex-chunked-prewarm-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
    job_id = str(response["jobId"])
    contract = response["upload"]
    log(f"JOB_CREATED job_id={job_id} upload_kind={contract.get('kind')} file={file_path}")
    if str(contract.get("kind") or "").strip().lower() != "chunked":
        raise SmokeError(f"expected chunked upload contract for {job_id}, got {contract.get('kind')!r}")

    total_parts = len(contract.get("parts") or [])
    if total_parts < 2:
        raise SmokeError(f"need at least 2 visible chunks to verify prewarm overlap, got total_parts={total_parts}")
    partial_limit = min(args.visible_chunks, total_parts - 1)
    if partial_limit <= 0:
        partial_limit = total_parts - 1

    upload_via_contract(
        args.base_url,
        job_id,
        contract,
        file_path,
        content_type,
        limit_parts=partial_limit,
        complete_upload=False,
    )

    deadline = time.time() + args.wait_prewarm_timeout_sec
    prewarm_status: dict[str, Any] | None = None
    last_signature: tuple[Any, ...] | None = None
    while time.time() < deadline:
        status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}")
        signature = (
            status.get("state"),
            status.get("stage"),
            status.get("phase_name"),
            status.get("progress_basis"),
            status.get("detail"),
        )
        if signature != last_signature:
            log(
                "RAW_STATUS "
                f"job_id={job_id} state={status.get('state')} stage={status.get('stage')} "
                f"phase_name={status.get('phase_name')} progress_basis={status.get('progress_basis')} "
                f"detail={status.get('detail')}"
            )
            last_signature = signature
        if status.get("phase_name") in {"stream_probe_live", "extract_frames_live", "audit_live"} or status.get("progress_basis") in {
            "prep_stream_probe_live",
            "prep_extract_frames_live",
            "prep_audit_live",
        }:
            prewarm_status = status
            break
        if status.get("state") in {"failed", "completed", "cancelled"}:
            raise SmokeError(f"job {job_id} reached unexpected terminal state before prewarm became visible: {status}")
        time.sleep(2.0)

    if prewarm_status is None:
        raise SmokeError(f"job {job_id} never exposed extract_frames_live/audit_live before upload complete")

    delete_status = api_json("DELETE", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
    log(
        f"CANCEL_SENT job_id={job_id} state={delete_status.get('state')} "
        f"cancel_acknowledged={delete_status.get('cancel_acknowledged')}"
    )

    cancelled = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_cancel_timeout_sec,
        poll_interval_sec=2.0,
        stop_when=lambda status: bool(status.get("cancel_acknowledged")),
    )
    if cancelled.get("state") != "cancelled" or not cancelled.get("cancel_acknowledged"):
        raise SmokeError(f"job {job_id} did not reach cancel acknowledgement after prewarm test: {cancelled}")

    worker_state = verify_worker_cleanup(
        args.worker_ssh_target,
        args.worker_ssh_port,
        job_id,
        expect_retained_local_artifact=False,
    )
    input_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"uploads/tenant_demo/{job_id}/",
    )
    artifact_prefix = artifact_key.rsplit("/", 1)[0] + "/"
    output_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        artifact_prefix,
    )
    if input_objects:
        raise SmokeError(f"chunked prewarm job still has input objects in Spaces: {input_objects}")
    if output_objects:
        raise SmokeError(f"chunked prewarm job still has output objects in Spaces: {output_objects}")

    log(
        f"PASS mode=chunked-prewarm job_id={job_id} "
        f"phase_name={prewarm_status.get('phase_name')} progress_basis={prewarm_status.get('progress_basis')} "
        f"worker_state={worker_state.get('liveness', {}).get('state')}"
    )
    return 0


def run_chunked_abort(args: argparse.Namespace) -> int:
    file_path = create_profile_clip(args, mode="chunked-prewarm")
    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    client_record_id = f"codex-chunked-abort-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
    job_id = str(response["jobId"])
    contract = response["upload"]
    log(f"JOB_CREATED job_id={job_id} upload_kind={contract.get('kind')} file={file_path}")
    if str(contract.get("kind") or "").strip().lower() != "chunked":
        raise SmokeError(f"expected chunked upload contract for {job_id}, got {contract.get('kind')!r}")

    total_parts = len(contract.get("parts") or [])
    if total_parts < 2:
        raise SmokeError(f"need at least 2 visible chunks to verify abort after prewarm, got total_parts={total_parts}")
    partial_limit = min(args.visible_chunks, total_parts - 1)
    if partial_limit <= 0:
        partial_limit = total_parts - 1

    upload_via_contract(
        args.base_url,
        job_id,
        contract,
        file_path,
        content_type,
        limit_parts=partial_limit,
        complete_upload=False,
    )

    deadline = time.time() + args.wait_preamble_timeout_sec
    prewarm_status: dict[str, Any] | None = None
    last_signature: tuple[Any, ...] | None = None
    while time.time() < deadline:
        status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}")
        signature = (
            status.get("state"),
            status.get("stage"),
            status.get("phase_name"),
            status.get("progress_basis"),
            status.get("detail"),
        )
        if signature != last_signature:
            log(
                "RAW_STATUS "
                f"job_id={job_id} state={status.get('state')} stage={status.get('stage')} "
                f"phase_name={status.get('phase_name')} progress_basis={status.get('progress_basis')} "
                f"detail={status.get('detail')}"
            )
            last_signature = signature
        if status.get("phase_name") in {"stream_probe_live", "extract_frames_live", "audit_live"} or status.get("progress_basis") in {
            "prep_stream_probe_live",
            "prep_extract_frames_live",
            "prep_audit_live",
        }:
            prewarm_status = status
            break
        if status.get("state") in {"failed", "completed", "cancelled"}:
            raise SmokeError(f"job {job_id} reached unexpected terminal state before abort test became visible: {status}")
        time.sleep(2.0)

    if prewarm_status is None:
        raise SmokeError(f"job {job_id} never exposed stream_probe_live/extract_frames_live/audit_live before abort")

    abort_url = str(contract.get("abortURL") or contract.get("abort_url") or "")
    if not abort_url:
        raise SmokeError(f"abort URL missing for {job_id}")
    api_json(
        "POST",
        abort_url,
        {
            "uploadId": contract.get("uploadId") or contract.get("upload_id"),
            "storageKey": contract.get("storageKey") or contract.get("storage_key"),
        },
    )
    log(f"ABORT_SENT job_id={job_id} via=upload_abort")

    cancelled = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_cancel_timeout_sec,
        poll_interval_sec=2.0,
        stop_when=lambda status: status.get("state") == "cancelled" and (
            bool(status.get("cancel_acknowledged")) or status.get("assigned_worker_id") is None
        ),
    )
    if cancelled.get("state") != "cancelled":
        raise SmokeError(f"job {job_id} did not enter cancelled after upload abort: {cancelled}")
    if cancelled.get("failure_reason") != "upload_failed":
        raise SmokeError(f"job {job_id} lost upload_failed reason after upload abort: {cancelled}")

    worker_state = verify_worker_cleanup(
        args.worker_ssh_target,
        args.worker_ssh_port,
        job_id,
        expect_retained_local_artifact=False,
    )
    input_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"uploads/tenant_demo/{job_id}/",
    )
    output_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"outputs/{job_id}/",
    )
    if input_objects:
        raise SmokeError(f"chunked abort job still has input objects in Spaces: {input_objects}")
    if output_objects:
        raise SmokeError(f"chunked abort job still has output objects in Spaces: {output_objects}")

    log(
        f"PASS mode=chunked-abort job_id={job_id} "
        f"phase_name={prewarm_status.get('phase_name')} failure_reason={cancelled.get('failure_reason')} "
        f"worker_state={worker_state.get('liveness', {}).get('state')}"
    )
    return 0


def run_chunked_live_sfm(args: argparse.Namespace) -> int:
    file_path = create_profile_clip(args, mode="chunked-prewarm")
    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    client_record_id = f"codex-chunked-live-sfm-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
    job_id = str(response["jobId"])
    contract = response["upload"]
    log(f"JOB_CREATED job_id={job_id} upload_kind={contract.get('kind')} file={file_path}")
    if str(contract.get("kind") or "").strip().lower() != "chunked":
        raise SmokeError(f"expected chunked upload contract for {job_id}, got {contract.get('kind')!r}")

    total_parts = len(contract.get("parts") or [])
    if total_parts < 2:
        raise SmokeError(f"need at least 2 visible chunks to verify live sfm before upload completion, got total_parts={total_parts}")
    partial_limit = min(args.visible_chunks, total_parts - 1)
    if partial_limit <= 0:
        partial_limit = total_parts - 1

    upload_via_contract(
        args.base_url,
        job_id,
        contract,
        file_path,
        content_type,
        limit_parts=partial_limit,
        complete_upload=False,
    )

    deadline = time.time() + args.wait_live_sfm_timeout_sec
    live_status: dict[str, Any] | None = None
    last_signature: tuple[Any, ...] | None = None
    while time.time() < deadline:
        status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}")
        signature = (
            status.get("state"),
            status.get("stage"),
            status.get("phase_name"),
            status.get("progress_basis"),
            status.get("detail"),
        )
        if signature != last_signature:
            log(
                "RAW_STATUS "
                f"job_id={job_id} state={status.get('state')} stage={status.get('stage')} "
                f"phase_name={status.get('phase_name')} progress_basis={status.get('progress_basis')} "
                f"detail={status.get('detail')}"
            )
            last_signature = signature
        if status.get("phase_name") in {"sfm_wait_live", "live_sfm_ready"} or status.get("progress_basis") in {
            "prep_live_sfm_wait_frames",
            "prep_live_sfm_ready",
            "prep_live_sfm_retry_wait",
        }:
            live_status = status
            break
        if status.get("state") in {"failed", "completed", "cancelled"}:
            raise SmokeError(f"job {job_id} reached unexpected terminal state before live sfm became visible: {status}")
        time.sleep(2.0)

    if live_status is None:
        raise SmokeError(f"job {job_id} never exposed live sfm status before upload completion")

    delete_status = api_json("DELETE", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
    log(
        f"CANCEL_SENT job_id={job_id} state={delete_status.get('state')} "
        f"cancel_acknowledged={delete_status.get('cancel_acknowledged')}"
    )

    cancelled = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_cancel_timeout_sec,
        poll_interval_sec=2.0,
        stop_when=lambda status: status.get("state") == "cancelled" and (
            bool(status.get("cancel_acknowledged")) or status.get("assigned_worker_id") is None
        ),
    )
    if cancelled.get("state") != "cancelled":
        raise SmokeError(f"job {job_id} did not enter cancelled after live sfm test: {cancelled}")

    worker_state = verify_worker_cleanup(
        args.worker_ssh_target,
        args.worker_ssh_port,
        job_id,
        expect_retained_local_artifact=False,
    )
    input_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"uploads/tenant_demo/{job_id}/",
    )
    output_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"outputs/{job_id}/",
    )
    if input_objects:
        raise SmokeError(f"chunked live sfm job still has input objects in Spaces: {input_objects}")
    if output_objects:
        raise SmokeError(f"chunked live sfm job still has output objects in Spaces: {output_objects}")

    log(
        f"PASS mode=chunked-live-sfm job_id={job_id} "
        f"phase_name={live_status.get('phase_name')} progress_basis={live_status.get('progress_basis')} "
        f"worker_state={worker_state.get('liveness', {}).get('state')}"
    )
    return 0


def run_chunked_train_seed(args: argparse.Namespace) -> int:
    file_path = create_profile_clip(args, mode="chunked-prewarm")
    client_record_id = f"codex-chunked-train-seed-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    response = create_mobile_job(
        args.base_url,
        file_path,
        content_type,
        client_record_id,
    )
    job_id = str(response["jobId"])
    contract = response["upload"]
    log(f"JOB_CREATED job_id={job_id} upload_kind={contract.get('kind')} file={file_path}")
    if str(contract.get("kind") or "").strip().lower() != "chunked":
        raise SmokeError(f"expected chunked upload contract for {job_id}, got {contract.get('kind')!r}")

    total_parts = len(contract.get("parts") or [])
    if total_parts < 2:
        raise SmokeError(f"need at least 2 visible chunks to verify train seed before upload completion, got total_parts={total_parts}")
    partial_limit = min(args.visible_chunks, total_parts - 1)
    if partial_limit <= 0:
        partial_limit = total_parts - 1

    upload_via_contract(
        args.base_url,
        job_id,
        contract,
        file_path,
        content_type,
        limit_parts=partial_limit,
        complete_upload=False,
    )

    deadline = time.time() + args.wait_train_seed_timeout_sec
    seed_status: dict[str, Any] | None = None
    last_signature: tuple[Any, ...] | None = None
    while time.time() < deadline:
        status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}")
        signature = (
            status.get("state"),
            status.get("stage"),
            status.get("phase_name"),
            status.get("progress_basis"),
            status.get("detail"),
        )
        if signature != last_signature:
            log(
                "RAW_STATUS "
                f"job_id={job_id} state={status.get('state')} stage={status.get('stage')} "
                f"phase_name={status.get('phase_name')} progress_basis={status.get('progress_basis')} "
                f"detail={status.get('detail')}"
            )
            last_signature = signature
        if (
            str(status.get("stage") or "").strip().lower() == "train"
            and str(status.get("progress_basis") or "").strip().lower() == "runtime_tqdm_steps"
        ) or status.get("phase_name") == "seed_handoff":
            seed_status = status
            break
        if status.get("state") in {"failed", "completed", "cancelled"}:
            raise SmokeError(f"job {job_id} reached unexpected terminal state before seed train became visible: {status}")
        time.sleep(2.0)

    if seed_status is None:
        raise SmokeError(f"job {job_id} never exposed seed train status before upload completion")

    delete_status = api_json("DELETE", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
    log(
        f"CANCEL_SENT job_id={job_id} state={delete_status.get('state')} "
        f"cancel_acknowledged={delete_status.get('cancel_acknowledged')}"
    )

    cancelled = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_cancel_timeout_sec,
        poll_interval_sec=2.0,
        stop_when=lambda status: status.get("state") == "cancelled" and (
            bool(status.get("cancel_acknowledged")) or status.get("assigned_worker_id") is None
        ),
    )
    if cancelled.get("state") != "cancelled":
        raise SmokeError(f"job {job_id} did not enter cancelled after train seed test: {cancelled}")

    worker_state = verify_worker_cleanup(
        args.worker_ssh_target,
        args.worker_ssh_port,
        job_id,
        expect_retained_local_artifact=False,
    )
    input_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"uploads/tenant_demo/{job_id}/",
    )
    output_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"outputs/{job_id}/",
    )
    if input_objects:
        raise SmokeError(f"chunked train seed job still has input objects in Spaces: {input_objects}")
    if output_objects:
        raise SmokeError(f"chunked train seed job still has output objects in Spaces: {output_objects}")

    log(
        f"PASS mode=chunked-train-seed job_id={job_id} "
        f"phase_name={seed_status.get('phase_name')} progress_basis={seed_status.get('progress_basis')} "
        f"worker_state={worker_state.get('liveness', {}).get('state')}"
    )
    return 0


def create_profile_clip(args: argparse.Namespace, *, mode: str) -> Path:
    source_video = Path(args.source_video).expanduser().resolve()
    if not source_video.exists():
        raise SmokeError(f"source video does not exist: {source_video}")

    if args.file:
        file_path = Path(args.file).expanduser().resolve()
        if not file_path.exists():
            raise SmokeError(f"file does not exist: {file_path}")
        return file_path

    if mode == "cancel-cleanup":
        clip_path = Path(args.clip_path or "/tmp/aether_cancel_cleanup_smoke.mp4")
        return ensure_clip(
            source_video=source_video,
            clip_path=clip_path,
            start_sec=args.clip_start_sec or 0,
            duration_sec=args.clip_seconds or 8,
            width_px=args.clip_width or 960,
            crf=args.clip_crf or 26,
        )

    if mode == "chunked-prewarm":
        clip_path = Path(args.clip_path or "/tmp/aether_chunked_prewarm_smoke.mp4")
        return ensure_clip(
            source_video=source_video,
            clip_path=clip_path,
            start_sec=args.clip_start_sec or 20,
            duration_sec=args.clip_seconds or 12,
            width_px=args.clip_width or 1280,
            crf=args.clip_crf or 24,
        )

    clip_path = Path(args.clip_path or "/tmp/aether_success_retention_smoke.mp4")
    return ensure_clip(
        source_video=source_video,
        clip_path=clip_path,
        start_sec=args.clip_start_sec or 0,
        duration_sec=args.clip_seconds or 15,
        width_px=args.clip_width or 1280,
        crf=args.clip_crf or 24,
    )


def run_cancel_cleanup(args: argparse.Namespace) -> int:
    file_path = create_profile_clip(args, mode="cancel-cleanup")
    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    client_record_id = f"codex-cancel-cleanup-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
    job_id = str(response["jobId"])
    log(f"JOB_CREATED job_id={job_id} upload_kind={response['upload'].get('kind')} file={file_path}")
    upload_via_contract(args.base_url, job_id, response["upload"], file_path, content_type)
    log(f"UPLOAD_DONE job_id={job_id}")

    active = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_active_timeout_sec,
        poll_interval_sec=2.0,
        stop_when=lambda status: status.get("state") in {"reconstructing", "training_probe", "training_full", "exporting"},
    )
    if active.get("state") == "completed":
        raise SmokeError(f"job {job_id} completed before cancel could be tested")

    delete_status = api_json("DELETE", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
    log(
        f"CANCEL_SENT job_id={job_id} state={delete_status.get('state')} "
        f"cancel_acknowledged={delete_status.get('cancel_acknowledged')}"
    )

    cancelled = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_cancel_timeout_sec,
        poll_interval_sec=2.0,
        stop_when=lambda status: bool(status.get("cancel_acknowledged")),
    )
    if cancelled.get("state") != "cancelled" or not cancelled.get("cancel_acknowledged"):
        raise SmokeError(f"job {job_id} did not reach cancel acknowledgement: {cancelled}")

    worker_state = verify_worker_cleanup(
        args.worker_ssh_target,
        args.worker_ssh_port,
        job_id,
        expect_retained_local_artifact=False,
    )
    input_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"uploads/tenant_demo/{job_id}/",
    )
    output_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"outputs/{job_id}/",
    )
    if input_objects:
        raise SmokeError(f"cancelled job still has input objects in Spaces: {input_objects}")
    if output_objects:
        raise SmokeError(f"cancelled job still has output objects in Spaces: {output_objects}")

    log(
        f"PASS mode=cancel-cleanup job_id={job_id} worker_state={worker_state.get('liveness', {}).get('state')} "
        f"cancel_acknowledged={cancelled.get('cancel_acknowledged')}"
    )
    return 0


def run_success_retention(args: argparse.Namespace) -> int:
    file_path = create_profile_clip(args, mode="success-retention")
    content_type = args.content_type or mimetypes.guess_type(file_path.name)[0] or "video/mp4"
    client_record_id = f"codex-success-retention-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    response = create_mobile_job(args.base_url, file_path, content_type, client_record_id)
    job_id = str(response["jobId"])
    log(f"JOB_CREATED job_id={job_id} upload_kind={response['upload'].get('kind')} file={file_path}")
    upload_via_contract(args.base_url, job_id, response["upload"], file_path, content_type)
    log(f"UPLOAD_DONE job_id={job_id}")

    completed = poll_mobile_job(
        args.base_url,
        job_id,
        timeout_sec=args.wait_success_timeout_sec,
        poll_interval_sec=5.0,
        stop_when=lambda status: status.get("state") in {"completed", "failed", "cancelled"},
    )
    if completed.get("state") != "completed":
        raise SmokeError(f"job {job_id} did not complete successfully: {completed}")

    api_status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/jobs/{job_id}")
    artifact = (api_status.get("artifact") or {}).get("primary_artifact") or {}
    artifact_key = str(artifact.get("storage_key") or "")
    if not artifact_key:
        raise SmokeError(f"completed job {job_id} is missing primary artifact manifest: {api_status}")

    mobile_status = api_json("GET", f"{args.base_url.rstrip('/')}/v1/mobile-jobs/{job_id}")
    mobile_artifact = mobile_status.get("artifact") or {}
    download_url = str(mobile_artifact.get("download_url") or "")
    if not download_url:
        raise SmokeError(f"mobile artifact payload missing download URL for completed job {job_id}: {mobile_status}")
    downloaded_size = verify_artifact_download(download_url)

    worker_state = verify_worker_cleanup(
        args.worker_ssh_target,
        args.worker_ssh_port,
        job_id,
        expect_retained_local_artifact=args.expect_retained_local_artifact,
    )
    input_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"uploads/tenant_demo/{job_id}/",
    )
    output_objects = list_spaces_prefix(
        args.control_plane_ssh_target,
        args.control_plane_ssh_port,
        f"outputs/{job_id}/",
    )

    if input_objects:
        raise SmokeError(f"completed job still has input objects in Spaces: {input_objects}")
    if not output_objects:
        raise SmokeError(f"completed job has no output objects in Spaces under {artifact_prefix}: {job_id}")
    if artifact_key not in output_objects:
        raise SmokeError(f"primary artifact key {artifact_key} not found in output objects: {output_objects}")
    if len(output_objects) != 1:
        raise SmokeError(f"completed job retained unexpected extra output objects: {output_objects}")

    log(
        f"PASS mode=success-retention job_id={job_id} artifact_key={artifact_key} "
        f"artifact_probe_bytes={downloaded_size} worker_state={worker_state.get('liveness', {}).get('state')}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aether3D public API end-to-end regression runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_options(target: argparse.ArgumentParser) -> None:
        target.add_argument("--base-url", default=DEFAULT_BASE_URL)
        target.add_argument("--source-video", default=DEFAULT_SOURCE_VIDEO)
        target.add_argument("--file")
        target.add_argument("--clip-path")
        target.add_argument("--clip-start-sec", type=int)
        target.add_argument("--clip-seconds", type=int)
        target.add_argument("--clip-width", type=int)
        target.add_argument("--clip-crf", type=int)
        target.add_argument("--content-type")
        target.add_argument("--control-plane-ssh-target", default=DEFAULT_CONTROL_PLANE_SSH_TARGET)
        target.add_argument("--control-plane-ssh-port", type=int, default=DEFAULT_CONTROL_PLANE_SSH_PORT)
        target.add_argument("--worker-ssh-target", default=DEFAULT_WORKER_SSH_TARGET)
        target.add_argument("--worker-ssh-port", type=int, default=DEFAULT_WORKER_SSH_PORT)

    upload_only = subparsers.add_parser("upload-only", help="Create a public mobile job and upload the file")
    upload_only.add_argument("--base-url", default=DEFAULT_BASE_URL)
    upload_only.add_argument("--file", required=True)
    upload_only.add_argument("--content-type")
    upload_only.add_argument("--client-record-id")

    chunked_early_claim = subparsers.add_parser(
        "chunked-early-claim",
        help="Verify chunk-visible early claim before upload completion, then cancel and clean up",
    )
    add_common_options(chunked_early_claim)
    chunked_early_claim.add_argument("--file-size-mib", type=int, default=40)
    chunked_early_claim.add_argument("--visible-chunks", type=int, default=4)
    chunked_early_claim.add_argument("--wait-claim-timeout-sec", type=int, default=90)
    chunked_early_claim.add_argument("--wait-cancel-timeout-sec", type=int, default=120)

    chunked_prewarm = subparsers.add_parser(
        "chunked-prewarm",
        help="Verify upload+preprocessing overlap by exposing extract_frames_live or audit_live before upload completion",
    )
    add_common_options(chunked_prewarm)
    chunked_prewarm.add_argument("--visible-chunks", type=int, default=2)
    chunked_prewarm.add_argument("--wait-prewarm-timeout-sec", type=int, default=120)
    chunked_prewarm.add_argument("--wait-cancel-timeout-sec", type=int, default=120)

    chunked_abort = subparsers.add_parser(
        "chunked-abort",
        help="Verify upload-abort marks the job terminal, stops any claimed worker, and cleans storage",
    )
    add_common_options(chunked_abort)
    chunked_abort.add_argument("--visible-chunks", type=int, default=2)
    chunked_abort.add_argument("--wait-preamble-timeout-sec", type=int, default=120)
    chunked_abort.add_argument("--wait-cancel-timeout-sec", type=int, default=120)

    chunked_live_sfm = subparsers.add_parser(
        "chunked-live-sfm",
        help="Verify upload+incremental SfM overlap by exposing sfm_wait_live or live_sfm_ready before upload completion",
    )
    add_common_options(chunked_live_sfm)
    chunked_live_sfm.add_argument("--visible-chunks", type=int, default=6)
    chunked_live_sfm.add_argument("--wait-live-sfm-timeout-sec", type=int, default=180)
    chunked_live_sfm.add_argument("--wait-cancel-timeout-sec", type=int, default=120)

    chunked_train_seed = subparsers.add_parser(
        "chunked-train-seed",
        help="Verify upload+train overlap by exposing train seed runtime before upload completion",
    )
    add_common_options(chunked_train_seed)
    chunked_train_seed.add_argument("--visible-chunks", type=int, default=8)
    chunked_train_seed.add_argument("--wait-train-seed-timeout-sec", type=int, default=240)
    chunked_train_seed.add_argument("--wait-cancel-timeout-sec", type=int, default=120)

    cancel_cleanup = subparsers.add_parser("cancel-cleanup", help="Verify cancel, remote stop, and storage cleanup")
    add_common_options(cancel_cleanup)
    cancel_cleanup.add_argument("--wait-active-timeout-sec", type=int, default=180)
    cancel_cleanup.add_argument("--wait-cancel-timeout-sec", type=int, default=120)

    success_retention = subparsers.add_parser("success-retention", help="Verify successful completion and retained output contract")
    add_common_options(success_retention)
    success_retention.add_argument("--wait-success-timeout-sec", type=int, default=1800)
    success_retention.add_argument("--expect-retained-local-artifact", action="store_true")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "upload-only":
        return do_upload_only(args)
    if args.command == "chunked-early-claim":
        return run_chunked_early_claim(args)
    if args.command == "chunked-prewarm":
        return run_chunked_prewarm(args)
    if args.command == "chunked-abort":
        return run_chunked_abort(args)
    if args.command == "chunked-live-sfm":
        return run_chunked_live_sfm(args)
    if args.command == "chunked-train-seed":
        return run_chunked_train_seed(args)
    if args.command == "cancel-cleanup":
        return run_cancel_cleanup(args)
    if args.command == "success-retention":
        return run_success_retention(args)
    raise SmokeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeError as error:
        log(f"FAIL {error}")
        raise SystemExit(1)
