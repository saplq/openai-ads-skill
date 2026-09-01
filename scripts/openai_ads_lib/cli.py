"""Command-line interface for OpenAI Ads Manager."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import stat
import urllib.error
import urllib.request
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import VERSION
from .client import CAPI_BASE_URL, AdsClient, capability_enabled
from .conversions import validate_batch
from .errors import AdsManagerError, AuthError, PolicyError, ValidationError
from .reporting import compare, completed_window, diagnostics, summarize
from .security import (
    atomic_write,
    audit_event,
    audit_preflight,
    body_hash,
    confirmation_hash,
    config_dir,
    fingerprint,
    get_profile,
    load_credentials,
    redact,
    safe_json_file,
    save_credentials,
    secure_read,
)
from .surfaces import authorize_surface, classify_path, is_read, normalize_path, validate_mutation

ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY_PATH = ROOT / "references" / "compatibility.json"


def envelope(
    *,
    ok: bool,
    profile: str | None = None,
    account_id: str | None = None,
    data: Any = None,
    warnings: list[str] | None = None,
    pagination: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "profile": profile,
        "account_id": account_id,
        "data": redact(data),
        "warnings": warnings or [],
        "pagination": pagination or {"pages": 0, "exhausted": True},
        "request": redact(request or {}),
    }


def print_json(payload: Any) -> None:
    print(json.dumps(redact(payload), ensure_ascii=False, indent=2, sort_keys=True))


def compatibility() -> dict[str, Any]:
    return json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))


def _account_data(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _account_id(payload: Any) -> str | None:
    account = _account_data(payload)
    return str(account.get("id") or account.get("ad_account_id") or "") or None


def _profile_client(profile_name: str) -> tuple[dict[str, Any], AdsClient]:
    profile = get_profile(profile_name)
    return profile, AdsClient(profile["api_key"])


def _with_context(error: AdsManagerError, account_id: str | None, warnings: list[str]) -> AdsManagerError:
    error.account_id = account_id
    error.warnings = list(warnings)
    return error


def _stored_account_id(profile_name: str | None) -> str | None:
    if not profile_name:
        return None
    try:
        stored = load_credentials().get("profiles", {}).get(profile_name, {})
        return str(stored.get("account_id") or "") or None
    except AdsManagerError:
        return None


def command_version(args: argparse.Namespace) -> dict[str, Any]:
    spec = compatibility()
    return envelope(ok=True, data={
        "skill": VERSION,
        "ads_api": spec["ads_api"]["major"],
        "openapi": spec["openapi"]["document_version"],
        "docs_changelog": spec["human_docs"]["api_overview_changelog"],
        "policy": spec["policy"]["version"],
        "features": spec["features"],
    })


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": f"openai-ads-manager/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise AdsManagerError(f"Doctor could not fetch valid JSON: {type(exc).__name__}") from exc


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"Accept": "text/markdown,text/html", "User-Agent": f"openai-ads-manager/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        raise AdsManagerError(f"Doctor could not fetch documentation: {type(exc).__name__}") from exc


def _changelog_marker(iso_date: str) -> str:
    value = datetime.strptime(iso_date, "%Y-%m-%d")
    day = value.day
    suffix = "th" if 10 < day % 100 < 14 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{value.strftime('%B')} {day}{suffix}, {value.year}"


def command_doctor(args: argparse.Namespace) -> dict[str, Any]:
    if args.offline and args.check_updates:
        raise ValidationError("Use either --offline or --check-updates, not both")
    spec = compatibility()
    profiles = load_credentials().get("profiles", {})
    profile_results: dict[str, Any] = {}
    warnings: list[str] = []
    for name, stored in profiles.items():
        status: dict[str, Any] = {"authenticated": True, "account_id": stored.get("account_id"), "key_fingerprint": stored.get("key_fingerprint")}
        if not args.offline:
            try:
                response = AdsClient(stored["api_key"]).request("GET", "/ad_account")
                live = _account_data(response.data)
                status.update({
                    "live": True,
                    "account_id": _account_id(response.data),
                    "credential_permissions": live.get("permissions") or live.get("capabilities") or "ad_account read verified",
                })
            except AdsManagerError as exc:
                status.update({"live": False, "error_category": exc.category, "message": exc.message})
        profile_results[name] = status
    drift: dict[str, Any] = {"checked": False}
    if args.check_updates:
        live_spec = _fetch_json(spec["openapi"]["url"])
        docs_text = _fetch_text(spec["human_docs"]["url"] + ".md")
        policy_text = _fetch_text(spec["policy"]["url"])
        live_paths = set(live_spec.get("paths", {}))
        operations = sorted(
            f"{method.upper()} {path}"
            for path, path_item in live_spec.get("paths", {}).items()
            if isinstance(path_item, dict)
            for method in path_item
            if method.lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
        )
        operation_hash = hashlib.sha256(("\n".join(operations) + "\n").encode("utf-8")).hexdigest()
        unknown_paths = sorted(path for path in live_paths if classify_path(path) == "unknown")
        docs_marker = _changelog_marker(spec["human_docs"]["api_overview_changelog"]) in docs_text
        pinned_policy_marker = f"v{spec['policy']['version']}"
        policy_marker = pinned_policy_marker in policy_text.lower()
        policy_version_live = spec["policy"]["version"] if policy_marker else None
        drift = {
            "checked": True,
            "openapi_document_version": live_spec.get("info", {}).get("version"),
            "version_changed": live_spec.get("info", {}).get("version") != spec["openapi"]["document_version"],
            "operation_surface_sha256": operation_hash,
            "operation_surface_changed": operation_hash != spec["openapi"]["operation_surface_sha256"],
            "unclassified_paths": unknown_paths,
            "path_count": len(live_paths),
            "docs_changelog_marker_present": docs_marker,
            "policy_version_detected": policy_version_live,
            "policy_pin_present": policy_marker,
        }
        if drift["version_changed"] or drift["operation_surface_changed"] or unknown_paths or not docs_marker or not policy_marker:
            warnings.append("Ads documentation or policy drift detected; review official sources before mutations")
    return envelope(ok=True, data={
        "versions": command_version(args)["data"],
        "config_dir": str(config_dir()),
        "profiles": profile_results,
        "drift": drift,
    }, warnings=warnings)


def command_auth(args: argparse.Namespace) -> dict[str, Any]:
    name = args.profile
    credentials = load_credentials()
    profiles = credentials.setdefault("profiles", {})
    if args.auth_command in {"login", "import-file"}:
        source_path: Path | None = None
        warnings: list[str] = []
        if args.auth_command == "login":
            key = getpass.getpass("OpenAI Ads API key (hidden): ").strip()
        else:
            key, source_path, warnings = _read_key_file(args.file)
        if not key:
            raise AuthError("No API key entered")
        response = AdsClient(key).request("GET", "/ad_account")
        account = _account_data(response.data)
        account_id = _account_id(response.data)
        if not account_id:
            raise AuthError("GET /ad_account succeeded but returned no account ID")
        profiles[name] = {
            "api_key": key,
            "account_id": account_id,
            "account_name": account.get("name"),
            "timezone": account.get("timezone") or account.get("time_zone"),
            "key_fingerprint": fingerprint(key),
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_credentials(credentials)
        removed_source = False
        if source_path and args.remove_source:
            source_path.unlink()
            removed_source = True
        return envelope(ok=True, profile=name, account_id=account_id, data={
            "authenticated": True,
            "key_fingerprint": fingerprint(key),
            "imported_from_file": bool(source_path),
            "source_removed": removed_source,
            "remove_source_recommended": bool(source_path) and not removed_source,
        }, warnings=warnings)
    if args.auth_command == "status":
        stored = profiles.get(name)
        if not stored:
            return envelope(ok=True, profile=name, data={"authenticated": False})
        response = AdsClient(stored["api_key"]).request("GET", "/ad_account")
        return envelope(ok=True, profile=name, account_id=_account_id(response.data), data={
            "authenticated": True,
            "key_fingerprint": stored.get("key_fingerprint"),
            "validated": True,
        }, request={"request_id": response.request_id})
    if args.auth_command == "logout":
        removed = profiles.pop(name, None)
        save_credentials(credentials)
        return envelope(ok=True, profile=name, account_id=removed.get("account_id") if removed else None, data={"removed": bool(removed)})
    raise ValidationError("Unknown auth command")


def _read_key_file(path_value: str) -> tuple[str, Path, list[str]]:
    path = Path(path_value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise AuthError("Key import source must be a regular non-symlink file")
    info = path.lstat()
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise AuthError("Key import source owner does not match the current user")
    if info.st_size > 4096:
        raise AuthError("Key import source is unexpectedly large")
    warnings: list[str] = []
    if stat.S_IMODE(info.st_mode) != 0o600:
        os.chmod(path, 0o600)
        warnings.append("Hardened imported key file permissions to 0600")
    try:
        key = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise AuthError(f"Cannot read key import source: {type(exc).__name__}") from exc
    if not key or any(char.isspace() for char in key):
        raise AuthError("Downloaded key file must contain exactly one non-whitespace token")
    return key, path, warnings


def _preview_capability(client: AdsClient, surface: str) -> None:
    if surface == "documented":
        return
    account = _account_data(client.request("GET", "/ad_account").data)
    if not capability_enabled(account, surface):
        raise ValidationError(f"Live account response did not advertise capability '{surface}'; failing closed")


def _readback_path(method: str, path: str, response_data: Any) -> str | None:
    if method == "DELETE" or "archive" in path.lower():
        return path.rsplit("/archive", 1)[0]
    if method == "POST":
        data = _account_data(response_data)
        resource_id = data.get("id") if isinstance(data, dict) else None
        if resource_id and path.count("/") == 1:
            return f"{path.rstrip('/')}/{resource_id}"
    if method in {"PUT", "PATCH"}:
        return path
    return None


def _before_path(method: str, path: str) -> str | None:
    if path == "/ad_account/brand":
        return "/ad_account"
    action = path.rsplit("/", 1)[-1]
    if action in {"archive", "activate", "pause", "add", "remove", "replace"}:
        return path.rsplit("/", 1)[0]
    if method in {"PUT", "PATCH", "DELETE"}:
        return path
    return None


def _planned_after(method: str, path: str, before: Any, body: Any) -> Any:
    action = path.rsplit("/", 1)[-1]
    if isinstance(before, dict) and isinstance(body, dict) and method in {"PUT", "PATCH"}:
        return {**before, **body}
    if isinstance(before, dict) and action in {"archive", "activate", "pause"}:
        result = dict(before)
        result["status"] = "archived" if action == "archive" else ("active" if action == "activate" else "paused")
        return result
    return body


def _audience_summary(value: Any) -> Any:
    if not isinstance(value, dict):
        return None if value is None else "[REDACTED AUDIENCE DATA]"
    allowed = {"id", "name", "revision", "version", "count", "status", "operation_id", "file_size", "file_sha256", "purpose"}
    summary = {key: redact(child) for key, child in value.items() if key in allowed}
    summary["audience_data_redacted"] = True
    return summary


def _mutation_diff(before: Any, after: Any, *, audience: bool) -> dict[str, Any]:
    if audience:
        return {"before": _audience_summary(before), "after": _audience_summary(after)}
    return {"before": redact(before), "after": redact(after)}


def command_api(args: argparse.Namespace) -> dict[str, Any]:
    profile, client = _profile_client(args.profile)
    method = args.method.upper()
    path = normalize_path(args.path)
    surface = authorize_surface(path, args.surface, method)
    _preview_capability(client, surface)
    query = safe_json_file(args.query_file) or {}
    if args.body_file and args.upload_file:
        raise ValidationError("Use either --body-file or --upload-file")
    upload_path = Path(args.upload_file) if args.upload_file else None
    if upload_path:
        if upload_path.is_symlink() or not upload_path.is_file():
            raise ValidationError("Upload input must be a regular non-symlink file")
        if path not in {"/upload", "/uploads"} or method != "POST":
            raise ValidationError("--upload-file requires POST /upload or POST /uploads")
        if path == "/uploads" and args.purpose != "custom_audience":
            raise ValidationError("POST /uploads requires --purpose custom_audience in the documented surface")
        if args.purpose == "custom_audience" and upload_path.stat().st_size > 500 * 1024 * 1024:
            raise ValidationError("Custom audience files must not exceed 500 MB")
        body = {
            "file_name": upload_path.name,
            "file_size": upload_path.stat().st_size,
            "file_sha256": _file_sha256(upload_path),
            "purpose": args.purpose,
        }
    else:
        body = safe_json_file(args.body_file) if args.body_file else None
    if not isinstance(query, dict):
        raise ValidationError("--query-file must contain a JSON object")
    warnings: list[str] = []
    account_id = profile.get("account_id")
    if is_read(method):
        response = client.request(method, path, query=query, all_pages=args.all_pages)
        return envelope(ok=True, profile=args.profile, account_id=account_id, data=response.data, warnings=warnings,
                        pagination={"pages": response.pages, "exhausted": True},
                        request={"method": method, "path": path, "request_id": response.request_id, "status": response.status})
    try:
        warnings.extend(validate_mutation(method, path, body))
    except AdsManagerError as exc:
        raise _with_context(exc, account_id, warnings)
    if path.startswith("/ads") and not args.policy_reviewed:
        raise _with_context(
            PolicyError("Creative mutations require --policy-reviewed after checking current Ads Policy and landing-page consistency"),
            account_id, warnings,
        )
    is_audience_data = path.startswith("/custom_audiences") or (path == "/uploads" and args.purpose == "custom_audience")
    if is_audience_data and not (args.first_party_confirmed and args.non_eea_confirmed):
        raise _with_context(
            PolicyError("Audience mutations require --first-party-confirmed and --non-eea-confirmed"), account_id, warnings,
        )
    if path.startswith("/custom_audiences") and not args.idempotency_key:
        raise _with_context(
            ValidationError("Custom audience mutations require a persisted --idempotency-key"), account_id, warnings,
        )
    before: Any = None
    before_path = _before_path(method, path)
    if before_path:
        try:
            before = client.request("GET", before_path).data
        except AdsManagerError as exc:
            error = ValidationError(
                f"Before-read is required for a safe mutation and failed with category {exc.category}; no confirmation hash issued"
            )
            raise _with_context(error, account_id, warnings) from exc
    audience_action = path.rsplit("/", 1)[-1] if path.startswith("/custom_audiences") else None
    if audience_action in {"add", "remove", "replace"}:
        if not isinstance(body, dict):
            raise _with_context(ValidationError("Audience membership request body must be a JSON object"), account_id, warnings)
        revision = before.get("membership_revision") if isinstance(before, dict) else None
        expected = body.get("expected_revision")
        if audience_action == "replace" and (
            isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
        ):
            raise _with_context(ValidationError("Audience replace requires a nonnegative expected_revision"), account_id, warnings)
        if audience_action in {"add", "remove"} and "expected_revision" not in body and isinstance(revision, int):
            body["expected_revision"] = revision
            warnings.append("Bound audience mutation to the live membership_revision")
    diff = _mutation_diff(before, _planned_after(method, path, before, body), audience=is_audience_data)
    confirmation_material = {"request": body, "before_hash": body_hash(before)}
    token = confirmation_hash(method, path, confirmation_material, account_id)
    plan = {
        "operation": f"{method} {path}",
        "surface": surface,
        "diff": diff,
        "body_hash": body_hash(body),
        "confirmation_hash": token,
        "idempotent_retry": bool(args.idempotency_key),
    }
    if not args.apply:
        return envelope(ok=True, profile=args.profile, account_id=account_id, data={"dry_run": True, "plan": plan}, warnings=warnings,
                        request={"method": method, "path": path})
    if not args.confirm or args.confirm != token:
        raise ConflictErrorWithPlan("Confirmation hash missing or stale; rerun without --apply", plan, account_id, warnings)
    audit_preflight()
    if upload_path:
        try:
            response = client.upload_file(path, upload_path, purpose=args.purpose, idempotency_key=args.idempotency_key)
        except AdsManagerError as exc:
            raise _with_context(exc, account_id, warnings)
    else:
        try:
            response = client.request(method, path, query=query, body=body, idempotency_key=args.idempotency_key)
        except AdsManagerError as exc:
            raise _with_context(exc, account_id, warnings)
    readback: Any = None
    readback_path = _readback_path(method, path, response.data)
    if readback_path:
        try:
            readback_response = client.request("GET", readback_path)
            readback = readback_response.data
        except AdsManagerError as exc:
            warnings.append(f"Post-write readback failed safely: {exc.category}")
    audit_event({
        "operation": "api_mutation", "method": method, "path": path, "resource_ids": [_account_data(response.data).get("id")],
        "body_hash": body_hash(body), "diff": diff, "request_id": response.request_id,
        "idempotency_key": args.idempotency_key,
    })
    return envelope(ok=True, profile=args.profile, account_id=account_id,
                    data={"applied": True, "result": response.data, "readback": readback}, warnings=warnings,
                    request={"method": method, "path": path, "status": response.status, "request_id": response.request_id})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class ConflictErrorWithPlan(AdsManagerError):
    category = "conflict"
    exit_code = 4

    def __init__(self, message: str, plan: dict[str, Any], account_id: str | None = None, warnings: list[str] | None = None):
        super().__init__(message, details={"plan": plan})
        self.account_id = account_id
        self.warnings = warnings or []


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [item for item in payload["data"] if isinstance(item, dict)]
    return []


def _report_range(client: AdsClient, account_id: str, start: Any, end: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, str | None]:
    time_range = json.dumps({"type": "date_range", "since": start.isoformat(), "until": end.isoformat()}, separators=(",", ":"))
    delivery = client.request("GET", "/ad_account/insights", query={
        "time_granularity": "none",
        "aggregation_level": "ad_account",
        "fields[]": ["ad_account.impressions", "ad_account.clicks", "ad_account.spend"],
        "time_ranges[]": [time_range],
        "limit": 2000,
    }, all_pages=True)
    try:
        conversions = client.request("POST", "/conversions/insights", body={
            "aggregation_level": "ad_account",
            "time_ranges": [time_range],
            "entity_ids": [account_id],
        }, idempotency_key=str(uuid.uuid4()))
        conversion_rows = _rows(conversions.data)
        warning = None
    except AdsManagerError as exc:
        conversion_rows = []
        warning = f"Conversion insights unavailable ({exc.category}); conversion metrics are null, not zero"
    return _rows(delivery.data), conversion_rows, delivery.pages, warning


def command_report(args: argparse.Namespace) -> dict[str, Any]:
    profile, client = _profile_client(args.profile)
    account_response = client.request("GET", "/ad_account")
    account = _account_data(account_response.data)
    account_id = _account_id(account_response.data) or profile.get("account_id")
    timezone_name = account.get("timezone") or account.get("time_zone") or profile.get("timezone")
    if not timezone_name:
        raise ValidationError("Ad account timezone is unavailable; refusing to guess reporting day boundaries")
    windows = [int(value) for value in args.windows.split(",")]
    targets = {"target_cpa": args.target_cpa, "target_cpc": args.target_cpc, "target_ctr": args.target_ctr, "business_goal": args.business_goal}
    has_target = any(value is not None for value in targets.values())
    output: list[dict[str, Any]] = []
    total_pages = 0
    warnings: list[str] = []
    for days in windows:
        window = completed_window(days, timezone_name)
        current_delivery, current_conversions, pages, current_warning = _report_range(client, account_id, window.start, window.end)
        previous_delivery, previous_conversions, previous_pages, previous_warning = _report_range(client, account_id, window.previous_start, window.previous_end)
        current = summarize(current_delivery, current_conversions, conversions_available=current_warning is None)
        previous = summarize(previous_delivery, previous_conversions, conversions_available=previous_warning is None)
        warnings.extend(item for item in (current_warning, previous_warning) if item and item not in warnings)
        total_pages += pages + previous_pages
        output.append({
            "days": days,
            "account_timezone": timezone_name,
            "current": {"start": window.start.isoformat(), "end": window.end.isoformat(), "metrics": current},
            "previous": {"start": window.previous_start.isoformat(), "end": window.previous_end.isoformat(), "metrics": previous},
            "relative_change": compare(current, previous),
            "diagnostics": diagnostics(current, has_target=has_target),
        })
    audit_window = completed_window(max(windows), timezone_name)
    hierarchy: dict[str, Any] = {}
    campaign_items: list[dict[str, Any]] = []
    for resource in ("campaigns", "ad_groups", "ads"):
        try:
            result = client.request("GET", f"/{resource}", query={"limit": 100}, all_pages=True)
            items = _rows(result.data)
            if resource == "campaigns":
                campaign_items = items
            hierarchy[resource] = {
                "count": len(items),
                "status_counts": _counts(items, "status"),
                "review_status_counts": _counts(items, "review_status") if resource == "ads" else {},
            }
            total_pages += result.pages
        except AdsManagerError as exc:
            hierarchy[resource] = {"error_category": exc.category, "message": exc.message}
    pacing = _campaign_pacing(client, campaign_items, audit_window, timezone_name)
    total_pages += int(pacing.get("pages") or 0)
    conversion_health: dict[str, Any]
    try:
        settings_response = client.request("GET", "/conversions/event_settings", query={"limit": 100}, all_pages=True)
        settings = _rows(settings_response.data)
        conversion_health = {
            "event_settings": len(settings),
            "archived": sum(1 for item in settings if item.get("archived") is True),
            "source_ids_configured": sum(1 for item in settings if item.get("source_ids")),
        }
        total_pages += settings_response.pages
    except AdsManagerError as exc:
        conversion_health = {"available": False, "error_category": exc.category, "message": exc.message}
        warnings.append("Conversion setup inspection is unavailable for this account")
    if args.pixel_id:
        try:
            recent = client.request("GET", "/conversions/events", query={"pid": args.pixel_id})
            conversion_health["recent_pixel_events_15m"] = len(_rows(recent.data))
            conversion_health["recent_event_stream_scope"] = "receipt testing only; not attribution reporting"
        except AdsManagerError as exc:
            conversion_health["recent_pixel_events_error"] = {"category": exc.category, "message": exc.message}

    segment_data: dict[str, Any] = {}
    for segment in [item.strip() for item in args.segments.split(",") if item.strip()]:
        if segment not in {"product", "country", "device"}:
            raise ValidationError(f"Unsupported report segment: {segment}")
        try:
            time_range = json.dumps({
                "type": "date_range", "since": audit_window.start.isoformat(), "until": audit_window.end.isoformat()
            }, separators=(",", ":"))
            fields = {
                "product": ["product.feed_id", "product.item_id", "product.title"],
                "country": ["country.name"],
                "device": ["device.type"],
            }[segment] + [f"{segment}.impressions", f"{segment}.clicks", f"{segment}.spend"]
            segment_response = client.request("GET", "/ad_account/insights", query={
                "time_granularity": "none",
                "aggregation_level": "ad_account",
                "segments[]": [segment],
                "override_segment_group_order[]": [segment, "ad_account"],
                "fields[]": fields,
                "time_ranges[]": [time_range],
                "limit": 2000,
            }, all_pages=True)
            rows = _rows(segment_response.data)
            rows.sort(key=lambda item: float(item.get(f"{segment}_spend") or item.get("spend") or 0), reverse=True)
            segment_data[segment] = {"rows": rows[:20], "total_rows": len(rows), "truncated_to_top": 20 if len(rows) > 20 else None}
            total_pages += segment_response.pages
        except AdsManagerError as exc:
            segment_data[segment] = {"available": False, "error_category": exc.category, "message": exc.message}
    return envelope(ok=True, profile=args.profile, account_id=account_id,
                    data={"account_health": {
                              "status": account.get("status"), "brand_review": account.get("review"),
                              "name": account.get("name"), "url": account.get("url"),
                              "timezone": timezone_name, "currency_code": account.get("currency_code"),
                          },
                          "windows": output, "hierarchy": hierarchy, "pacing": pacing, "conversion_health": conversion_health,
                          "segments": segment_data, "targets": targets, "current_day_excluded": True},
                    warnings=warnings + ([] if has_target else ["No spend recommendation was produced without a target KPI or business goal"]),
                    pagination={"pages": total_pages, "exhausted": True}, request={"request_id": account_response.request_id})


def _counts(items: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(field, "missing"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _campaign_pacing(client: AdsClient, campaigns: list[dict[str, Any]], window: Any, timezone_name: str) -> dict[str, Any]:
    if not campaigns:
        return {"rows": [], "note": "No campaign objects were available for pacing checks"}
    time_range = json.dumps({
        "type": "date_range", "since": window.start.isoformat(), "until": window.end.isoformat()
    }, separators=(",", ":"))
    try:
        response = client.request("GET", "/ad_account/insights", query={
            "time_granularity": "none",
            "aggregation_level": "campaign",
            "fields[]": ["campaign.id", "campaign.impressions", "campaign.clicks", "campaign.spend"],
            "includes[]": ["zero_impression_items"],
            "time_ranges[]": [time_range],
            "limit": 2000,
        }, all_pages=True)
    except AdsManagerError as exc:
        return {"available": False, "error_category": exc.category, "message": exc.message}
    insights = {str(row.get("campaign_id")): row for row in _rows(response.data) if row.get("campaign_id")}
    zone = ZoneInfo(timezone_name)
    window_start = datetime.combine(window.start, time.min, tzinfo=zone).timestamp()
    window_end = datetime.combine(window.end + timedelta(days=1), time.min, tzinfo=zone).timestamp()
    rows: list[dict[str, Any]] = []
    for campaign in campaigns:
        campaign_id = str(campaign.get("id") or "")
        metric = insights.get(campaign_id, {})
        start = campaign.get("start_time")
        end = campaign.get("end_time")
        budget = campaign.get("budget") if isinstance(campaign.get("budget"), dict) else {}
        budget_micros = budget.get("lifetime_spend_limit_micros")
        blockers: list[str] = []
        if campaign.get("status") == "active" and isinstance(start, (int, float)) and start >= window_end:
            blockers.append("scheduled_after_report_window")
        if campaign.get("status") == "active" and isinstance(end, (int, float)) and end <= window_start:
            blockers.append("active_but_ended")
        if campaign.get("status") == "active" and not metric.get("impressions"):
            blockers.append("zero_delivery_in_window")
        expected = None
        ratio = None
        if all(isinstance(value, (int, float)) for value in (start, end, budget_micros)) and end > start:
            overlap = max(0.0, min(float(end), window_end) - max(float(start), window_start))
            expected = (float(budget_micros) / 1_000_000) * (overlap / (float(end) - float(start)))
            spend = float(metric.get("spend") or 0)
            ratio = spend / expected if expected > 0 else None
        rows.append({
            "campaign_id": campaign_id,
            "name": campaign.get("name"),
            "status": campaign.get("status"),
            "window_spend": float(metric.get("spend") or 0),
            "expected_even_pace_spend": expected,
            "actual_to_even_pace_ratio": ratio,
            "blockers": blockers,
        })
    return {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat()},
        "rows": rows,
        "interpretation": "Even-pacing ratios are evidence, not automatic scale/pause thresholds.",
        "pages": response.pages,
    }


def _load_capi_secrets() -> dict[str, Any]:
    return secure_read("capi-secrets.json", {"version": 1, "profiles": {}})


def command_capi(args: argparse.Namespace) -> dict[str, Any]:
    profile, ads_client = _profile_client(args.profile)
    account_id = profile.get("account_id")
    secrets = _load_capi_secrets()
    profile_secrets = secrets.setdefault("profiles", {}).setdefault(args.profile, {})
    if args.capi_command == "key":
        if args.key_command == "create":
            if not 3 <= len(args.name) <= 1000:
                raise ValidationError("CAPI key name must be 3–1000 characters")
            create_body = {"name": args.name}
            token = confirmation_hash("POST", "/conversions/api_keys", create_body, account_id)
            plan = {
                "operation": "POST /conversions/api_keys",
                "diff": {"before": None, "after": create_body, "reversible": "Revoke in Ads Manager"},
                "confirmation_hash": token,
                "secret_handling": "The returned key will be intercepted and stored locally without printing it",
            }
            if not args.apply:
                return envelope(ok=True, profile=args.profile, account_id=account_id, data={"dry_run": True, "plan": plan})
            if args.confirm != token:
                raise ConflictErrorWithPlan("Confirmation hash missing or stale; rerun without --apply", plan, account_id)
            audit_preflight()
            response = ads_client.request("POST", "/conversions/api_keys", body={"name": args.name},
                                          idempotency_key=args.idempotency_key or str(uuid.uuid4()), sensitive_response=True)
            raw = response.data if isinstance(response.data, dict) else {}
            secret = raw.get("api_key")
            if not isinstance(secret, str) or not secret:
                raise AdsManagerError("CAPI key response did not contain a secret")
            profile_secrets[args.name] = {
                "api_key": secret,
                "key_fingerprint": fingerprint(secret),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            path = atomic_write("capi-secrets.json", secrets)
            audit_event({"operation": "capi_key_create", "method": "POST", "path": "/conversions/api_keys",
                         "body_hash": body_hash({"name": args.name}), "request_id": response.request_id})
            return envelope(ok=True, profile=args.profile, account_id=account_id, data={
                "name": args.name, "key_fingerprint": fingerprint(secret), "stored_at": str(path),
                "production_secret_transfer_required": True,
            })
        if args.key_command == "status":
            entries = [{"name": name, "key_fingerprint": item.get("key_fingerprint"), "created_at": item.get("created_at")}
                       for name, item in profile_secrets.items()]
            return envelope(ok=True, profile=args.profile, account_id=account_id, data={
                "local_keys": entries, "server_validity_checked": False,
            })
        if args.key_command == "remove-local":
            removed = profile_secrets.pop(args.name, None)
            atomic_write("capi-secrets.json", secrets)
            return envelope(ok=True, profile=args.profile, account_id=account_id, data={
                "removed_local": bool(removed), "server_key_revoked": False,
                "warning": "Removing the local copy does not revoke the server-side key.",
            })
    if args.capi_command == "send":
        if not args.validate_only:
            raise PolicyError(f"Skill v{VERSION} requires --validate-only; production event sending belongs in the target server integration")
        entry = profile_secrets.get(args.key_name)
        if not entry:
            raise AuthError(f"No local CAPI key named '{args.key_name}' for profile '{args.profile}'")
        payload = safe_json_file(args.body_file)
        validated = validate_batch(payload, consent_confirmed=args.consent_confirmed)
        validated["validate_only"] = True
        capi_client = AdsClient(entry["api_key"], base_url=CAPI_BASE_URL)
        response = capi_client.request("POST", "/events", query={"pid": args.pixel_id}, body=validated,
                                       idempotency_key=str(uuid.uuid4()))
        return envelope(ok=True, profile=args.profile, account_id=account_id, data={
            "validated": True, "event_count": len(validated["events"]), "response": response.data,
        }, request={"status": response.status, "request_id": response.request_id})
    raise ValidationError("Unknown CAPI command")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openai_ads.py", description="Safe OpenAI Ads management and reporting")
    sub = parser.add_subparsers(dest="command", required=True)
    version = sub.add_parser("version")
    version.set_defaults(handler=command_version)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--check-updates", action="store_true")
    doctor.add_argument("--offline", action="store_true")
    doctor.set_defaults(handler=command_doctor)

    auth = sub.add_parser("auth")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    for name in ("login", "status", "logout"):
        item = auth_sub.add_parser(name)
        item.add_argument("--profile", default="default")
        item.set_defaults(handler=command_auth)
    import_file = auth_sub.add_parser("import-file")
    import_file.add_argument("--profile", default="default")
    import_file.add_argument("--file", required=True, help="Downloaded one-line Ads API key file")
    import_file.add_argument("--remove-source", action="store_true", help="Delete the source file only after successful validation and storage")
    import_file.set_defaults(handler=command_auth)

    api = sub.add_parser("api")
    api_sub = api.add_subparsers(dest="api_command", required=True)
    request = api_sub.add_parser("request")
    request.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE", "get", "post", "put", "patch", "delete"])
    request.add_argument("path")
    request.add_argument("--profile", default="default")
    request.add_argument("--query-file")
    request.add_argument("--body-file", help="JSON file or - for stdin")
    request.add_argument("--upload-file", help="Binary file for POST /upload or audience file for POST /uploads")
    request.add_argument("--purpose", choices=["custom_audience", "account_favicon"])
    request.add_argument("--all-pages", action="store_true")
    request.add_argument("--surface", choices=["documented", "bulk_preview", "spec_preview"], default="documented")
    request.add_argument("--idempotency-key")
    request.add_argument("--apply", action="store_true")
    request.add_argument("--confirm")
    request.add_argument("--policy-reviewed", action="store_true")
    request.add_argument("--first-party-confirmed", action="store_true")
    request.add_argument("--non-eea-confirmed", action="store_true")
    request.set_defaults(handler=command_api)

    report = sub.add_parser("report")
    report_sub = report.add_subparsers(dest="report_command", required=True)
    account = report_sub.add_parser("account")
    account.add_argument("--profile", default="default")
    account.add_argument("--windows", default="7,30")
    account.add_argument("--target-cpa", type=float)
    account.add_argument("--target-cpc", type=float)
    account.add_argument("--target-ctr", type=float)
    account.add_argument("--business-goal")
    account.add_argument("--pixel-id", help="Optional Pixel ID for the recent-event receipt check")
    account.add_argument("--segments", default="product,country,device")
    account.set_defaults(handler=command_report)

    capi = sub.add_parser("capi")
    capi_sub = capi.add_subparsers(dest="capi_command", required=True)
    key = capi_sub.add_parser("key")
    key_sub = key.add_subparsers(dest="key_command", required=True)
    create = key_sub.add_parser("create")
    create.add_argument("--profile", default="default")
    create.add_argument("--name", required=True)
    create.add_argument("--idempotency-key")
    create.add_argument("--apply", action="store_true")
    create.add_argument("--confirm")
    create.set_defaults(handler=command_capi)
    status = key_sub.add_parser("status")
    status.add_argument("--profile", default="default")
    status.set_defaults(handler=command_capi)
    remove = key_sub.add_parser("remove-local")
    remove.add_argument("--profile", default="default")
    remove.add_argument("--name", required=True)
    remove.set_defaults(handler=command_capi)
    send = capi_sub.add_parser("send")
    send.add_argument("--profile", default="default")
    send.add_argument("--key-name", required=True)
    send.add_argument("--pixel-id", required=True)
    send.add_argument("--body-file", required=True, help="JSON file or - for stdin")
    send.add_argument("--validate-only", action="store_true")
    send.add_argument("--consent-confirmed", action="store_true")
    send.set_defaults(handler=command_capi)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.handler(args)
        print_json(result)
        return 0
    except AdsManagerError as exc:
        known_account_id = getattr(exc, "account_id", None) or _stored_account_id(getattr(args, "profile", None))
        payload = envelope(ok=False, profile=getattr(args, "profile", None), account_id=known_account_id, data={
            "error": {"category": exc.category, "message": exc.message, "status": exc.status, "details": redact(exc.details)}
        }, warnings=getattr(exc, "warnings", None))
        print_json(payload)
        return exc.exit_code
    except (KeyboardInterrupt, EOFError):
        payload = envelope(ok=False, profile=getattr(args, "profile", None), data={
            "error": {"category": "validation", "message": "Operation cancelled"}
        })
        print_json(payload)
        return 130
    except Exception as exc:  # Keep the public envelope stable and avoid leaking local paths or payloads.
        payload = envelope(ok=False, profile=getattr(args, "profile", None), data={
            "error": {"category": "api", "message": f"Unexpected local error: {type(exc).__name__}"}
        })
        print_json(payload)
        return 1
