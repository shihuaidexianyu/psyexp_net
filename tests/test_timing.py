from __future__ import annotations

import time
import unittest

from psyexp_net.runtime.scheduler import resolve_execute_at
from psyexp_net.timing.jitter import compute_jitter_ms
from psyexp_net.timing.sync import SyncEstimator

"""时间相关工具测试。"""


class TimingTests(unittest.TestCase):
    def test_sync_estimator(self) -> None:
        estimator = SyncEstimator()
        sample = estimator.add_sample(t0=10.0, t1=10.01, t2=10.02, t3=10.03)
        self.assertAlmostEqual(sample.rtt_ms, 20.0, places=6)
        self.assertAlmostEqual(sample.offset_ms, 0.0, places=6)

    def test_scheduler_relative_ms(self) -> None:
        before = time.monotonic()
        execute_at = resolve_execute_at("+50ms", lead_time_ms=120)
        self.assertGreaterEqual(execute_at, before + 0.045)

    def test_jitter(self) -> None:
        jitter = compute_jitter_ms([10.0, 12.0, 14.0, 16.0])
        self.assertGreater(jitter, 0.0)


if __name__ == "__main__":
    unittest.main()
