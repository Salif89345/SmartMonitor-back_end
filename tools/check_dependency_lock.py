from __future__ import annotations

import argparse
import json
import platform
import re
import sys

from importlib import metadata
from pathlib import Path


EXACT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)

CRITICAL_DEPENDENCIES = {
    "alembic": "1.19.1",
    "argon2-cffi": "25.1.0",
    "fastapi": "0.141.1",
    "paho-mqtt": "2.1.0",
    "psycopg": "3.3.4",
    "pydantic": "2.13.4",
    "pyjwt": "2.13.0",
    "python-dotenv": "1.2.2",
    "sqlalchemy": "2.0.51",
    "uvicorn": "0.52.1",
}


def canonical_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_text_compatible(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def parse_locked_requirements(path: Path) -> dict[str, str]:
    requirements: dict[str, str] = {}
    errors: list[str] = []

    for line_number, raw_line in enumerate(read_text_compatible(path).splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        match = EXACT_REQUIREMENT.fullmatch(line)
        if match is None:
            errors.append(
                f"ligne {line_number}: version non verrouillee ou syntaxe non supportee: {line}"
            )
            continue

        name = canonical_name(match.group("name"))
        version = match.group("version")
        if name in requirements:
            errors.append(f"ligne {line_number}: dependance dupliquee: {name}")
            continue
        requirements[name] = version

    if not requirements:
        errors.append("aucune dependance verrouillee trouvee")
    if errors:
        raise ValueError("\n".join(errors))
    return requirements


def validate_critical_dependencies(requirements: dict[str, str]) -> None:
    errors = []
    for name, expected_version in CRITICAL_DEPENDENCIES.items():
        actual_version = requirements.get(name)
        if actual_version is None:
            errors.append(f"dependance critique absente: {name}=={expected_version}")
        elif actual_version != expected_version:
            errors.append(
                f"version critique inattendue: {name}=={actual_version}; "
                f"attendu: {name}=={expected_version}"
            )
    if errors:
        raise ValueError("\n".join(errors))


def validate_installed(requirements: dict[str, str]) -> dict[str, str]:
    installed: dict[str, str] = {}
    errors = []
    for name, expected_version in requirements.items():
        try:
            actual_version = metadata.version(name)
        except metadata.PackageNotFoundError:
            errors.append(f"dependance non installee: {name}=={expected_version}")
            continue
        installed[name] = actual_version
        if actual_version != expected_version:
            errors.append(
                f"environnement divergent: {name}=={actual_version}; "
                f"verrou: {name}=={expected_version}"
            )
    if errors:
        raise ValueError("\n".join(errors))
    return installed


def build_report(
    requirements_path: Path,
    requirements: dict[str, str],
    installed: dict[str, str] | None,
) -> dict[str, object]:
    return {
        "op": "OP-016",
        "result": "PASS",
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "requirements_file": requirements_path.name,
        "locked_dependency_count": len(requirements),
        "critical_dependency_count": len(CRITICAL_DEPENDENCIES),
        "installed_environment_verified": installed is not None,
        "critical_dependencies": CRITICAL_DEPENDENCIES,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Valide le verrou de dependances Python SmartMonitor (OP-016)."
    )
    parser.add_argument("--requirements", type=Path, default=Path("requirements.txt"))
    parser.add_argument("--verify-installed", action="store_true")
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()

    try:
        requirements_path = args.requirements.resolve(strict=True)
        requirements = parse_locked_requirements(requirements_path)
        validate_critical_dependencies(requirements)
        installed = validate_installed(requirements) if args.verify_installed else None
    except (OSError, ValueError) as exc:
        print(f"[OP-016] ECHEC : {exc}", file=sys.stderr)
        return 1

    report = build_report(requirements_path, requirements, installed)
    if args.json_report is not None:
        report_path = args.json_report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(
        f"[OP-016] Verrou valide : {len(requirements)} dependances exactement versionnees."
    )
    print(
        f"[OP-016] Dependances critiques controlees : {len(CRITICAL_DEPENDENCIES)}."
    )
    if installed is not None:
        print("[OP-016] Environnement installe conforme au verrou.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
