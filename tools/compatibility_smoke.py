"""Krátká offline kontrola instalace a hlavních importů podporovaného Pythonu."""

from __future__ import annotations

import importlib


MODULES = (
    "kajovo",
    "kajovo.core",
    "kajovo.core.contracts",
    "kajovo.core.openai_client",
    "kajovo.core.openai_transport",
    "kajovo.core.runs.executor",
    "kajovo.studio",
    "kajovong",
    "utf8nobom",
)


def main() -> int:
    for name in MODULES:
        importlib.import_module(name)
    print(f"Compatibility smoke: OK ({len(MODULES)} modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
