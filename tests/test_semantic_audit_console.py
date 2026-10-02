"""Konzolový souhrn auditu funguje i v omezeném kódování konzole Windows."""

import io
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("encoding", ["ascii", "cp1250", "cp1252"])
@pytest.mark.parametrize("has_errors", [False, True])
def test_semantic_main_preserves_czech_json_on_console_and_in_utf8_report(monkeypatch, tmp_path,
                                                                       encoding, has_errors):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    from tools import verify_semantic_flows as audit

    reason = "Příliš žluťoučký kůň úpěl ďábelské ódy."
    unverified = [{"variant": "Ukázkový běh", "reason": reason}]
    errors = [{"scope": "syntetická kontrola", "error": "Chybí ověřený výsledek práce."}] if has_errors else []
    runtime_inventory = {"test_nodes": ["tests/ukazka.py::test_prubeh"], "provider_sites": ["místní náhrada"]}
    monkeypatch.setattr(audit, "_validate_manifest", lambda: list(errors))
    monkeypatch.setattr(audit, "validate_runtime_inventory", lambda: ([], unverified, runtime_inventory))
    monkeypatch.setattr(audit, "_response_format_sites", lambda: {"dynamic": [], "errors": []})
    monkeypatch.setattr(audit, "_runtime_named_contracts", lambda: {"UKAZKOVY_KONTRAKT"})
    monkeypatch.setattr(audit, "_all_tests", lambda: ["tests/ukazka.py"])
    monkeypatch.setattr(audit, "_git_sha", lambda: "testovaci-revize")
    monkeypatch.setattr(audit, "PROCESS_FAMILIES", ())
    monkeypatch.setattr(audit, "RUNTIME_VARIANTS", ())
    monkeypatch.setattr(audit.importlib.metadata, "version", lambda _name: "testovaci-verze")
    calls = []

    def run(command, *, env=None):
        calls.append(command)
        if "tools/verify_contract_links.py" in command:
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps({"errors": []}), encoding="utf-8")
        else:
            assert command[1:3] == ["-m", "pytest"]
            assert env["KAJOVO_LIVE_ACCEPTANCE"] == "0"
        return {"returncode": 0, "output": "Místní ověření bez poskytovatele."}

    # Všechny podprocesy i sběr důkazů jsou místní náhrady; main zůstává skutečný.
    monkeypatch.setattr(audit, "_run", run)
    report_path = tmp_path / "vysledek" / "audit.json"
    monkeypatch.setattr(audit.sys, "argv", ["verify_semantic_flows.py", "--output", str(report_path)])
    raw_console = io.BytesIO()
    console = io.TextIOWrapper(raw_console, encoding=encoding, errors="strict", write_through=True)
    try:
        with monkeypatch.context() as output_patch:
            output_patch.setattr(audit.sys, "stdout", console)
            returncode = audit.main()
            console.flush()
            printed = raw_console.getvalue()
    finally:
        console.detach()

    assert returncode == (1 if has_errors else 0)
    assert len(calls) == 5
    assert all(byte < 128 for byte in printed)
    prefix, encoded_summary = printed.decode(encoding).split(" ", 1)
    assert prefix == "SEMANTIC_FLOWS"
    summary = json.loads(encoded_summary)
    assert summary["runtime_unverified"] == unverified
    assert summary["errors"] == errors
    assert summary["runtime_pytest_collect"] == summary["pytest_collect"] == 0
    report_bytes = report_path.read_bytes()
    assert reason.encode("utf-8") in report_bytes
    report = json.loads(report_bytes.decode("utf-8"))
    assert report["runtime_inventory"]["unverified"] == unverified
    assert report["errors"] == errors
    assert report["pytest_execute"]["output"] == "Místní ověření bez poskytovatele."
