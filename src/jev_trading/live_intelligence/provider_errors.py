"""Structured, secret-safe provider failure metadata."""
from __future__ import annotations

import requests


class ProviderFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        component: str = "",
        provider: str = "",
        model: str = "",
        category: str = "unknown",
        http_status: int | None = None,
        retry_count: int = 0,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.component = component
        self.provider = provider
        self.model = model
        self.category = category
        self.http_status = http_status
        self.retry_count = retry_count
        self.request_id = request_id


def classify_request_error(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout | requests.ConnectionError):
        return "transport"
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status in {401, 403}:
            return "authentication"
        if status == 429:
            return "rate_limit"
        if status in {400, 404, 422}:
            return "invalid_request"
        if status is not None and status >= 500:
            return "provider"
        return "http_error"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "validation"
    return "unknown"


def retryable_request_error(exc: Exception) -> bool:
    category = classify_request_error(exc)
    if category in {"transport", "rate_limit", "provider"}:
        return True
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and status >= 500
    return False


def http_status(exc: Exception) -> int | None:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None
