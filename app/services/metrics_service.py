from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from time import perf_counter


DEFAULT_DURATION_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsService:
    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: defaultdict[str, int] = defaultdict(int)
        self._histograms: dict[str, dict[str, object]] = {}

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_duration(
        self,
        name: str,
        duration_seconds: float,
        buckets: tuple[float, ...] = DEFAULT_DURATION_BUCKETS,
    ) -> None:
        if duration_seconds < 0:
            duration_seconds = 0.0

        with self._lock:
            histogram = self._histograms.get(name)
            if histogram is None:
                histogram = {
                    "buckets": {str(boundary): 0 for boundary in buckets},
                    "+Inf": 0,
                    "count": 0,
                    "sum": 0.0,
                }
                self._histograms[name] = histogram

            bucket_counts = histogram["buckets"]
            for boundary in buckets:
                if duration_seconds <= boundary:
                    bucket_counts[str(boundary)] += 1

            histogram["+Inf"] += 1
            histogram["count"] += 1
            histogram["sum"] += duration_seconds

    @contextmanager
    def time(self, name: str, buckets: tuple[float, ...] = DEFAULT_DURATION_BUCKETS):
        started_at = perf_counter()
        try:
            yield
        finally:
            self.observe_duration(name, perf_counter() - started_at, buckets=buckets)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counters)

    def histogram_snapshot(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {
                metric_name: {
                    "buckets": dict(values["buckets"]),
                    "+Inf": values["+Inf"],
                    "count": values["count"],
                    "sum": values["sum"],
                }
                for metric_name, values in self._histograms.items()
            }

    def full_snapshot(self) -> dict[str, object]:
        return {
            "counters": self.snapshot(),
            "histograms": self.histogram_snapshot(),
        }


metrics_service = MetricsService()
