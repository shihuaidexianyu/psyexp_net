from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, pstdev
from typing import DefaultDict

"""简单指标采集器。"""


class MetricsCollector:
    def __init__(self) -> None:
        self.counters: Counter[str] = Counter()
        self.samples: DefaultDict[str, list[float]] = defaultdict(list)

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        self.samples[name].append(value)

    def snapshot(self) -> dict[str, float | int]:
        # 对采样型指标输出均值、P95、最大值和标准差，方便日志或 CLI 展示。
        data: dict[str, float | int] = dict(self.counters)
        for key, values in self.samples.items():
            data[f"{key}_mean"] = mean(values) if values else 0.0
            data[f"{key}_max"] = max(values) if values else 0.0
            data[f"{key}_p95"] = self._percentile(values, 0.95) if values else 0.0
            data[f"{key}_stddev"] = pstdev(values) if len(values) > 1 else 0.0
        return data

    def _percentile(self, values: list[float], ratio: float) -> float:
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * ratio)))
        return ordered[index]
