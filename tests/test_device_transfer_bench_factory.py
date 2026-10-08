import unittest
from unittest.mock import patch

from app.device_transfer_bench_factory import configured_bench_factory_key


UID = "SM-A1B2C3D4E5F6"
KEY_HEX = "A5" * 32


class BenchFactoryKeyTests(unittest.TestCase):
    def test_explicit_dev_bench_key_is_scoped_to_one_device(self):
        with patch("app.device_transfer_bench_factory.APP_ENV", "dev"), patch(
            "app.device_transfer_bench_factory.SM015_TRANSFER_STAGING_ENABLED", True
        ), patch.dict(
            "os.environ",
            {
                "SM015_BENCH_DEVICE_UID": UID,
                "SM015_BENCH_FACTORY_KEY_HEX": KEY_HEX,
            },
        ):
            provider = configured_bench_factory_key()
        self.assertEqual(provider.key_for_device(UID), bytes.fromhex(KEY_HEX))
        self.assertIsNone(provider.key_for_device("SM-112233445566"))

    def test_prod_or_disabled_staging_never_uses_bench_key(self):
        with patch.dict(
            "os.environ",
            {
                "SM015_BENCH_DEVICE_UID": UID,
                "SM015_BENCH_FACTORY_KEY_HEX": KEY_HEX,
            },
        ):
            with patch("app.device_transfer_bench_factory.APP_ENV", "prod"), patch(
                "app.device_transfer_bench_factory.SM015_TRANSFER_STAGING_ENABLED", True
            ):
                self.assertIsNone(configured_bench_factory_key())
            with patch("app.device_transfer_bench_factory.APP_ENV", "dev"), patch(
                "app.device_transfer_bench_factory.SM015_TRANSFER_STAGING_ENABLED", False
            ):
                self.assertIsNone(configured_bench_factory_key())


if __name__ == "__main__":
    unittest.main()
