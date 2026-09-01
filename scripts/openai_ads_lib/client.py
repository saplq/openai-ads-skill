"""Minimal stdlib HTTP client with bounded retries and pagination."""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path

from .errors import AdsManagerError, ValidationError, for_status
from .security import redact

ADS_BASE_URL = "https://api.ads.openai.com/v1"
CAPI_BASE_URL = "https://bzr.openai.com/v1"
Transport = Callable[[urllib.request.Request, float], tuple[int, dict[str, str], bytes]]


@dataclass
class ApiResponse:
    status: int
    data: Any
    headers: dict[str, str]
    request_id: str | None
    pages: int = 1


def default_transport(request: urllib.request.Request, timeout: float) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


class AdsClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = ADS_BASE_URL,
        timeout: float = 30.0,
        transport: Transport = default_transport,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ):
        if base_url not in {ADS_BASE_URL, CAPI_BASE_URL}:
            raise ValidationError("Only fixed OpenAI Ads hosts are allowed")
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.transport = transport
        self.sleep = sleep
        self.max_retries = max_retries

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        idempotency_key: str | None = None,
        all_pages: bool = False,
        extra_headers: dict[str, str] | None = None,
        sensitive_response: bool = False,
    ) -> ApiResponse:
        method = method.upper()
        if not path.startswith("/") or "://" in path or ".." in path:
            raise ValidationError("API request path must be relative to the fixed host")
        if all_pages and method != "GET":
            raise ValidationError("--all-pages is only valid for GET requests")
        first = self._single(
            method, path, query=query, body=body, idempotency_key=idempotency_key,
            extra_headers=extra_headers, sensitive_response=sensitive_response,
        )
        if not all_pages:
            return first
        combined = self._items(first.data)
        pages = 1
        current = first
        next_query = dict(query or {})
        seen: set[str] = set()
        while self._has_more(current.data):
            cursor = self._next_cursor(current.data)
            if not cursor or cursor in seen:
                raise AdsManagerError("Pagination indicated more data but did not provide a new cursor")
            seen.add(cursor)
            next_query["after"] = cursor
            current = self._single(
                method, path, query=next_query, idempotency_key=idempotency_key,
                extra_headers=extra_headers, sensitive_response=sensitive_response,
            )
            combined.extend(self._items(current.data))
            pages += 1
        data = dict(first.data) if isinstance(first.data, dict) else {}
        data["data"] = combined
        data["has_more"] = False
        return ApiResponse(first.status, data, current.headers, current.request_id, pages=pages)

    def upload_file(
        self,
        path: str,
        file_path: Path,
        *,
        purpose: str | None = None,
        idempotency_key: str | None = None,
    ) -> ApiResponse:
        if path not in {"/upload", "/uploads"}:
            raise ValidationError("Binary upload is allowed only for /upload or /uploads")
        if file_path.is_symlink() or not file_path.is_file():
            raise ValidationError("Upload input must be a regular non-symlink file")
        boundary = "----openai-ads-manager-" + hashlib.sha256(os.urandom(32)).hexdigest()[:24]
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        body = MultipartBody(boundary, file_path, content_type, purpose)
        return self._single(
            "POST", path, idempotency_key=idempotency_key,
            raw_body=body, content_type=f"multipart/form-data; boundary={boundary}", content_length=body.length,
        )

    def _single(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        idempotency_key: str | None = None,
        extra_headers: dict[str, str] | None = None,
        sensitive_response: bool = False,
        raw_body: Any = None,
        content_type: str | None = None,
        content_length: int | None = None,
    ) -> ApiResponse:
        encoded_query = urllib.parse.urlencode(query or {}, doseq=True)
        url = self.base_url + path + (("?" + encoded_query) if encoded_query else "")
        if raw_body is None and body is not None:
            raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json", "User-Agent": "openai-ads-manager/0.1.0"}
        if raw_body is not None:
            headers["Content-Type"] = content_type or "application/json"
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        may_retry = method in {"GET", "HEAD", "OPTIONS"} or bool(idempotency_key)
        attempts = self.max_retries + 1 if may_retry else 1
        last_error: AdsManagerError | None = None
        for attempt in range(attempts):
            request = urllib.request.Request(url, data=raw_body, headers=headers, method=method)
            try:
                status, response_headers, payload = self.transport(request, self.timeout)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                last_error = AdsManagerError(f"Network error after safe redaction: {type(exc).__name__}")
                if attempt + 1 >= attempts:
                    raise last_error from exc
                self._sleep(attempt, {})
                continue
            parsed = self._parse(payload)
            request_id = self._header(response_headers, "x-request-id") or self._header(response_headers, "request-id")
            if 200 <= status < 300:
                return ApiResponse(status, parsed if sensitive_response else redact(parsed), response_headers, request_id)
            message = self._message(parsed, status)
            last_error = for_status(status, message, redact(parsed))
            if status not in {429, 500, 502, 503, 504} or attempt + 1 >= attempts:
                raise last_error
            self._sleep(attempt, response_headers)
        assert last_error is not None
        raise last_error

    def _sleep(self, attempt: int, headers: dict[str, str]) -> None:
        retry_after = self._header(headers, "retry-after")
        try:
            delay = min(float(retry_after), 60.0) if retry_after else min((2**attempt) + random.random(), 10.0)
        except ValueError:
            delay = min((2**attempt) + random.random(), 10.0)
        self.sleep(delay)

    @staticmethod
    def _header(headers: dict[str, str], name: str) -> str | None:
        return next((str(v) for k, v in headers.items() if k.lower() == name.lower()), None)

    @staticmethod
    def _parse(payload: bytes) -> Any:
        if not payload:
            return {}
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"message": "API returned a non-JSON response"}

    @staticmethod
    def _message(payload: Any, status: int) -> str:
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return str(error.get("message") or error.get("type") or f"Ads API error {status}")
            if isinstance(error, str):
                return error
            if payload.get("message"):
                return str(payload["message"])
        return f"Ads API error {status}"

    @staticmethod
    def _items(payload: Any) -> list[Any]:
        if isinstance(payload, dict):
            for key in ("data", "items", "results"):
                if isinstance(payload.get(key), list):
                    return list(payload[key])
        if isinstance(payload, list):
            return list(payload)
        return []

    @staticmethod
    def _has_more(payload: Any) -> bool:
        return bool(payload.get("has_more")) if isinstance(payload, dict) else False

    @staticmethod
    def _next_cursor(payload: Any) -> str | None:
        if not isinstance(payload, dict):
            return None
        paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
        return str(payload.get("last_id") or payload.get("next_cursor") or paging.get("next_cursor") or "") or None


class MultipartBody:
    """Re-iterable multipart stream; large audience files are never loaded into memory."""

    def __init__(self, boundary: str, path: Path, content_type: str, purpose: str | None):
        self.path = path
        fields = b""
        if purpose:
            fields = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n"
                f"{purpose}\r\n"
            ).encode("utf-8")
        safe_name = path.name.replace('"', "")
        self.prefix = fields + (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe_name}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
        self.suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
        self.length = len(self.prefix) + path.stat().st_size + len(self.suffix)

    def __iter__(self):
        yield self.prefix
        with self.path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                yield chunk
        yield self.suffix


def capability_enabled(account: Any, feature: str) -> bool:
    """Fail closed unless the live account response explicitly advertises the feature."""
    if not isinstance(account, dict):
        return False
    candidates = [account.get("capabilities"), account.get("features"), account.get("api_features")]
    aliases = {feature, feature.replace("_preview", ""), feature.replace("_", "-")}
    for value in candidates:
        if isinstance(value, list) and any(str(item).lower() in aliases for item in value):
            return True
        if isinstance(value, dict):
            for alias in aliases:
                enabled = value.get(alias)
                if enabled is True or str(enabled).lower() in {"enabled", "active", "true"}:
                    return True
    return False
