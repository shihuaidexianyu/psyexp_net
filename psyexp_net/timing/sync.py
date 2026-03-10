from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

"""时间同步估计工具。"""


@dataclass(slots=True)
class SyncSample:
    rtt_ms: float
    offset_ms: float


@dataclass(slots=True)
class SyncEstimator:
    samples: list[SyncSample] = field(default_factory=list)

    def add_sample(self, *, t0: float, t1: float, t2: float, t3: float) -> SyncSample:
        # NTP 风格估计：
        # RTT = (t3 - t0) - (t2 - t1)
        # offset = ((t1 - t0) + (t2 - t3)) / 2
        rtt_ms = (t3 - t0 - (t2 - t1)) * 1000
        offset_ms = ((t1 - t0) + (t2 - t3)) * 500
        sample = SyncSample(rtt_ms=rtt_ms, offset_ms=offset_ms)
        self.samples.append(sample)
        return sample

    @property
    def average_offset_ms(self) -> float:
        if not self.samples:
            return 0.0
        return mean(item.offset_ms for item in self.samples)
