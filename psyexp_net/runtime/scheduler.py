from __future__ import annotations

import time

"""将用户输入的执行时间转换为绝对单调时钟。"""


def resolve_execute_at(value: str | float | None, *, lead_time_ms: int) -> float:
    now = time.monotonic()
    if value is None:
        return now + lead_time_ms / 1000
    if isinstance(value, float):
        return value
    if value.startswith("+") and value.endswith("ms"):
        return now + float(value[1:-2]) / 1000
    if value.startswith("+") and value.endswith("s"):
        return now + float(value[1:-1])
    return float(value)
