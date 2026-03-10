from __future__ import annotations

from collections import deque
from statistics import mean

"""滚动延迟统计。"""


class RollingLatencyStats:
    def __init__(self, max_size: int = 128) -> None:
        self.values: deque[float] = deque(maxlen=max_size)

    def add(self, value_ms: float) -> None:
        self.values.append(value_ms)

    def summary(self) -> dict[str, float]:
        if not self.values:
            return {"mean_ms": 0.0, "max_ms": 0.0}
        return {"mean_ms": mean(self.values), "max_ms": max(self.values)}
