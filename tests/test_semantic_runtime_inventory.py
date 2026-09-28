"""Regrese nezávislého runtime inventáře sémantického auditu."""

from tools.semantic_runtime_inventory import (
    discover_history_modes,
    discover_provider_sites,
    discover_workflow_modes,
    validate_runtime_inventory,
)


def test_runtime_inventory_owns_discovered_dispatchers_and_provider_sites():
    errors, unverified, inventory = validate_runtime_inventory()

    assert errors == []
    assert discover_workflow_modes() == {"GENERATE", "MODIFY", "QA", "QFILE"}
    assert "KASKADA" in discover_history_modes()
    assert discover_provider_sites()
    assert inventory["provider_sites"] == discover_provider_sites()

    # Neuzavřené větve musí zůstat explicitní; nesmí se ztratit v obecném PASS.
    open_variants = {row["variant"] for row in unverified}
    assert {"qfile", "verification-profiles", "cancellation"} <= open_variants
