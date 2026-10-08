"""The SM-015 SQLite bench cannot replace the normal or production DB."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class Sm015BenchDatabaseTests(unittest.TestCase):
    def _run_import(self, *, enabled: bool, path: str | None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["APP_ENV"] = "dev"
        env["SM015_TRANSFER_STAGING_ENABLED"] = "true" if enabled else "false"
        if path is None:
            env.pop("SM015_BENCH_SQLITE_PATH", None)
        else:
            env["SM015_BENCH_SQLITE_PATH"] = path
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from app.database import DATABASE_URL; print(DATABASE_URL.drivername)",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_normal_backend_remains_postgresql(self):
        result = self._run_import(enabled=False, path=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "postgresql+psycopg")

    def test_bench_database_requires_explicit_staging_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bench.sqlite3")
            result = self._run_import(enabled=False, path=path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires enabled development staging", result.stderr)
            self.assertFalse(Path(path).exists())

    def test_bench_database_is_sqlite_only_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "bench.sqlite3")
            result = self._run_import(enabled=True, path=path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "sqlite+pysqlite")
            self.assertFalse(Path(path).exists())


if __name__ == "__main__":
    unittest.main()
