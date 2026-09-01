"""Conversions API validation and privacy-preserving identifier normalization."""

from __future__ import annotations

import hashlib
import re
import string
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .errors import PolicyError, ValidationError

STANDARD_EVENTS = {
    "appointment_scheduled",
    "checkout_started",
    "contents_viewed",
    "items_added",
    "lead_created",
    "order_created",
    "page_viewed",
    "registration_completed",
    "subscription_created",
    "trial_started",
    "app_installed",
    "app_opened",
}
ACTION_SOURCES = {"web", "mobile_app", "offline", "physical_store", "phone_call", "email", "other"}
EVENT_DATA_TYPES = {
    "appointment_scheduled": "customer_action",
    "lead_created": "customer_action",
    "registration_completed": "customer_action",
    "app_installed": "customer_action",
    "app_opened": "customer_action",
    "checkout_started": "contents",
    "contents_viewed": "contents",
    "items_added": "contents",
    "order_created": "contents",
    "page_viewed": "contents",
    "subscription_created": "plan_enrollment",
    "trial_started": "plan_enrollment",
    "custom": "custom",
}
HASH_FIELDS = {
    "emails_sha256",
    "phone_numbers_sha256",
    "external_ids_sha256",
    "first_names_sha256",
    "last_names_sha256",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CUSTOM_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")
INTEGRATION_SOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_identifier(kind: str, value: str) -> str:
    if kind == "email":
        normalized = value.strip().lower()
    elif kind == "phone":
        normalized = re.sub(r"[\s().-]", "", value.strip())
        normalized = normalized.removeprefix("+").lstrip("0")
        if not normalized.isdigit() or not 8 <= len(normalized) <= 15:
            raise ValidationError("Phone must normalize to 8–15 digits including country code")
    elif kind == "external_id":
        normalized = value.strip()
    elif kind in {"first_name", "last_name"}:
        removable = set(string.whitespace + string.punctuation)
        normalized = "".join(char for char in value.lower() if char not in removable)
    else:
        raise ValidationError(f"Unsupported identifier type: {kind}")
    if not normalized:
        raise ValidationError(f"{kind} is empty after normalization")
    return normalized


def hash_identifier(kind: str, value: str) -> str:
    return sha256(normalize_identifier(kind, value))


def sanitize_source_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("source_url must use http or https and include a host")
    host = parsed.hostname.lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    # Fragments and query strings commonly contain identifiers; never forward them.
    return urlunsplit((parsed.scheme.lower(), host, parsed.path or "/", "", ""))


def validate_batch(payload: Any, *, now_ms: int | None = None, consent_confirmed: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationError("CAPI body must be a JSON object")
    events = payload.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 1000:
        raise ValidationError("events must contain 1–1000 items")
    integration = payload.get("integration_source")
    if integration is not None and not INTEGRATION_SOURCE.fullmatch(str(integration).strip()):
        raise ValidationError("integration_source must be 1–64 allowed ASCII characters")
    if not consent_confirmed:
        raise PolicyError("CAPI sending requires explicit confirmation that applicable consent and privacy requirements are satisfied")
    current = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    earliest = current - (7 * 24 * 60 * 60 * 1000)
    latest = current + (10 * 60 * 1000)
    sanitized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            raise ValidationError(f"events[{index}] must be an object")
        event = dict(raw)
        event_id = event.get("id")
        event_type = event.get("type")
        timestamp = event.get("timestamp_ms")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValidationError(f"events[{index}].id is required for retries and Pixel+CAPI dedupe")
        if event_id in seen_ids:
            raise ValidationError(f"Duplicate event id in batch: {event_id}")
        seen_ids.add(event_id)
        if event_type != "custom" and event_type not in STANDARD_EVENTS:
            raise ValidationError(f"events[{index}].type is not documented")
        if event_type == "custom":
            name = event.get("custom_event_name")
            if not isinstance(name, str) or not CUSTOM_NAME.fullmatch(name) or name.lower() in STANDARD_EVENTS:
                raise ValidationError(f"events[{index}].custom_event_name is invalid")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not earliest <= timestamp <= latest:
            raise ValidationError(f"events[{index}].timestamp_ms must be within 7 days past and 10 minutes future")
        source = event.get("action_source")
        if source not in ACTION_SOURCES:
            raise ValidationError(f"events[{index}].action_source is required and must be documented")
        if event_type in {"app_installed", "app_opened"} and source != "mobile_app":
            raise ValidationError(f"events[{index}] app lifecycle events require action_source=mobile_app")
        if source == "web":
            if not event.get("source_url"):
                raise ValidationError(f"events[{index}].source_url is required for web events")
            event["source_url"] = sanitize_source_url(str(event["source_url"]))
        if not isinstance(event.get("data"), dict) or not event["data"].get("type"):
            raise ValidationError(f"events[{index}].data.type is required")
        data = event["data"]
        if data.get("type") != EVENT_DATA_TYPES[event_type]:
            raise ValidationError(f"events[{index}].data.type must be {EVENT_DATA_TYPES[event_type]} for {event_type}")
        if "amount" in data:
            if isinstance(data["amount"], bool) or not isinstance(data["amount"], int):
                raise ValidationError(f"events[{index}].data.amount must be an integer minor-unit value")
            currency = data.get("currency")
            if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
                raise ValidationError(f"events[{index}].data.currency is required with amount and must be ISO-style")
        if "currency" in data and "amount" not in data:
            raise ValidationError(f"events[{index}].data.currency requires amount")
        user = event.get("user")
        if user is not None:
            if not isinstance(user, dict):
                raise ValidationError(f"events[{index}].user must be an object")
            forbidden_raw = {"email", "phone", "external_id", "first_name", "last_name"}.intersection(user)
            if forbidden_raw:
                raise PolicyError(f"Raw identifiers are forbidden; hash before sending: {sorted(forbidden_raw)}")
            for field in HASH_FIELDS:
                values = user.get(field)
                if values is None:
                    continue
                if not isinstance(values, list) or any(not isinstance(v, str) or not HEX64.fullmatch(v) for v in values):
                    raise ValidationError(f"events[{index}].user.{field} must contain lowercase SHA-256 hashes")
        sanitized.append(event)
    result = dict(payload)
    result["events"] = sanitized
    return result


def dedupe_key(pixel_id: str, event: dict[str, Any]) -> tuple[str, str, str]:
    name = str(event.get("custom_event_name")) if event.get("type") == "custom" else str(event.get("type"))
    return pixel_id, name, str(event.get("id"))
