import os
import platform
import time
import unittest

from tools.op017_performance_baseline import (
    build_query_model,
    measure_controlled_state_sql,
    percentile,
    WindowsProcessSampler,
)


class PerformanceBaselineTests(unittest.TestCase):
    def test_percentile_interpolates(self):
        self.assertEqual(percentile([1, 2, 3, 4, 5], 50), 3)
        self.assertAlmostEqual(percentile([1, 2, 3, 4], 95), 3.85)

    def test_controlled_state_sql_budget(self):
        result = measure_controlled_state_sql(samples=30)
        self.assertEqual(
            result["sql_statements_per_channel"],
            {
                "cold_cache": 4,
                "warm_cache_not_due": 0,
                "persistence_due": 2,
            },
        )

    def test_two_channel_query_model_matches_current_cadence(self):
        result = build_query_model(channel_count=2, state_interval_seconds=5.0)
        self.assertEqual(result["pre_op003_sql_statements_per_state"], 4.0)
        self.assertEqual(result["current_steady_state_sql_statements_per_state"], 0.333)
        self.assertEqual(result["modeled_reduction_percent"], 91.667)

    @unittest.skipUnless(platform.system() == "Windows", "Windows process sampler")
    def test_windows_process_sampler(self):
        sampler = WindowsProcessSampler(os.getpid())
        sampler.start()
        deadline = time.perf_counter() + 0.35
        while time.perf_counter() < deadline:
            sum(index * index for index in range(1000))
        result = sampler.stop()
        self.assertTrue(result["available"], result)
        self.assertGreater(result["working_set_mb"]["p50"], 0)


if __name__ == "__main__":
    unittest.main()
