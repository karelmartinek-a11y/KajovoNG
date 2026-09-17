#!/usr/bin/env python3
"""Vygeneruje forenzní inventář Studio UI KájovoNG."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kajovo.studio.ui_audit import audit_studio, write_inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="Kořen repozitáře")
    parser.add_argument("--write", default="", help="Cílový JSON soubor")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Selže při nevyřešené self vazbě ovládacího prvku/signálu.",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    result = (
        write_inventory(root, args.write)
        if args.write
        else audit_studio(root)
    )
    if not args.write:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.check and result["totals"]["unresolved"]:
        for module in result["modules"]:
            for issue in module["unresolved"]:
                print(
                    f"{module['source']}:{issue['line']}: {issue['target']} - {issue['reason']}"
                )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
