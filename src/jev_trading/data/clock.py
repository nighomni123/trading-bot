"""Venue clock synchronization for exchange event timestamps.

An exchange stamps events with *its* wall clock. Comparing that to a local
machine clock measures two unknowns at once: genuine clock skew, and transport
latency. This module separates them with the standard midpoint estimator::

    t0 = local wall clock
    server = venue server time
    t1 = local wall clock
    rtt = t1 - t0
    offset = server - (t0 + t1) / 2

``offset`` is corrected out of every incoming event timestamp; ``rtt / 2`` is the
irreducible uncertainty of that correction, so the caller can fail closed when
the link is too slow to be sure.

The calibration is bounded on purpose. A clock mechanism that can silently move
event times backwards would be a look-ahead leak, so an implausible or unstable
calibration disables correction entirely and the downstream future-dated guard
does exactly what it did before.
"""
from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

# Bounds are defaults, not policy: they are configuration, they are recorded in
# the resolved config hash, and they are sized from measured venue links
# (Binance ~208 ms median RTT, Bybit ~573 ms) so a healthy link passes and a
# degraded one fails closed.
DEFAULT_MAX_OFFSET_MS = 2_000
DEFAULT_MAX_UNCERTAINTY_MS = 600
DEFAULT_REFRESH_SECONDS = 60.0
DEFAULT_BOOTSTRAP_SAMPLES = 5
DEFAULT_RETAIN_SAMPLES = 10
# When most samples agree exactly, the MAD is 0 and the trim would keep a
# genuinely wild reading; this floor gives the rule something to bite on.
_MAD_FLOOR_MS = 50
# Short pause between bootstrap requests. Long enough not to hammer the venue,
# short enough that the feed becomes usable within a couple of seconds.
_BOOTSTRAP_GAP_SECONDS = 0.25

ClockStatus = str  # "UNSYNCED" | "HEALTHY" | "UNHEALTHY"


@dataclass(frozen=True)
class ClockSample:
    """One midpoint measurement of venue-minus-local wall-clock offset."""

    offset_ms: int
    rtt_ms: int
    observed_at: float


def midpoint_offset(server_ms: int, t0_ms: int, t1_ms: int) -> tuple[int, int]:
    """Return ``(offset_ms, rtt_ms)`` for a single bracketed request."""
    rtt = max(0, t1_ms - t0_ms)
    midpoint = (t0_ms + t1_ms) // 2
    return int(server_ms) - midpoint, rtt


class VenueClock:
    """Robust estimator of one venue's offset from the local wall clock.

    Uses a median over a retained window rather than a mean, because a cold
    connection can be seconds slower than a warm one and a single such sample
    must not move the calibration. Transport timing (``WebSocketRunner``) stays
    on ``time.monotonic``; only venue comparison uses wall clock.
    """

    version = "venue-clock-v1"

    def __init__(
        self,
        venue: str,
        time_url: str,
        extract_ms: Callable[[dict[str, Any]], int],
        *,
        max_offset_ms: int = DEFAULT_MAX_OFFSET_MS,
        max_uncertainty_ms: int = DEFAULT_MAX_UNCERTAINTY_MS,
        refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
        bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
        retain_samples: int = DEFAULT_RETAIN_SAMPLES,
        fetch: Callable[[str], dict[str, Any]] | None = None,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.venue = venue
        self.time_url = time_url
        self.extract_ms = extract_ms
        self.max_offset_ms = max_offset_ms
        self.max_uncertainty_ms = max_uncertainty_ms
        self.refresh_seconds = refresh_seconds
        self.bootstrap_samples = bootstrap_samples
        self.retain_samples = retain_samples
        self._fetch = fetch or _http_get_json
        self._wall = wall

        self._lock = threading.Lock()
        self._samples: list[ClockSample] = []
        self._offset_ms: int | None = None
        self._uncertainty_ms: int | None = None
        self._status: ClockStatus = "UNSYNCED"
        self._reason = "not_calibrated"
        self._last_error: str | None = None
        self._last_sync: float | None = None
        self._max_observed_offset_ms: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- sampling

    def sample_once(self) -> ClockSample | None:
        """Take one midpoint measurement. A failure returns None, never raises."""
        try:
            t0 = int(self._wall() * 1000)
            payload = self._fetch(self.time_url)
            t1 = int(self._wall() * 1000)
            offset_ms, rtt_ms = midpoint_offset(self.extract_ms(payload), t0, t1)
        except Exception as exc:  # network/parse failures must not kill the feed
            with self._lock:
                self._last_error = f"{type(exc).__name__}: {exc}"[:200]
            return None
        sample = ClockSample(offset_ms=offset_ms, rtt_ms=rtt_ms, observed_at=t1 / 1000)
        self._ingest(sample)
        return sample

    def _ingest(self, sample: ClockSample) -> None:
        with self._lock:
            self._samples.append(sample)
            if len(self._samples) > self.retain_samples:
                self._samples = self._samples[-self.retain_samples:]
            self._last_error = None
            self._last_sync = sample.observed_at
            self._recompute()

    def _recompute(self) -> None:
        """Median offset, outlier-trimmed; uncertainty is sampling spread plus
        the tightest midpoint bound the link can justify."""
        if len(self._samples) < self.bootstrap_samples:
            self._status = "UNSYNCED"
            self._reason = f"insufficient_samples:{len(self._samples)}"
            self._offset_ms = None
            self._uncertainty_ms = None
            return

        offsets = [sample.offset_ms for sample in self._samples]
        median_offset = int(statistics.median(offsets))
        deviations = [abs(value - median_offset) for value in offsets]
        mad = int(statistics.median(deviations))
        # Two different questions need two different bounds. `max_offset_ms` asks
        # "is this whole calibration plausible?"; the MAD rule asks "is this one
        # sample a glitch?". Reusing the first for the second would let a wild
        # reading into the window and only rely on the median to survive it.
        trim = min(self.max_offset_ms, max(3 * mad, _MAD_FLOOR_MS))
        retained = [
            sample
            for sample, deviation in zip(self._samples, deviations)
            if deviation <= trim
        ]
        if len(retained) < self.bootstrap_samples:
            self._status = "UNHEALTHY"
            self._reason = "offset_samples_disagree"
            self._offset_ms = None
            self._uncertainty_ms = None
            return

        retained_offsets = [sample.offset_ms for sample in retained]
        offset = int(statistics.median(retained_offsets))
        spread = int(
            statistics.median([abs(value - offset) for value in retained_offsets])
        )
        # min(rtt) is the tightest midpoint bound the link can justify; NTP
        # practice, because a fast sample carries the least ambiguity.
        uncertainty = spread + min(sample.rtt_ms for sample in retained) // 2

        if abs(offset) > self.max_offset_ms:
            self._status, self._reason = "UNHEALTHY", "offset_out_of_bounds"
            self._offset_ms, self._uncertainty_ms = offset, uncertainty
            return
        if uncertainty > self.max_uncertainty_ms:
            self._status, self._reason = "UNHEALTHY", "uncertainty_above_maximum"
            self._offset_ms, self._uncertainty_ms = offset, uncertainty
            return

        self._status, self._reason = "HEALTHY", "ok"
        self._offset_ms, self._uncertainty_ms = offset, uncertainty
        self._max_observed_offset_ms = max(
            abs(value) for value in [*offsets, self._max_observed_offset_ms or 0]
        )

    def calibrate(self, samples: int | None = None) -> bool:
        """Block until calibrated (or out of attempts). Used at startup only."""
        target = samples or self.bootstrap_samples
        for _ in range(target * 2):
            if self.healthy:
                return True
            if self.sample_once() is None:
                continue
        return self.healthy

    # ------------------------------------------------------------------ public

    @property
    def healthy(self) -> bool:
        with self._lock:
            return self._status == "HEALTHY"

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def offset_ms(self) -> int | None:
        with self._lock:
            return self._offset_ms

    @property
    def uncertainty_ms(self) -> int | None:
        with self._lock:
            return self._uncertainty_ms

    def correct(self, raw_ms: int | None) -> int | None:
        """Return the locally-comparable event time.

        A no-op when the calibration is missing or untrustworthy: a wrong
        correction would be a look-ahead leak, so the future-dated guard in the
        caller keeps rejecting the feed exactly as it does today.
        """
        if raw_ms is None:
            return None
        with self._lock:
            if self._status != "HEALTHY" or self._offset_ms is None:
                return raw_ms
            return raw_ms - self._offset_ms

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "venue": self.venue,
                "status": self._status,
                "reason": self._reason,
                "offset_ms": self._offset_ms,
                "uncertainty_ms": self._uncertainty_ms,
                "samples": len(self._samples),
                "max_observed_offset_ms": self._max_observed_offset_ms,
                "last_sync": self._last_sync,
                "last_error": self._last_error,
                "max_offset_ms": self.max_offset_ms,
                "max_uncertainty_ms": self.max_uncertainty_ms,
            }

    # --------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Refresh the estimate in the background; never blocks the feed."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"jev-clock-{self.venue}", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def _run(self) -> None:
        # Bootstrap in a burst. Waiting `refresh_seconds` between each of the
        # bootstrap samples would leave the feed unusable for minutes at
        # startup, which is the whole outage this is meant to end.
        for _ in range(self.bootstrap_samples * 2):
            if self._stop.is_set() or self.healthy:
                break
            self.sample_once()
            if not self.healthy:
                self._stop.wait(_BOOTSTRAP_GAP_SECONDS)
        while not self._stop.is_set():
            if self._stop.wait(self.refresh_seconds):
                return
            self.sample_once()


def _http_get_json(url: str) -> dict[str, Any]:
    import json
    import urllib.request

    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read())
