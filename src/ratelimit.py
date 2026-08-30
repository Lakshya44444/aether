"""Per-caller token bucket.

Detection is regex-bound and linear in input length, and a body may be 100 KB, so an
unlimited caller can exhaust the CPU even with a valid key. Authentication answers
"who", not "how much".

Keyed by API key when one is presented, by client IP otherwise, so one caller's burst
cannot starve another's.

ponytail: process-local, so N workers permit N times the configured rate. That is the
correct trade at this size -- the cap exists to stop one caller monopolising a process,
and the proxy in front is the right place for a global limit. Move the buckets into
Redis if the number has to mean something fleet-wide.
"""
import time
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class _Bucket:
    tokens: float
    last: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Token bucket: `per_minute` sustained, `burst` in hand."""

    def __init__(self, per_minute: int, burst: int) -> None:
        self.rate_per_s = per_minute / 60.0
        self.burst = max(1, burst)
        self.enabled = per_minute > 0
        self._buckets: Dict[str, _Bucket] = {}

    def _evict(self, now: float) -> None:
        # A bucket that has been full for a minute is indistinguishable from a caller
        # that has never been seen, so it can be dropped rather than retained forever.
        if len(self._buckets) < 10_000:
            return
        idle_since = now - 60
        for key in [k for k, b in self._buckets.items() if b.last < idle_since]:
            del self._buckets[key]

    def allow(self, identity: str) -> bool:
        if not self.enabled:
            return True

        now = time.monotonic()
        self._evict(now)
        bucket = self._buckets.get(identity)
        if bucket is None:
            self._buckets[identity] = _Bucket(tokens=self.burst - 1)
            return True

        bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.last) * self.rate_per_s)
        bucket.last = now
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True

    def retry_after_s(self) -> int:
        """Seconds until one token is back, for the Retry-After header."""
        return max(1, int(1 / self.rate_per_s)) if self.rate_per_s else 1
