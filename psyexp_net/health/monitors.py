from __future__ import annotations

from dataclasses import dataclass

from psyexp_net.config import TimingConfig

"""健康状态评估。"""


@dataclass(slots=True)
class HealthSnapshot:
    degraded: bool
    reasons: list[str]


class HealthMonitor:
    def __init__(self, config: TimingConfig) -> None:
        self.config = config

    def evaluate(
        self, *, rtt_ms: float, jitter_ms: float, offset_ms: float
    ) -> HealthSnapshot:
        # 只要任一关键指标越界，就标记为 degraded。
        reasons: list[str] = []
        if rtt_ms > self.config.max_rtt_ms:
            reasons.append("rtt")
        if jitter_ms > self.config.max_jitter_ms:
            reasons.append("jitter")
        if abs(offset_ms) > self.config.max_clock_offset_ms:
            reasons.append("clock_offset")
        return HealthSnapshot(degraded=bool(reasons), reasons=reasons)
