"""Path classification and high-risk mutation checks."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .errors import PolicyError, ValidationError

DOCUMENTED = (
    "/campaigns",
    "/ad_groups",
    "/ads",
    "/upload",
    "/uploads",
    "/geo_lookup",
    "/conversions",
    "/custom_audiences",
)
BULK = ("/bulk",)
SPEC_PREVIEW = ("/business_agent_tools", "/business_agents", "/lead_forms", "/lead_sync_subscriptions", "/partner_data")
OAUTH_ONLY = ("/oauth", "/me", "/ad_accounts", "/ad_account_creation_sessions", "/organizations", "/organization_users")
SECRET_PATHS = (
    re.compile(r"^/api_keys(?:/|$)"),
    re.compile(r"^/conversions/api_keys(?:/|$)"),
    re.compile(r"/sftp_access(?:/|$)", re.IGNORECASE),
)


def normalize_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    decoded = urllib.parse.unquote(path)
    if "?" in path:
        raise ValidationError("Put query parameters in --query-file, not PATH")
    if "://" in decoded or ".." in decoded or "\\" in decoded or "#" in decoded:
        raise ValidationError("PATH must be a relative API path on the fixed Ads host")
    return path


def _prefix(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == value or path.startswith(value + "/") for value in prefixes)


def classify_path(path: str, method: str | None = None) -> str:
    path = normalize_path(path)
    for pattern in SECRET_PATHS:
        if pattern.search(path):
            return "secret"
    if _prefix(path, OAUTH_ONLY):
        return "oauth_only"
    if _prefix(path, BULK):
        return "bulk_preview"
    if _prefix(path, SPEC_PREVIEW):
        return "spec_preview"
    if path.startswith("/feeds/") and path.endswith("/products"):
        return "documented" if not method or method.upper() == "PATCH" else "spec_preview"
    if _prefix(path, ("/feeds",)):
        return "spec_preview"
    if _prefix(path, ("/ad_account/activate", "/ad_account/pause", "/ad_account/negative_keywords", "/ad_account/spend_limit_windows")):
        return "spec_preview"
    if path in {"/ad_account", "/ad_account/brand", "/ad_account/insights"}:
        return "documented"
    if _prefix(path, DOCUMENTED):
        return "documented"
    return "unknown"


def authorize_surface(path: str, requested: str, method: str | None = None) -> str:
    actual = classify_path(path, method)
    if actual == "secret":
        raise PolicyError("Secret-generating endpoints are blocked in generic API mode; use a secret-aware flow or Ads Manager")
    if actual == "oauth_only":
        raise ValidationError("OAuth-only API paths are unsupported in skill version 0.1.0")
    if actual == "unknown":
        raise ValidationError("Unknown API path; update compatibility.json after verifying official documentation")
    if actual != "documented" and requested != actual:
        raise ValidationError(f"Path requires explicit --surface {actual}")
    if requested not in {"documented", "bulk_preview", "spec_preview"}:
        raise ValidationError("Invalid surface selection")
    return actual


def is_read(method: str) -> bool:
    return method.upper() in {"GET", "HEAD", "OPTIONS"}


def validate_mutation(method: str, path: str, body: Any) -> list[str]:
    method = method.upper()
    warnings: list[str] = []
    if is_read(method):
        return warnings
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        raise ValidationError(f"Unsupported HTTP method: {method}")
    path = normalize_path(path)
    if method == "POST" and path.rstrip("/") == "/ads":
        status = body.get("status") if isinstance(body, dict) else None
        if status and str(status).lower() != "paused":
            raise PolicyError("New ads must be created paused; set status to 'paused'")
        if isinstance(body, dict) and not status:
            body["status"] = "paused"
            warnings.append("Added status=paused to new ad")
    if path.startswith("/ads") and isinstance(body, dict):
        creative = body.get("creative") if isinstance(body.get("creative"), dict) else body
        title = creative.get("title")
        copy = creative.get("body") if creative.get("body") is not None else creative.get("copy")
        if title is not None and not 3 <= len(str(title)) <= 50:
            raise ValidationError("Ad creative title must be 3–50 characters")
        if copy is not None and not 1 <= len(str(copy)) <= 100:
            raise ValidationError("Ad creative body must be 1–100 characters")
        target_url = creative.get("target_url") or creative.get("link")
        if target_url is not None and not str(target_url).startswith(("https://", "http://")):
            raise ValidationError("Ad creative target URL must use http or https")
    lowered = path.lower()
    if method == "DELETE" or "archive" in lowered:
        warnings.append("Archive is irreversible and confirmation binds the resource ID")
    body_keys = _nested_keys(body)
    if any(term in lowered for term in ("budget", "bid", "target", "activate")) or any(
        term in key for key in body_keys for term in ("budget", "bid", "targeting")
    ):
        warnings.append("This changes spend, bidding, targeting, or delivery")
    if any(key in {"status", "enabled", "active"} for key in body_keys):
        warnings.append("Delivery state is part of this mutation; verify parent hierarchy and review status")
    if path.startswith("/custom_audiences"):
        warnings.append("Audience operations require first-party rights, consent, and non-EEA/Switzerland eligibility")
    return warnings


def _nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(_nested_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_nested_keys(child))
    return keys


def redact_diff(body: Any) -> dict[str, Any]:
    from .security import redact

    return {"before": "readback required", "after": redact(body), "reversible": False}
