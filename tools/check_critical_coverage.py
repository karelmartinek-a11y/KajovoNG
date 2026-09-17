"""Kontrola pokrytí kritických oblastí z úplného pytest-cov JSON reportu."""

from __future__ import annotations

import argparse
import json
import math
import sys
import tomllib
from pathlib import Path


def check(report: dict, config: dict, root: Path) -> list[str]:
    files = report.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Coverage report neobsahuje soubory.")
    normalized = {}
    for path, record in files.items():
        name = path.replace("\\", "/")
        root_name = root.resolve().as_posix() + "/"
        if name.casefold().startswith(root_name.casefold()):
            name = name[len(root_name):]
        if name.startswith("./"):
            name = name[2:]
        if name in normalized:
            raise ValueError(f"Duplicitní coverage cesta: {name}")
        normalized[name] = record
    areas = config.get("areas")
    if not isinstance(areas, dict) or not areas:
        raise ValueError("Konfigurace neobsahuje kritické oblasti.")
    failures = []
    for name, area in areas.items():
        threshold = area["minimum"]
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError(f"{name}: limit musí být číslo.")
        if not math.isfinite(threshold) or not 0 <= threshold <= 100:
            raise ValueError(f"{name}: limit není mezi 0 a 100.")
        patterns = area["paths"]
        if not isinstance(patterns, list) or not patterns:
            raise ValueError(f"{name}: chybí zdrojové cesty.")
        selected = set()
        for pattern in patterns:
            if not isinstance(pattern, str) or ".." in Path(pattern).parts or Path(pattern).is_absolute():
                raise ValueError(f"{name}: neplatný vzor cesty.")
            matched = {p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file()}
            if not matched:
                failures.append(f"{name}: vzor {pattern} neodpovídá žádnému souboru")
            selected.update(matched)
        missing = selected - normalized.keys()
        if missing:
            failures.append(f"{name}: soubory chybí v coverage: {', '.join(sorted(missing))}")
            continue
        covered = total = 0
        for path in selected:
            summary = normalized[path]["summary"]
            hit, count = summary["covered_lines"], summary["num_statements"]
            if type(hit) is not int or type(count) is not int or not 0 <= hit <= count:
                raise ValueError(f"{path}: neplatné počty statements.")
            covered += hit
            total += count
        if not total:
            failures.append(f"{name}: oblast nemá měřitelné statements")
            continue
        percent = 100 * covered / total
        print(f"{name}: {percent:.2f}% ({covered}/{total}), minimum {threshold:.2f}%")
        if percent < threshold:
            failures.append(f"{name}: {percent:.4f}% < {threshold}%")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--config", type=Path, default=Path("coverage-critical.toml"))
    args = parser.parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        config = tomllib.loads(args.config.read_text(encoding="utf-8"))
        failures = check(report, config, args.config.resolve().parent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Neplatná kritická coverage: {exc}", file=sys.stderr)
        return 1
    for failure in failures:
        print(failure, file=sys.stderr)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
