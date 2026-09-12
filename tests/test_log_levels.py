import logging
import unittest

from app.logging_config import (
    configure_logging,
    resolve_log_level,
)


class LogLevelTests(unittest.TestCase):
    def tearDown(self):
        logging.getLogger("smartmonitor").setLevel(logging.NOTSET)

    def test_dev_defaults_to_debug(self):
        self.assertEqual(resolve_log_level("dev", None), "DEBUG")

    def test_prod_defaults_to_info(self):
        self.assertEqual(resolve_log_level("prod", None), "INFO")

    def test_explicit_level_is_normalized(self):
        self.assertEqual(resolve_log_level("prod", " warning "), "WARNING")

    def test_invalid_level_is_rejected(self):
        with self.assertRaises(RuntimeError):
            resolve_log_level("dev", "verbose")

    def test_prod_suppresses_debug_and_keeps_errors(self):
        configure_logging("INFO")
        logger = logging.getLogger("smartmonitor.mqtt")

        self.assertFalse(logger.isEnabledFor(logging.DEBUG))
        self.assertTrue(logger.isEnabledFor(logging.ERROR))


if __name__ == "__main__":
    unittest.main()
