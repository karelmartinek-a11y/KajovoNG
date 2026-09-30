"""Fail-closed kontrola ručních podkladů před vznikem nové dávky."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent


def submission_blockers(root: Path) -> list[str]:
    status = json.loads((root / "resolution-status.json").read_text(encoding="utf-8"))
    findings = status["findings"]
    expected = {f"B-{number:02d}" for number in range(1, 19)}
    if {item["id"] for item in findings} != expected or len(findings) != len(expected):
        raise ValueError("Evidence musí obsahovat právě všech 18 jedinečných nálezů.")
    blockers = []
    for item in findings:
        if item["status"] not in {"OPEN", "PARTIAL", "DECIDED"}:
            raise ValueError("Neznámý stav nálezu.")
        artifact = Path(item["artifact"])
        if artifact.is_absolute() or ".." in artifact.parts or not (root / artifact).is_file():
            raise ValueError(f"Chybí lokální podklad {item['id']}.")
        if item["status"] != "DECIDED":
            blockers.append(f"{item['id']}: {item['remaining']}")
    # Ani ručně vyplněné DECIDED nesmí nahradit skutečné kontroly aplikace.
    required = ["A0.effective.json", "A1.effective.json", "A2.implementation.json"]
    absent = [name for name in required if not (root / name).is_file()]
    if absent:
        blockers.append("Chybí navazující podklady: " + ", ".join(absent))
    if blockers:
        return blockers

    from kajovo.core.context_compiler import ContextCompiler
    from kajovo.core.delivery_preparation import validate_delivery_structure

    requirements, plan, structure = [json.loads((root / name).read_text(encoding="utf-8")) for name in required]
    _, additions = validate_delivery_structure(requirements, plan, structure, mode="GENERATE")
    if additions:
        return ["A2 vyžaduje explicitní doplnění závislostí před sestavením manifestu."]
    snapshot = {"requirements": requirements, "plan": plan, "structure": structure}
    compiler = ContextCompiler(snapshot)
    for file in structure["files"]:
        compiler.compile(file["path"])
    # Manifest + quality evidence musí ověřit následný submit workflow.
    blockers.append("Ještě není ověřen quality gate a konkrétní Batch manifest/JSONL.")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    blockers = submission_blockers(args.root)
    if blockers:
        print("BATCH NEPŘIPRAVEN — bez síťového volání:")
        for blocker in blockers:
            print("- " + blocker)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
