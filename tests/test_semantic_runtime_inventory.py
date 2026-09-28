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

    # Každé zbývající omezení musí být explicitní; prázdný seznam je dovolen
    # pouze pokud všechny aktuálně inventarizované kroky mají oba důkazy.
    assert all(row["variant"] and row["reason"] for row in unverified)
