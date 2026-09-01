"""Account-local reporting windows and exact derived KPI formulas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ValidationError


@dataclass(frozen=True)
class Window:
    days: int
    start: date
    end: date
    previous_start: date
    previous_end: date


def completed_window(days: int, timezone_name: str, now: datetime | None = None) -> Window:
    if days < 1:
        raise ValidationError("Report window must be at least one day")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(f"Unknown account timezone: {timezone_name}") from exc
    local_today = (now.astimezone(zone) if now else datetime.now(zone)).date()
    end = local_today - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return Window(days, start, end, previous_start, previous_end)


def _decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)


def _sum(rows: Iterable[dict[str, Any]], key: str) -> Decimal:
    return sum((_decimal(row.get(key)) for row in rows), Decimal(0))


def _ratio(numerator: Decimal, denominator: Decimal, multiplier: Decimal = Decimal(1)) -> float | None:
    if denominator == 0:
        return None
    return float((numerator / denominator) * multiplier)


def summarize(
    delivery_rows: list[dict[str, Any]],
    conversion_rows: list[dict[str, Any]],
    *,
    conversions_available: bool = True,
) -> dict[str, Any]:
    impressions = _sum(delivery_rows, "impressions")
    clicks = _sum(delivery_rows, "clicks")
    spend = _sum(delivery_rows, "spend")
    click_conversions = _sum(conversion_rows, "conversions")
    view_conversions = _sum(conversion_rows, "view_through_conversions")
    return {
        "impressions": int(impressions),
        "clicks": int(clicks),
        "spend": float(spend),
        "ctr": _ratio(clicks, impressions),
        "cpc": _ratio(spend, clicks),
        "cpm": _ratio(spend, impressions, Decimal(1000)),
        "click_through_conversions": float(click_conversions) if conversions_available else None,
        "cvr": _ratio(click_conversions, clicks) if conversions_available else None,
        "cpa": _ratio(spend, click_conversions) if conversions_available else None,
        "view_through_conversions": float(view_conversions) if conversions_available else None,
        "attribution_note": "View-through conversions are reported separately and excluded from CVR and CPA.",
    }


def compare(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in current.items():
        if key == "attribution_note" or not isinstance(value, (int, float)):
            continue
        old = previous.get(key)
        if not isinstance(old, (int, float)) or old == 0:
            result[key] = None
        else:
            result[key] = (value - old) / abs(old)
    return result


def diagnostics(summary: dict[str, Any], *, has_target: bool) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if summary["impressions"] == 0:
        findings.append({
            "finding": "No delivery in the completed window",
            "evidence": {"impressions": 0},
            "confidence": "high",
            "risk": "Campaign hierarchy, review state, schedule, or eligibility may block delivery",
            "reversible": True,
            "measurement_window": "Recheck after the serving blocker is resolved",
        })
    if summary["clicks"] and summary["click_through_conversions"] == 0:
        findings.append({
            "finding": "Clicks are present but no click-through conversions were recorded",
            "evidence": {"clicks": summary["clicks"], "click_through_conversions": 0},
            "confidence": "medium",
            "risk": "Tracking, offer, landing page, or post-click flow may be responsible",
            "reversible": True,
            "measurement_window": "Validate tracking first, then observe one full conversion cycle",
        })
    if not has_target:
        findings.append({
            "finding": "Spend recommendations withheld",
            "evidence": "No target CPA, CPC, CTR, or business goal supplied",
            "confidence": "high",
            "risk": "Changing spend without an objective can optimize the wrong outcome",
            "reversible": True,
            "measurement_window": "Provide a target before proposing a budget action",
        })
    return findings
