"""Structured, secret-safe provider failure metadata."""
from __future__ import annotations

import random

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
        retry_after: float | None = None,
        chosen_delay: float | None = None,
    ) -> None:
        super().__init__(message)
        self.component = component
        self.provider = provider
        self.model = model
        self.category = category
        self.http_status = http_status
        self.retry_count = retry_count
        self.request_id = request_id
        self.retry_after = retry_after
        self.chosen_delay = chosen_delay


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


def retry_after_seconds(exc: Exception) -> float | None:
    """Honour a provider-supplied Retry-After header (seconds form)."""
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return None
    raw = exc.response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def next_retry_delay(exc: Exception, attempt: int, base: float, cap: float) -> tuple[float, float | None]:
    """Bounded backoff with jitter; a provider Retry-After wins when present."""
    retry_after = retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, cap), retry_after
    exponential = min(cap, base * (2 ** attempt))
    return exponential * (1.0 + random.uniform(0.0, 0.25)), None
