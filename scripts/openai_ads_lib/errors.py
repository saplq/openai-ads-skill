"""Stable, secret-safe error types for the CLI."""

from __future__ import annotations


class AdsManagerError(Exception):
    category = "api"
    exit_code = 1

    def __init__(self, message: str, *, status: int | None = None, details: object = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details


class AuthError(AdsManagerError):
    category = "auth"
    exit_code = 2


class ValidationError(AdsManagerError):
    category = "validation"
    exit_code = 2


class PolicyError(AdsManagerError):
    category = "policy"
    exit_code = 3


class ConflictError(AdsManagerError):
    category = "conflict"
    exit_code = 4


class RateLimitError(AdsManagerError):
    category = "rate_limit"
    exit_code = 5


def for_status(status: int, message: str, details: object = None) -> AdsManagerError:
    if status in (401, 403):
        return AuthError(message, status=status, details=details)
    if status in (400, 404, 422):
        return ValidationError(message, status=status, details=details)
    if status == 409:
        return ConflictError(message, status=status, details=details)
    if status == 429:
        return RateLimitError(message, status=status, details=details)
    return AdsManagerError(message, status=status, details=details)
