"""Souhrnná fail-closed kontrola rozpracovaných podkladů LW0D.

Nevolá síť, nečte credentials a nikdy neoznačí návrhové podklady za Batch-ready.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def check(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    status = json.loads((root / "resolution-status.json").read_text(encoding="utf-8"))
    for finding in status["findings"]:
        if finding["status"] != "DECIDED":
            errors.append(f"{finding['id']}:{finding.get('remaining', 'neuzavřeno')}")

    lifecycle = json.loads((root / "entity-lifecycles.json").read_text(encoding="utf-8"))
    if lifecycle.get("design_status") != "VALIDATED_DESIGN":
        errors.append("B-03: lifecycle registr není VALIDATED_DESIGN")
    entities = lifecycle.get("entities", {})
    machines = lifecycle.get("machines", {})
    errors.extend(f"B-03: {name} nemá state machine" for name, item in entities.items()
                  if not item.get("machines"))
    errors.extend(f"B-03: {name} nemá explicitní transitions" for name, item in machines.items()
                  if not item.get("transitions"))

    operation = root / "operation-contracts.json"
    if not operation.is_file():
        errors.append("B-01: chybí operation-contracts.json")
    else:
        data = json.loads(operation.read_text(encoding="utf-8"))
        if data.get("status") != "VALIDATED_DESIGN" or data.get("batchReady") is not False:
            errors.append("B-01: operation registry nemá fail-closed DESIGN status")
        coverage = data.get("coverage", {})
        missing = coverage.get("missingOperationContracts", [])
        if missing:
            errors.append(f"B-01: {len(missing)} operací bez kontraktu")

    if not (root / "implementation-layout.json").is_file():
        errors.append("A2: chybí implementation-layout.json")
    if not (root / "A0.effective.json").is_file():
        errors.append("A0: chybí effective requirements")
    if not (root / "A1.effective.json").is_file():
        errors.append("A1: chybí effective plan")
    if not (root / "A2.implementation.json").is_file():
        errors.append("A2: chybí implementation structure")
    return errors


def main() -> int:
    errors = check()
    print("LW0D RECOVERY: BLOCKED" if errors else "LW0D RECOVERY: READY")
    for error in errors:
        print(f"- {error}")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
