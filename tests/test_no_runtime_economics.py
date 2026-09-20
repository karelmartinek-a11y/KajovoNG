from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {
    "context_pricing",
    "context_budget",
    "cost_context_report",
    "max_cost_microusd",
    "max_paid_requests",
    "unknown_pricing",
    "budget_reservation_id",
    "actual_cost_microusd",
    "price_snapshot_hash",
    "BUDGET_EXCEEDED",
    "orchestration.ledger",
}
LEGACY_ALLOW = {
    ROOT / "kajovo" / "core" / "orchestration" / "repository.py": {
        "actual_cost_microusd",
        "price_snapshot_hash",
    },
    ROOT / "kajovo" / "core" / "orchestration" / "work_order.py": {
        "budget_reservation_id",
    },
}
ACTIVE_FILES = [
    ROOT / "scripts" / "live_acceptance.py",
    ROOT / "kajovo_settings.example.json",
    ROOT
    / "resources"
    / "orchestration"
    / "contracts"
    / "local"
    / "RUN_CONFIG_V2.schema.json",
    ROOT
    / "resources"
    / "orchestration"
    / "contracts"
    / "local"
    / "WORK_ORDER_V2.schema.json",
]


def _runtime_files():
    yield from sorted((ROOT / "kajovo").rglob("*.py"))
    yield from ACTIVE_FILES


def test_runtime_has_no_economic_architecture_symbols():
    findings = []
    for path in _runtime_files():
        text = path.read_text(encoding="utf-8")
        allowed = LEGACY_ALLOW.get(path, set())
        for token in FORBIDDEN:
            if token in text and token not in allowed:
                findings.append(
                    f"{path.relative_to(ROOT)}: forbidden token {token}"
                )
    assert not findings, "\n".join(findings)


def test_deleted_economic_modules_do_not_exist():
    for relative in (
        "kajovo/core/context_pricing.py",
        "kajovo/core/context_budget.py",
        "kajovo/core/cost_context_report.py",
        "kajovo/core/orchestration/ledger.py",
    ):
        assert not (ROOT / relative).exists()
