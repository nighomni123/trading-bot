"""Retry-After, bounded jitter, and duplicate-decision tests."""
from __future__ import annotations

import requests

from jev_trading.live_intelligence.provider_errors import (
    next_retry_delay,
    retry_after_seconds,
    retryable_request_error,
    classify_request_error,
)
from jev_trading.live_intelligence.frontier.client import FrontierUnavailable, OpenAICompatibleFrontierClient
import pytest


def _http_error(status, headers=None):
    response = requests.Response()
    response.status_code = status
    if headers:
        response.headers.update(headers)
    return requests.HTTPError(response=response)


def test_retry_classification_separates_retryable_and_permanent():
    assert classify_request_error(_http_error(429)) == "rate_limit"
    assert retryable_request_error(_http_error(429)) is True
    assert retryable_request_error(_http_error(503)) is True
    assert retryable_request_error(requests.Timeout()) is True
    assert classify_request_error(_http_error(401)) == "authentication"
    assert classify_request_error(_http_error(400)) == "invalid_request"
    assert retryable_request_error(_http_error(401)) is False
    assert retryable_request_error(_http_error(422)) is False


def test_retry_after_header_is_honoured_and_capped():
    exc = _http_error(429, {"Retry-After": "3"})
    assert retry_after_seconds(exc) == 3.0
    delay, retry_after = next_retry_delay(exc, attempt=0, base=0.5, cap=2.0)
    assert retry_after == 3.0
    assert delay == 2.0  # cap wins over a hostile header


def test_exponential_backoff_is_bounded_and_jittered():
    for attempt in range(10):
        delay, retry_after = next_retry_delay(_http_error(500), attempt, base=0.5, cap=5.0)
        assert retry_after is None
        assert 0.5 <= delay <= 5.0 * 1.25


def test_retry_after_final_failure_records_telemetry(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        raise _http_error(429, {"Retry-After": "0"})

    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", post)
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", max_retries=2,
        retry_backoff_seconds=0, max_retry_delay_seconds=1,
    )
    with pytest.raises(FrontierUnavailable) as error:
        client.complete(system_prompt="s", payload={"request_id": "r"})
    exc = error.value
    assert exc.category == "rate_limit"
    assert exc.http_status == 429
    assert exc.retry_count == 3  # bounded: 1 initial + 2 retries
    assert exc.retry_after == 0.0
    assert exc.chosen_delay is not None
    assert len(calls) == 3


def test_retry_never_produces_two_decisions(monkeypatch):
    """A retried request returns exactly one decision, never a second one."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    state = {"calls": 0}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    def post(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] < 3:
            raise _http_error(503)
        return Response()

    monkeypatch.setattr("jev_trading.live_intelligence.frontier.client.requests.post", post)
    client = OpenAICompatibleFrontierClient(
        base_url="https://example.invalid/v1", model="test/model", max_retries=2, retry_backoff_seconds=0,
    )
    first = client.complete(system_prompt="s", payload={"request_id": "r"})
    assert first == {"ok": True}
    assert state["calls"] == 3
