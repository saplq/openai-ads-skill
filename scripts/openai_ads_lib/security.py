"""Credential storage, permission enforcement, redaction, and audit helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fcntl

from .errors import AuthError, ValidationError

CONFIG_ENV = "OPENAI_ADS_MANAGER_CONFIG_DIR"
PLAN_TTL_SECONDS = 15 * 60
SECRET_KEYS = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|cookie|"
    r"email|phone|external[_-]?id|first[_-]?name|last[_-]?name|address|ip[_-]?address)",
    re.IGNORECASE,
)
AUDIENCE_KEYS = re.compile(r"(?:^|_)(?:identifiers?|member_ids?|user_ids?)(?:$|_)", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"(?i)(?:bearer\s+[a-z0-9._-]{8,}|(?:sk|oa|capi)[-_][a-z0-9_-]{8,})")
EMAIL_PATTERN = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+[0-9][0-9\s().-]{6,}[0-9]|[0-9][0-9\s().-]{9,}[0-9])(?!\w)")
IP_PATTERN = re.compile(r"(?<!\w)(?:\d{1,3}\.){3}\d{1,3}(?!\w)")


def config_dir() -> Path:
    override = os.environ.get(CONFIG_ENV)
    return Path(override).expanduser() if override else Path.home() / ".config" / "openai-ads-manager"


def _ensure_owner(path: Path, st: os.stat_result) -> None:
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise AuthError(f"Refusing {path}: owner does not match the current user")


def ensure_secure_dir(path: Path | None = None) -> Path:
    path = path or config_dir()
    if path.is_symlink():
        raise AuthError(f"Refusing symlinked config directory: {path}")
    if path.exists():
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode):
            raise AuthError(f"Config path is not a directory: {path}")
        _ensure_owner(path, st)
        if stat.S_IMODE(st.st_mode) != 0o700:
            raise AuthError(f"Config directory must have permissions 0700: {path}")
    else:
        path.mkdir(parents=True, mode=0o700)
        os.chmod(path, 0o700)
    return path


def _check_secure_file(path: Path) -> None:
    if path.is_symlink():
        raise AuthError(f"Refusing symlinked credential file: {path}")
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode):
        raise AuthError(f"Credential path is not a regular file: {path}")
    _ensure_owner(path, st)
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise AuthError(f"Credential file must have permissions 0600: {path}")


def secure_read(name: str, default: Any = None) -> Any:
    root = config_dir()
    if not root.exists():
        return default
    root = ensure_secure_dir(root)
    path = root / name
    if not path.exists():
        return default
    _check_secure_file(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"Cannot read secure file {name}: {exc}") from exc


def atomic_write(name: str, data: Any) -> Path:
    root = ensure_secure_dir()
    destination = root / name
    if destination.exists():
        _check_secure_file(destination)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{name}.", dir=root)
    tmp_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, destination)
        os.chmod(destination, 0o600)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return destination


def load_credentials() -> dict[str, Any]:
    return secure_read("credentials.json", {"version": 1, "profiles": {}})


def save_credentials(payload: dict[str, Any]) -> Path:
    payload.setdefault("version", 1)
    payload.setdefault("profiles", {})
    return atomic_write("credentials.json", payload)


def get_profile(name: str) -> dict[str, Any]:
    profile = load_credentials().get("profiles", {}).get(name)
    if not isinstance(profile, dict) or not profile.get("api_key"):
        raise AuthError(f"Profile '{name}' is not authenticated; run auth login")
    return profile


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def redact(value: Any, *, key: str = "") -> Any:
    if SECRET_KEYS.search(key) or AUDIENCE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = TOKEN_PATTERN.sub("[REDACTED]", value)
        value = EMAIL_PATTERN.sub("[REDACTED]", value)
        value = PHONE_PATTERN.sub("[REDACTED]", value)
        return IP_PATTERN.sub("[REDACTED]", value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def body_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def confirmation_hash(
    method: str,
    path: str,
    body: Any,
    account_id: str | None,
    *,
    query: Any = None,
    idempotency_key: str | None = None,
    before_hash: str | None = None,
    plan_nonce: str | None = None,
) -> str:
    material = {
        "method": method.upper(),
        "path": path,
        "body": body,
        "query": query,
        "idempotency_key_hash": body_hash(idempotency_key) if idempotency_key else None,
        "before_hash": before_hash,
        "plan_nonce": plan_nonce,
        "account_id": account_id,
    }
    digest = body_hash(material)[:16]
    resource_id = extract_resource_id(path)
    irreversible = method.upper() == "DELETE" or "archive" in path.lower()
    return f"{digest}:{resource_id}" if irreversible and resource_id else digest


@contextmanager
def _plan_lock():
    root = ensure_secure_dir()
    path = root / "pending-plans.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise AuthError(f"Cannot lock confirmation plans safely: {exc}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        st = os.fstat(descriptor)
        if not stat.S_ISREG(st.st_mode):
            raise AuthError("Confirmation plan lock is not a regular file")
        _ensure_owner(path, st)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _active_plans(payload: Any, *, now: float) -> dict[str, Any]:
    source = payload.get("plans", {}) if isinstance(payload, dict) else {}
    return {
        token: record
        for token, record in source.items()
        if isinstance(record, dict)
        and not record.get("consumed_at")
        and isinstance(record.get("expires_at"), (int, float))
        and float(record["expires_at"]) > now
    }


def save_confirmation_plan(token: str, record: dict[str, Any], *, ttl_seconds: int = PLAN_TTL_SECONDS) -> dict[str, Any]:
    now = time.time()
    saved = {
        **record,
        "created_at": now,
        "expires_at": now + ttl_seconds,
        "consumed_at": None,
    }
    with _plan_lock():
        plans = _active_plans(secure_read("pending-plans.json", {"version": 1, "plans": {}}), now=now)
        plans[token] = saved
        if len(plans) > 128:
            ordered = sorted(plans, key=lambda item: float(plans[item].get("expires_at", 0)), reverse=True)
            plans = {item: plans[item] for item in ordered[:128]}
        atomic_write("pending-plans.json", {"version": 1, "plans": plans})
    return dict(saved)


def get_confirmation_plan(token: str) -> dict[str, Any] | None:
    now = time.time()
    payload = secure_read("pending-plans.json", {"version": 1, "plans": {}})
    record = payload.get("plans", {}).get(token) if isinstance(payload, dict) else None
    if not isinstance(record, dict) or record.get("consumed_at"):
        return None
    expires_at = record.get("expires_at")
    if not isinstance(expires_at, (int, float)) or float(expires_at) <= now:
        return None
    return dict(record)


def consume_confirmation_plan(token: str, request_hash: str) -> bool:
    now = time.time()
    with _plan_lock():
        payload = secure_read("pending-plans.json", {"version": 1, "plans": {}})
        plans = payload.get("plans", {}) if isinstance(payload, dict) else {}
        record = plans.get(token) if isinstance(plans, dict) else None
        if (
            not isinstance(record, dict)
            or record.get("consumed_at")
            or record.get("request_hash") != request_hash
            or not isinstance(record.get("expires_at"), (int, float))
            or float(record["expires_at"]) <= now
        ):
            return False
        record["consumed_at"] = now
        atomic_write("pending-plans.json", {"version": 1, "plans": plans})
    return True


def extract_resource_id(path: str) -> str | None:
    parts = [part for part in path.split("?")[0].split("/") if part]
    if len(parts) >= 2 and parts[-1] not in {"archive", "activate", "pause"}:
        return parts[-1]
    if len(parts) >= 3:
        return parts[-2]
    return None


def audit_event(event: dict[str, Any]) -> Path:
    root = ensure_secure_dir()
    path = root / "audit.jsonl"
    if path.exists():
        _check_secure_file(path)
    safe = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": event.get("operation"),
        "method": event.get("method"),
        "path": event.get("path"),
        "resource_ids": event.get("resource_ids", []),
        "body_hash": event.get("body_hash"),
        "diff": redact(event.get("diff")),
        "request_id": event.get("request_id"),
        "idempotency_key_hash": body_hash(event["idempotency_key"])[:12] if event.get("idempotency_key") else None,
    }
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise AuthError(f"Cannot open audit log safely: {exc}") from exc
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(canonical_json(safe) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)
    return path


def audit_preflight() -> Path:
    """Validate audit destination before a remote write can succeed."""
    root = ensure_secure_dir()
    path = root / "audit.jsonl"
    if path.exists():
        _check_secure_file(path)
    return path


def safe_json_file(path_value: str | None, *, allow_stdin: bool = True) -> Any:
    if not path_value:
        return None
    if path_value == "-":
        if not allow_stdin:
            raise ValidationError("stdin is not allowed for this input")
        import sys

        raw = sys.stdin.read()
    else:
        path = Path(path_value)
        if path.is_symlink() or not path.is_file():
            raise ValidationError(f"Input must be a regular non-symlink file: {path}")
        raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON input: {exc}") from exc
