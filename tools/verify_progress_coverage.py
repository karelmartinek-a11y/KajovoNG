"""Ověří úplné mapování referenčních procesů na produkční kruhový progress."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kajovo.core.progress_catalog import (  # noqa: E402
    REFERENCE_PROGRESS_FAMILIES,
    coverage_for_variant,
    covered_variant_ids,
)
from kajovo.core.filesystem_metadata import is_appledouble_metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    errors: list[dict] = []
    inventory_path = ROOT / "docs" / "progress" / "reference_inventory_182.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    variants = inventory.get("variants") or []
    ids = [int(row.get("id")) for row in variants]
    expected_ids = list(range(1, 183))
    if inventory.get("variant_count") != 182 or ids != expected_ids:
        errors.append(
            {
                "scope": "reference_inventory",
                "error": "Referenční inventura nemá přesně varianty 1 až 182.",
                "ids": ids,
            }
        )
    counted_steps = sum(len(row.get("steps") or []) for row in variants)
    if inventory.get("step_count") != 1284 or counted_steps != 1284:
        errors.append(
            {
                "scope": "reference_inventory",
                "error": "Referenční inventura nemá přesně 1 284 kroků.",
                "counted_steps": counted_steps,
            }
        )

    covered = list(covered_variant_ids())
    if covered != expected_ids:
        errors.append(
            {
                "scope": "coverage_catalog",
                "error": "Katalog nepokrývá přesně každou variantu právě jednou.",
                "covered": covered,
            }
        )

    step_evidence: list[dict] = []
    for variant in variants:
        variant_id = int(variant["id"])
        try:
            family = coverage_for_variant(variant_id)
        except ValueError as exc:
            errors.append(
                {
                    "variant": variant_id,
                    "error": str(exc),
                }
            )
            continue
        sources = []
        for relative in family.owners:
            path = ROOT / relative
            if not path.is_file():
                errors.append(
                    {
                        "family": family.key,
                        "path": relative,
                        "error": "Runtime vlastník neexistuje.",
                    }
                )
                continue
            sources.append(path.read_text(encoding="utf-8"))
        joined = "\n".join(sources)
        for marker in family.required_markers:
            if marker not in joined:
                errors.append(
                    {
                        "family": family.key,
                        "marker": marker,
                        "error": "Povinný runtime progress marker nebyl nalezen.",
                    }
                )
        for step in variant.get("steps") or []:
            step_evidence.append(
                {
                    "variant_id": variant_id,
                    "variant_title": variant.get("title", ""),
                    "step_index": int(step.get("index") or 0),
                    "step_title": str(step.get("title") or ""),
                    "runtime_family": family.key,
                    "strategy": family.strategy,
                    "owners": list(family.owners),
                }
            )

    operations_source = (
        ROOT / "kajovo" / "studio" / "operations.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "class Task(QThread)",
        "planned_steps",
        '"Příprava operace"',
        '"Příprava čtení"',
        '"Převzetí výsledku"',
        "worker.progress_event.connect",
        "for event in record.events",
        "OperationDialog = MultiProgressDialog",
    ):
        if marker not in operations_source:
            errors.append(
                {
                    "scope": "managed_operations",
                    "marker": marker,
                    "error": "Společný správce operací nemá úplný kruhový lifecycle.",
                }
            )

    studio_root = ROOT / "kajovo" / "studio"
    for path in studio_root.rglob("*.py"):
        if is_appledouble_metadata(path):
            continue
        source = path.read_text(encoding="utf-8")
        if "QProgressDialog" in source:
            errors.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "error": "Produkční Studio znovu používá starý QProgressDialog.",
                }
            )

    progress_dialog = (
        ROOT / "kajovo" / "studio" / "progress_dialog.py"
    ).read_text(encoding="utf-8")
    progress_view = (
        ROOT / "kajovo" / "studio" / "progress_view.py"
    ).read_text(encoding="utf-8")
    if "MultiProgressDialog" not in progress_dialog or "MultiProgressView" not in progress_view:
        errors.append(
            {
                "scope": "circular_ui",
                "error": "Produkční kruhový dialog nebo jeho view chybí.",
            }
        )

    report = {
        "version": 1,
        "reference_variants": len(variants),
        "reference_steps": counted_steps,
        "covered_variants": len({row["variant_id"] for row in step_evidence}),
        "covered_steps": len(step_evidence),
        "families": [
            {
                "key": family.key,
                "first": family.first,
                "last": family.last,
                "strategy": family.strategy,
                "owners": list(family.owners),
            }
            for family in REFERENCE_PROGRESS_FAMILIES
        ],
        "step_evidence": step_evidence,
        "errors": errors,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print(
        "PROGRESS_COVERAGE",
        json.dumps(
            {
                "reference_variants": report["reference_variants"],
                "reference_steps": report["reference_steps"],
                "covered_variants": report["covered_variants"],
                "covered_steps": report["covered_steps"],
                "errors": errors,
            },
            ensure_ascii=False,
        ),
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
