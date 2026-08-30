"""Structured logging and Prometheus metrics.

Two things an operator needs that this gateway had neither of.

**Logs are JSON on stdout.** A container's log driver ships stdout; anything that
parses logs wants one object per line. `log_event` takes the fields rather than a
formatted string, so a field can be filtered on rather than grepped for.

**Metrics are the Prometheus text format, rendered from counters kept here.** No
client library: the exposition format is a dozen lines to emit and a dependency that
has to be kept current is not worth it for that. `prometheus_client` is the upgrade
if histograms with real bucket boundaries start mattering.

The metric that matters most is `aether_detector_failures_total`. A detector that
times out silently changes every decision through the policy's fail_mode, so a rise
here is a change in what the gateway is doing, not just a performance problem.

ponytail: counters are per process. With `--workers 4` behind one port a scrape lands
on whichever worker answers, so the numbers are one worker's view rather than the
service's. Fine for rates and for spotting a detector failing; wrong if you need exact
totals. The upgrade is `prometheus_client` in multiprocess mode over a shared
directory, or scraping each worker on its own port.
"""
import json
import logging
import sys
import threading
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable

_START = time.time()


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with whatever `extra` the call site attached."""

    _BUILTIN = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
        "asctime", "message", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._BUILTIN and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Replaces the root handlers so uvicorn's lines are JSON too."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; without this every access line is logged twice,
    # once as JSON and once as uvicorn's own text.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields):
    logger.log(level, event, extra=fields)


# ── metrics ──────────────────────────────────────────────────────────────────

# Latency buckets in milliseconds. Chosen around the policy budgets (300/500/1000 ms)
# so a histogram query can answer "are we inside budget" directly.
_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


class Metrics:
    """Process-wide counters. Cheap enough to update on every request.

    Guarded by a lock because a worker thread can finish a detector while the event
    loop is serving another request, and `+=` on a dict entry is not atomic under
    free-threaded builds.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.decisions: Counter = Counter()
        self.detector_failures: Counter = Counter()
        self.requests: Counter = Counter()
        self.rate_limited = 0
        self.unauthorized = 0
        self._latency_counts: Dict[str, Counter] = defaultdict(Counter)
        self._latency_sum: Dict[str, float] = defaultdict(float)
        self._latency_total: Counter = Counter()

    def observe_decision(self, use_case: str, decision: str, latency_ms: float) -> None:
        with self._lock:
            self.decisions[(use_case, decision)] += 1
            self._latency_sum[use_case] += latency_ms
            self._latency_total[use_case] += 1
            for bound in _BUCKETS_MS:
                if latency_ms <= bound:
                    self._latency_counts[use_case][bound] += 1

    def observe_detector_failures(self, names: Iterable[str]) -> None:
        with self._lock:
            for name in names:
                # "privacy (timeout)" and "privacy" are the same detector failing two
                # ways; the reason belongs in a label, not in the metric name.
                detector, _, reason = name.partition(" ")
                self.detector_failures[(detector, reason.strip("()") or "error")] += 1

    def observe_request(self, path: str, status: int) -> None:
        with self._lock:
            self.requests[(path, status)] += 1
            if status == 429:
                self.rate_limited += 1
            elif status == 401:
                self.unauthorized += 1

    def render(self) -> str:
        """Prometheus text exposition format."""
        with self._lock:
            lines = [
                "# HELP aether_uptime_seconds Seconds since this process started.",
                "# TYPE aether_uptime_seconds gauge",
                f"aether_uptime_seconds {time.time() - _START:.1f}",
                "# HELP aether_decisions_total Evaluations by use case and decision.",
                "# TYPE aether_decisions_total counter",
            ]
            for (use_case, decision), count in sorted(self.decisions.items()):
                lines.append(
                    f'aether_decisions_total{{use_case="{use_case}",'
                    f'decision="{decision}"}} {count}'
                )

            lines += [
                "# HELP aether_detector_failures_total Detectors that raised or timed "
                "out. Each one hands the decision to the policy's fail_mode.",
                "# TYPE aether_detector_failures_total counter",
            ]
            for (detector, reason), count in sorted(self.detector_failures.items()):
                lines.append(
                    f'aether_detector_failures_total{{detector="{detector}",'
                    f'reason="{reason}"}} {count}'
                )

            lines += [
                "# HELP aether_http_requests_total HTTP responses by path and status.",
                "# TYPE aether_http_requests_total counter",
            ]
            for (path, status), count in sorted(self.requests.items()):
                lines.append(
                    f'aether_http_requests_total{{path="{path}",status="{status}"}} {count}'
                )

            lines += [
                "# HELP aether_evaluate_latency_ms End-to-end pipeline latency.",
                "# TYPE aether_evaluate_latency_ms histogram",
            ]
            for use_case in sorted(self._latency_total):
                cumulative = 0
                for bound in _BUCKETS_MS:
                    cumulative = self._latency_counts[use_case][bound]
                    lines.append(
                        f'aether_evaluate_latency_ms_bucket{{use_case="{use_case}",'
                        f'le="{bound}"}} {cumulative}'
                    )
                total = self._latency_total[use_case]
                lines.append(
                    f'aether_evaluate_latency_ms_bucket{{use_case="{use_case}",'
                    f'le="+Inf"}} {total}'
                )
                lines.append(
                    f'aether_evaluate_latency_ms_sum{{use_case="{use_case}"}} '
                    f'{self._latency_sum[use_case]:.1f}'
                )
                lines.append(
                    f'aether_evaluate_latency_ms_count{{use_case="{use_case}"}} {total}'
                )
        return "\n".join(lines) + "\n"


metrics = Metrics()
