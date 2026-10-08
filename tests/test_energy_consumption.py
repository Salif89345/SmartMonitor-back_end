import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from sqlalchemy.dialects import postgresql

from app.energy_consumption import consumption_buckets
from app.history_service import build_detailed_history

START = datetime(2026, 10, 5, tzinfo=timezone.utc)


def calculate(values, minutes=None, baseline=None, resolution=300, end=None):
    minutes = list(range(len(values))) if minutes is None else minutes
    rows = [(START + timedelta(minutes=m), value) for m, value in zip(minutes, values)]
    return consumption_buckets(
        rows, period_from=START,
        period_to=end or START + timedelta(minutes=minutes[-1] + 1),
        resolution_seconds=resolution, max_gap_seconds=180, baseline=baseline,
    )


class CounterConsumptionTests(unittest.TestCase):
    def test_monotonic_counter_and_real_zero(self):
        totals, _, _ = calculate([61.48, 61.481, 61.49, 61.49])
        self.assertAlmostEqual(sum(v for v in totals.values() if v is not None), .01)
        self.assertEqual(calculate([4., 4.])[0][START], 0.)

    def test_single_sample_is_unknown_not_zero(self):
        self.assertIsNone(calculate([61.48])[0][START])

    def test_decrease_does_not_lock_future_counter_or_invent_rebound(self):
        for values in ([100, 0, .1, .2], [100, 1, 100.1, 100.2]):
            with self.subTest(values=values):
                totals, partial, _ = calculate(values)
                self.assertAlmostEqual(totals[START], .1)
                self.assertTrue(partial[START])

    def test_reset_inside_and_across_buckets_has_same_known_total(self):
        values = [100, 100.1, 0, .1, .2, .3]
        totals = []
        for resolution in (60, 120, 300, 7200):
            buckets, _, _ = calculate(values, resolution=resolution)
            totals.append(round(sum(v for v in buckets.values() if v is not None), 9))
        self.assertEqual(totals, [.3] * 4)

    def test_gap_is_not_attributed_to_reconnection_bucket(self):
        totals, partial, _ = calculate([1, 1.1, 8, 8.1], [0, 1, 60, 61])
        self.assertAlmostEqual(totals[START], .1)
        later = START + timedelta(hours=1)
        self.assertAlmostEqual(totals[later], .1)
        self.assertTrue(partial[later])

    def test_null_nonfinite_negative_break_continuity(self):
        for missing in (None, float('nan'), float('inf'), -1):
            totals, partial, _ = calculate([1, missing, 20, 20.1])
            self.assertAlmostEqual(totals[START], .1)
            self.assertTrue(partial[START])

    def test_baseline_must_be_recent_and_valid(self):
        recent = (START - timedelta(seconds=60), 1)
        self.assertAlmostEqual(calculate([1.1], baseline=recent)[0][START], .1)
        for baseline in ((START - timedelta(days=4), 1), (START, None)):
            self.assertIsNone(calculate([4], baseline=baseline)[0][START])

    def test_duplicate_or_reversed_timestamp_does_not_double_count(self):
        totals, partial, _ = calculate([1, 1.1, 30, 20, 1.2], [0, 1, 1, 0, 2])
        self.assertAlmostEqual(totals[START], .2)
        self.assertTrue(partial[START])

    def test_dst_repeated_hour_is_accounted_in_utc(self):
        paris = ZoneInfo('Europe/Paris')
        start = datetime(2026, 10, 25, 0, tzinfo=timezone.utc)
        rows = [(start + timedelta(minutes=i), 1 + i * .001) for i in range(121)]
        totals, _, _ = consumption_buckets(
            [(t.astimezone(paris), v) for t, v in rows], period_from=start,
            period_to=start + timedelta(hours=3), resolution_seconds=3600,
            max_gap_seconds=180,
        )
        self.assertEqual(len(totals), 3)
        self.assertAlmostEqual(sum(v for v in totals.values() if v is not None), .12)


class HistoryCalculationTests(unittest.TestCase):
    def test_latest_scoped_baseline_and_summary_equals_visible_buckets(self):
        statements = []
        metrics = {f'{kind}_{name}': 0 for kind in ('min', 'avg', 'max')
                   for name in ('power_w', 'voltage_v', 'current_a', 'frequency_hz', 'power_factor')}
        class Session:
            def execute(self, statement):
                statements.append(statement)
                if len(statements) == 1:
                    return SimpleNamespace(one=lambda: SimpleNamespace(**metrics))
                if len(statements) == 2:
                    return SimpleNamespace(first=lambda: (START - timedelta(seconds=60), 61.48))
                if len(statements) == 3:
                    return [(START, 61.481), (START + timedelta(seconds=60), 61.484)]
                return SimpleNamespace(all=lambda: [SimpleNamespace(
                    bucket_start=START, sample_count=1, **{f'avg_{name}': 0 for name in (
                        'power_w', 'voltage_v', 'current_a', 'frequency_hz', 'power_factor')}
                ), SimpleNamespace(
                    bucket_start=START + timedelta(seconds=60), sample_count=1,
                    **{f'avg_{name}': 0 for name in (
                        'power_w', 'voltage_v', 'current_a', 'frequency_hz', 'power_factor')}
                )])
        result = build_detailed_history(Session(), 8, START, START + timedelta(minutes=2), 90, 42, 9)
        self.assertAlmostEqual(result['summary']['consumption_kwh'], .004)
        self.assertAlmostEqual(sum(p['consumption_kwh'] for p in result['points']), .004)
        for query in statements:
            sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={'literal_binds': True}))
            self.assertIn('owner_user_id = 42', sql)
            self.assertIn('epoch_transfer_id = 9', sql)
        baseline_sql = str(statements[1].compile(dialect=postgresql.dialect()))
        self.assertIn('ORDER BY power_measurements.measured_at DESC', baseline_sql)
        self.assertIn('LIMIT', baseline_sql)
        self.assertNotIn('max(', baseline_sql)
