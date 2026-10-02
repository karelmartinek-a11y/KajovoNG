"""Inventář musí odhalit nové volání, změněnou větev i chybějící důkaz."""
from __future__ import annotations

import copy
import json

import pytest

from tools.verify_response_contracts import (
    INVENTORY,
    ROOT,
    RUNTIME_SCHEMAS,
    response_call_sites,
    runtime_catalog,
    transport_registry,
    verify_inventory,
    verify_source_index,
)


def inventory():
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_call_sites_and_transport_registry_match_current_code():
    value = inventory()
    verify_inventory(value, response_call_sites())
    verify_source_index(value)
    assert value["transport_registry"] == transport_registry()
    assert json.loads(RUNTIME_SCHEMAS.read_text()) == runtime_catalog()


def test_new_call_or_removed_call_fails_inventory_gate():
    value = inventory()
    discovered = response_call_sites()
    extra = {**discovered[0], "id": "new_process::new_call::create_response::1"}
    with pytest.raises(ValueError, match="nové="):
        verify_inventory(value, [*discovered, extra])
    with pytest.raises(ValueError, match="chybějící="):
        verify_inventory(value, discovered[1:])


@pytest.mark.parametrize("field", ["call_sha256", "caller_sha256"])
def test_new_contract_or_branch_fails_even_when_call_id_remains(field):
    discovered = response_call_sites()
    discovered[0][field] = "different"
    with pytest.raises(ValueError, match="větvení"):
        verify_inventory(inventory(), discovered)


def test_pending_handoffs_prevent_complete_pass():
    value = inventory()
    assert value["status"] == "PARTIAL"
    with pytest.raises(ValueError, match="PARTIAL"):
        verify_inventory(value, response_call_sites(), require_complete=True)
    claimed = copy.deepcopy(value)
    for row in claimed["calls"]:
        row["verification"]["status"] = "verified"
        row["variants"] = []
    with pytest.raises(ValueError, match="Neúplný důkaz"):
        verify_inventory(claimed, response_call_sites(), require_complete=True)


def test_unknown_network_code_changes_source_gate(tmp_path):
    value = inventory()
    path = tmp_path / "kajovo/core/openai_client.py"
    path.parent.mkdir(parents=True)
    path.write_text((ROOT / "kajovo/core/openai_client.py").read_text() + "\n# nová transportní větev\n")
    with pytest.raises(ValueError, match="Změna vlastních zdrojů"):
        verify_source_index(value, root=tmp_path)


@pytest.mark.parametrize("path", ["kajovo/core/diagnostics/remote.ps1", "tools/new_network.sh", "resources/orchestration/contracts/wire/NEW.schema.json"])
def test_non_python_network_or_contract_addition_requires_an_audit(tmp_path, path):
    new = tmp_path / path
    new.parent.mkdir(parents=True)
    new.write_text("# nový síťový skript nebo kontrakt\n")
    with pytest.raises(ValueError, match="skriptů nebo fyzických kontraktů"):
        verify_source_index({"sources": [], "source_assets": []}, root=tmp_path)
