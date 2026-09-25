import unittest
from datetime import datetime, timedelta, timezone

from app.device_transfer_notice import (
    TRANSFER_CAUSE,
    TRANSFER_DELAY,
    completed_transfer_notice,
    pending_transfer_notice,
)


class DeviceTransferNoticeTests(unittest.TestCase):
    def test_v1_delay_and_cause_are_explicit(self):
        self.assertEqual(TRANSFER_DELAY, timedelta(minutes=1))
        self.assertEqual(
            TRANSFER_CAUSE,
            "physical_factory_reset_reassociation",
        )

    def test_completed_notice_uses_effective_local_date_and_physical_cause(self):
        effective_at = datetime(2026, 9, 21, 10, 47, tzinfo=timezone.utc)
        self.assertEqual(
            completed_transfer_notice("SmartMonitor atelier", effective_at),
            "Le retrait de l'appareil SmartMonitor atelier de votre compte "
            "a été déclenché par une action physique sur cet appareil.\n\n"
            "Date d'effet : 21 septembre 2026 à 12h47.",
        )

    def test_pre_notice_uses_expected_date_not_effective_date(self):
        expected_at = datetime(2026, 9, 21, 10, 47, tzinfo=timezone.utc)
        message = pending_transfer_notice("SM-123", expected_at)
        self.assertIn("prévu le 21 septembre 2026 à 12h47", message)
        self.assertIn("action physique", message)
        self.assertIn("Ce n'est pas une anomalie logicielle", message)

    def test_dates_are_converted_across_winter_time(self):
        effective_at = datetime(2026, 12, 1, 11, 47, tzinfo=timezone.utc)
        self.assertIn(
            "1 décembre 2026 à 12h47",
            completed_transfer_notice("SM-123", effective_at),
        )

    def test_naive_timestamp_and_multiline_label_are_rejected(self):
        with self.assertRaises(ValueError):
            completed_transfer_notice("SM-123", datetime(2026, 9, 21))
        with self.assertRaises(ValueError):
            completed_transfer_notice(
                "SM-123\nForgerie", datetime.now(timezone.utc)
            )


if __name__ == "__main__":
    unittest.main()
