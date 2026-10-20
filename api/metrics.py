"""RED metrics (rate, errors, duration) per endpoint in Prometheus text format (docs/12 §3, REQ-N-07).

Dependency-free on purpose. Labels use the ROUTE TEMPLATE (`/api/v1/recipes/{recipe_id}`), never the raw path, so recipe
ids cannot explode label cardinality.
"""

from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS_S = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[tuple[str, str, str], int] = defaultdict(int)
        self._hist: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS_S) + 1))
        self._sum: dict[tuple[str, str], float] = defaultdict(float)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self._requests[(method, route, str(status))] += 1
            counts = self._hist[(method, route)]
            for i, b in enumerate(BUCKETS_S):
                if seconds <= b:
                    counts[i] += 1
            counts[-1] += 1                                   # +Inf
            self._sum[(method, route)] += seconds

    def render(self, gauges: dict[str, tuple[float, dict[str, str]]]) -> str:
        out = ["# HELP http_requests_total Requests by method, route template and status.",
               "# TYPE http_requests_total counter"]
        with self._lock:
            for (m, r, s), n in sorted(self._requests.items()):
                out.append(f'http_requests_total{{method="{m}",route="{r}",status="{s}"}} {n}')
            out += ["# HELP http_request_duration_seconds Request latency by route template.",
                    "# TYPE http_request_duration_seconds histogram"]
            for (m, r), counts in sorted(self._hist.items()):
                for b, c in zip((*map(str, BUCKETS_S), "+Inf"), counts, strict=True):
                    out.append(f'http_request_duration_seconds_bucket{{method="{m}",route="{r}",le="{b}"}} {c}')
                out.append(f'http_request_duration_seconds_sum{{method="{m}",route="{r}"}} {self._sum[(m, r)]:.6f}')
                out.append(f'http_request_duration_seconds_count{{method="{m}",route="{r}"}} {counts[-1]}')
        for name, (value, labels) in sorted(gauges.items()):
            lbl = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
            out.append(f"{name}{{{lbl}}} {value}" if lbl else f"{name} {value}")
        return "\n".join(out) + "\n"
