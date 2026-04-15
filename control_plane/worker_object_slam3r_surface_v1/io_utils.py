from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping


def write_text_atomic(path: Path, text: str, *, encoding: str = "utf-8", fsync: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding=encoding) as handle:
        handle.write(text)
        handle.flush()
        if fsync:
            os.fsync(handle.fileno())
    tmp_path.replace(path)
    if not fsync:
        return
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def write_json_atomic(
    path: Path,
    payload: Mapping[str, Any],
    *,
    ensure_ascii: bool = False,
    indent: int | None = None,
    sort_keys: bool = False,
    fsync: bool = True,
) -> None:
    write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent, sort_keys=sort_keys),
        fsync=fsync,
    )
