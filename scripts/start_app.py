"""Ověření provozních závislostí a spuštění z projektového prostředí."""

import argparse
from importlib import metadata
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str) -> int:
    return subprocess.run([sys.executable, *arguments], cwd=ROOT, check=False).returncode


def missing_requirements(requirements: list[str]) -> list[str]:
    # Pip poskytuje parser verzí i v čerstvém virtuálním prostředí.
    from pip._vendor.packaging.requirements import Requirement

    missing = []
    for value in requirements:
        requirement = Requirement(value)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            missing.append(value)
            continue
        if not requirement.specifier.contains(installed):
            missing.append(value)
    return missing


def prepare() -> int:
    if sys.version_info < (3, 12) or sys.prefix == sys.base_prefix:
        print("Spouštěč vyžaduje projektové .venv s Pythonem 3.12+.", flush=True)
        return 1
    print("Kontroluji provozní závislosti...", flush=True)
    if run("-m", "pip", "--version") != 0:
        if run("-m", "ensurepip", "--upgrade") != 0:
            return 1
    with (ROOT / "pyproject.toml").open("rb") as source:
        requirements = tomllib.load(source)["project"]["dependencies"]
    missing = missing_requirements(requirements)
    consistent = run("-m", "pip", "check") == 0
    if missing or not consistent:
        print("Doplňuji závislosti podle pyproject.toml; může být potřeba internet.", flush=True)
        if run("-m", "pip", "install", *requirements) != 0:
            return 1
        if missing_requirements(requirements) or run("-m", "pip", "check") != 0:
            print("Závislosti nejsou konzistentní. Aplikace se nespustí.", flush=True)
            return 1
    print("Prostředí je připraveno.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    result = prepare()
    if result or args.check_only:
        return result
    return run("-m", "kajovo.app.main")


if __name__ == "__main__":
    raise SystemExit(main())
