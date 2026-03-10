from __future__ import annotations

from statistics import pstdev

"""抖动统计工具。"""


def compute_jitter_ms(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    return pstdev(values)
