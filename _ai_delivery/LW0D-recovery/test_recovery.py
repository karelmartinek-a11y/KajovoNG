"""Regrese lokálních kontrol, výhradně nad dočasnými soubory."""

import json

import pytest

from check_submission import submission_blockers
from verify_recovery import audit_hash
from compile_operation_map import compile_rows
from verify_error_symbols import verify as verify_error_symbols


def fixture_status(tmp_path):
    (tmp_path / "decision.md").write_text("Rozhodnutí", encoding="utf-8")
    return {
        "findings": [
            {"id": f"B-{i:02d}", "status": "DECIDED", "artifact": "decision.md"}
            for i in range(1, 19)
        ]
    }


def save(tmp_path, status):
    (tmp_path / "resolution-status.json").write_text(json.dumps(status), encoding="utf-8")


def test_decisions_do_not_replace_implementation(tmp_path):
    status = fixture_status(tmp_path)
    save(tmp_path, status)
    assert "A2.implementation.json" in submission_blockers(tmp_path)[0]


@pytest.mark.parametrize("state", ["OPEN", "PARTIAL"])
def test_unresolved_blocks_even_if_named_artifacts_exist(tmp_path, state):
    status = fixture_status(tmp_path)
    status["findings"][0].update(status=state, remaining="Neuzavřený mapping")
    for name in ["A0.effective.json", "A1.effective.json", "A2.implementation.json"]:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    save(tmp_path, status)
    assert submission_blockers(tmp_path) == ["B-01: Neuzavřený mapping"]


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "bad_status", "missing_artifact", "escape"])
def test_bad_evidence_is_rejected(tmp_path, mutation):
    status = fixture_status(tmp_path)
    if mutation == "duplicate":
        status["findings"].append(status["findings"][0])
    elif mutation == "missing":
        status["findings"].pop()
    elif mutation == "bad_status":
        status["findings"][0]["status"] = "PASS_WITHOUT_REVIEW"
    elif mutation == "missing_artifact":
        status["findings"][0]["artifact"] = "absent.md"
    else:
        status["findings"][0]["artifact"] = "../outside.md"
    save(tmp_path, status)
    with pytest.raises(ValueError):
        submission_blockers(tmp_path)


def test_audit_hash_domain_and_length():
    digest = audit_hash(1, bytes(32), 1, b"{}")
    assert len(digest) == 64
    assert digest != audit_hash(1, bytes(32), 1, b"{}\n")
    assert digest != audit_hash(1, bytes(32), 2, b"{}")
    assert digest != audit_hash(1, b"1" * 32, 1, b"{}")


@pytest.mark.parametrize("version,previous,sequence", [(2, bytes(32), 1), (1, bytes(31), 1), (1, bytes(32), 0), (1, bytes(32), 2**63)])
def test_audit_rejects_invalid_header(version, previous, sequence):
    with pytest.raises(ValueError):
        audit_hash(version, previous, sequence, b"{}")


def test_explicit_post_query_is_not_inferred_as_command():
    rows = compile_rows([{"method": "POST", "path": "/config/validate", "source_section": "26.14"}],
                        "[26.14]\nQ configuration.validate")
    assert rows[0]["exposure"] == "OWNER_QUERY"
    assert rows[0]["operation"] == "configuration.validate"


@pytest.mark.parametrize("mapping", [
    "[26.14]\nC configuration.apply", "[26.14]\nQ configuration.validate\nQ configuration.validate",
    "[26.13]\nQ configuration.validate", "[26.14]\nQ unknown.read",
    "[26.14]\nQ configuration.validate\n[26.14]\nQ configuration.validate",
])
def test_operation_map_rejects_drift(mapping):
    with pytest.raises(ValueError):
        compile_rows([{"method": "GET", "path": "/config/settings", "source_section": "26.14"}], mapping)


def test_error_alias_requires_known_target():
    source = "## 32. Chybové stavy a obnova\nKNOWN_ERROR\n## 33.\nALIAS_ERROR"
    definitions = {"canonical_additions": {}, "aliases": {"ALIAS_ERROR": "MISSING_ERROR"}, "non_error_symbols": {}}
    with pytest.raises(ValueError, match="canonical target"):
        verify_error_symbols(source, definitions)


def test_error_symbol_cannot_be_both_state_and_alias():
    source = "## 32. Chybové stavy a obnova\nKNOWN_ERROR\n## 33.\nALIAS_ERROR"
    definitions = {"canonical_additions": {}, "aliases": {"ALIAS_ERROR": "KNOWN_ERROR"},
                   "non_error_symbols": {"ALIAS_ERROR": "state"}}
    with pytest.raises(ValueError, match="rolí"):
        verify_error_symbols(source, definitions)


def test_new_unknown_error_does_not_pass_scan():
    source = "## 32. Chybové stavy a obnova\nKNOWN_ERROR\n## 33.\nNEW_CONFLICT"
    with pytest.raises(ValueError, match="NEW_CONFLICT"):
        verify_error_symbols(source, {"canonical_additions": {}, "aliases": {}, "non_error_symbols": {}})
