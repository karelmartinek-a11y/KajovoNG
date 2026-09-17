from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CONSTRAINTS = ROOT / "requirements" / "constraints.txt"
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(value: str) -> str:
    match = NAME_RE.match(value.strip())
    if not match:
        raise ValueError(f"Nelze přečíst název dependency: {value!r}")
    return normalize(match.group(0))


def constrained_names() -> set[str]:
    names: set[str] = set()
    if not CONSTRAINTS.is_file():
        return names
    for raw in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        names.add(requirement_name(line))
    return names


def project_dependency_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    values = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        values.extend(extra)
    return {requirement_name(value) for value in values}


def ad_hoc_install_lines() -> list[str]:
    violations: list[str] = []
    roots = [ROOT / "Build", ROOT / ".github" / "workflows"]
    for base in roots:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".ps1", ".sh", ".yml", ".yaml"}:
                continue
            for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                line = raw.strip()
                lower = line.lower()
                if "pip install" not in lower:
                    continue
                if "requirements/constraints.txt" not in lower:
                    violations.append(f"{path.relative_to(ROOT)}:{number}: {line}")
                    continue
                if ".[build]" not in lower and ".[dev,build]" not in lower and ".[build,dev]" not in lower:
                    violations.append(f"{path.relative_to(ROOT)}:{number}: {line}")
    return violations


def main() -> int:
    errors: list[str] = []
    if not CONSTRAINTS.is_file():
        errors.append("Chybí requirements/constraints.txt.")
    constrained = constrained_names()
    declared = project_dependency_names()
    missing = sorted(declared - constrained)
    if missing:
        errors.append("V constraints chybí deklarované dependencies: " + ", ".join(missing))
    if "pyinstaller" not in constrained:
        errors.append("PyInstaller není připnutý v constraints.")
    violations = ad_hoc_install_lines()
    if violations:
        errors.append("Ad-hoc instalace mimo kanonický contract:\n" + "\n".join(violations))
    if errors:
        print("DEPENDENCY CONTRACT: FAIL", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"DEPENDENCY CONTRACT: OK ({len(constrained)} constrained packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
