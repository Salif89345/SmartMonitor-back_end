from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.check_dependency_lock import (
    CRITICAL_DEPENDENCIES,
    parse_locked_requirements,
    validate_critical_dependencies,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class DependencyLockTests(unittest.TestCase):
    def test_project_requirements_are_exactly_locked(self):
        requirements = parse_locked_requirements(BACKEND_ROOT / "requirements.txt")
        self.assertGreaterEqual(len(requirements), len(CRITICAL_DEPENDENCIES))

    def test_critical_dependencies_match_approved_versions(self):
        requirements = parse_locked_requirements(BACKEND_ROOT / "requirements.txt")
        validate_critical_dependencies(requirements)

    def test_unpinned_requirement_is_rejected(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "requirements.txt"
            path.write_text("fastapi>=0.141.1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "version non verrouillee"):
                parse_locked_requirements(path)

    def test_duplicate_requirement_is_rejected(self):
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "requirements.txt"
            path.write_text("FastAPI==0.141.1\nfastapi==0.141.1\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "dependance dupliquee"):
                parse_locked_requirements(path)


if __name__ == "__main__":
    unittest.main()
