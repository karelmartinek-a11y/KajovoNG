"""Inventář musí odhalit nové volání, změněnou větev i chybějící důkaz."""
from __future__ import annotations

import copy
import json
from pathlib import Path

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


@pytest.mark.parametrize("default_encoding", [None, "cp1252"])
def test_call_sites_and_transport_registry_match_current_code(monkeypatch, default_encoding):
    if default_encoding:
        original = Path.read_text

        def local_read_text(path, encoding=None, errors=None):
            return original(path, encoding=encoding or default_encoding, errors=errors)

        monkeypatch.setattr(Path, "read_text", local_read_text)
    value = inventory()
    verify_inventory(value, response_call_sites())
    verify_source_index(value)
    assert value["transport_registry"] == transport_registry()
    assert json.loads(RUNTIME_SCHEMAS.read_text(encoding="utf-8")) == runtime_catalog()


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
    path.write_text((ROOT / "kajovo/core/openai_client.py").read_text(encoding="utf-8") + "\n# nová transportní větev\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Změna vlastních zdrojů"):
        verify_source_index(value, root=tmp_path)


@pytest.mark.parametrize("path", ["kajovo/core/diagnostics/remote.ps1", "tools/new_network.sh", "resources/orchestration/contracts/wire/NEW.schema.json"])
def test_non_python_network_or_contract_addition_requires_an_audit(tmp_path, path):
    new = tmp_path / path
    new.parent.mkdir(parents=True)
    new.write_text("# nový síťový skript nebo kontrakt\n", encoding="utf-8")
    with pytest.raises(ValueError, match="skriptů nebo fyzických kontraktů"):
        verify_source_index({"sources": [], "source_assets": []}, root=tmp_path)
