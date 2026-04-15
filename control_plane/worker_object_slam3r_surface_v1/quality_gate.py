from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import config
from .context import JobContext
from .io_utils import write_json_atomic


_REQUIRED_HQ_CARDS = (
    "geometry_hq",
    "texture_hq",
    "open_surface_hq",
    "sheetness_hq",
    "hole_fill_hq",
    "mesh_fidelity_hq",
)


def quality_report_path(ctx: JobContext) -> Path:
    return ctx.output_dir / config.quality_report_filename


def load_quality_report(ctx: JobContext) -> dict[str, Any]:
    path = quality_report_path(ctx)
    if not path.exists():
        return _empty_quality_report()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_quality_report()


def update_quality_card(
    ctx: JobContext,
    *,
    card_id: str,
    title: str,
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    failed_metrics: list[str],
    notes: list[str] | None = None,
) -> dict[str, Any]:
    report = load_quality_report(ctx)
    cards = report.setdefault("cards", {})
    cards[card_id] = {
        "title": title,
        "status": "pass" if not failed_metrics else "fail",
        "metrics": _json_safe(metrics),
        "thresholds": _json_safe(thresholds),
        "failed_metrics": list(failed_metrics),
        "notes": list(notes or []),
        "updated_at": _utc_now(),
    }
    return finalize_quality_report(ctx, report=report)


def finalize_quality_report(ctx: JobContext, *, report: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = report if report is not None else load_quality_report(ctx)
    payload["version"] = "hq_gate_v1"
    payload["mode"] = "hq_only"
    payload["required_cards"] = list(_REQUIRED_HQ_CARDS)

    cards = payload.get("cards") or {}
    failed_cards = [
        card_id
        for card_id in _REQUIRED_HQ_CARDS
        if (cards.get(card_id) or {}).get("status") != "pass"
    ]
    payload["publish_allowed"] = len(failed_cards) == 0
    payload["failed_cards"] = failed_cards
    payload["updated_at"] = _utc_now()

    path = quality_report_path(ctx)
    write_json_atomic(path, _json_safe(payload), ensure_ascii=False, indent=2)
    return payload


def assert_hq_publishable(ctx: JobContext) -> dict[str, Any]:
    report = finalize_quality_report(ctx)
    if report.get("publish_allowed"):
        return report
    failed_cards = report.get("failed_cards") or []
    raise RuntimeError(f"hq_gate_failed:{','.join(str(card) for card in failed_cards)}")


def hq_gate_failure_reason(report: dict[str, Any]) -> str:
    failed_cards = [str(card) for card in (report.get("failed_cards") or [])]
    return f"hq_gate_failed:{','.join(failed_cards)}"


def _empty_quality_report() -> dict[str, Any]:
    return {
        "version": "hq_gate_v1",
        "mode": "hq_only",
        "required_cards": list(_REQUIRED_HQ_CARDS),
        "cards": {},
        "publish_allowed": False,
        "failed_cards": list(_REQUIRED_HQ_CARDS),
        "updated_at": _utc_now(),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value
