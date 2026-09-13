"""Regrese deterministického výběru, vazeb, integrity a rozpočtu."""
from copy import deepcopy
import hashlib
import json

import pytest

from delivery_fixtures import implementation_fixture
from test_generate_batch import specification
from kajovo.core.context_compiler import ContextCompiler, content_hash, dependency_groups
from kajovo.core.context_budget import BudgetPolicy, configure_file_request, enforce_budget, measure_request
from kajovo.core.contracts import ContractError
from kajovo.core.cost_context_report import CostContextReport
from kajovo.core.recoverable_artifacts import artifact_path, save_artifact


def snapshot():
    structure = specification()
    structure["files"].append({"path": "other.txt", "purpose": "Samostatný výstup",
        "behavior": "Zapíše jiné sdělení.", "kind": "text", "language": "text",
        "requires": [], "provides": [], "dependencies": [], "requirement_ids": ["R2"]})
    structure["files"][0]["requirement_ids"] = ["R1"]
    requirements = {"explicit_requirements": [{"id": "R1", "description": "Sečíst čísla."},
                                              {"id": "R2", "description": "Jiné sdělení."}],
                    "invariants": ["Součet nemění vstup."]}
    implementation_fixture(structure, requirements, {})
    structure["implementation"]["scopes"][0]["paths"] = ["maths.py"]
    return {"structure": structure, "requirements": requirements, "plan": {}, "prompt": "Celý původní zdroj."}


def test_determinism_relevance_and_separate_hashes():
    source = snapshot()
    compiler = ContextCompiler(source)
    result = compiler.compile("maths.py")
    assert result == ContextCompiler(deepcopy(source)).compile("maths.py")
    assert result["file_context_hash"] == content_hash(result["working_context"])
    assert [r["id"] for r in result["working_context"]["relevant_requirements"]] == ["R1"]
    assert "Celý původní zdroj" not in json.dumps(result, ensure_ascii=False)
    source["prompt"] += " Změna nesouvisejícího zdroje."
    changed = ContextCompiler(source).compile("maths.py")
    assert changed["source_snapshot_hash"] != result["source_snapshot_hash"]
    assert changed["file_context_hash"] == result["file_context_hash"]


def test_scoped_invariant_and_provider_contract():
    compiler = ContextCompiler(snapshot())
    assert any(i["value"] == "Součet nemění vstup." for i in compiler.compile("maths.py")["working_context"]["applicable_invariants"])
    assert not any(i["value"] == "Součet nemění vstup." for i in compiler.compile("other.txt")["working_context"]["applicable_invariants"])
    consumer = compiler.compile("main.py")["working_context"]
    assert consumer["dependency_contracts"][0]["interfaces"][0]["id"] == "add"


@pytest.mark.parametrize("fault", ["unknown_symbol", "version", "question", "acceptance", "scope", "facet", "source", "provider"])
def test_insufficient_contract_blocks(fault):
    source = snapshot()
    implementation = source["structure"]["implementation"]
    contract = implementation["files"][0]
    if fault == "unknown_symbol":
        contract["interface_bindings"][0]["id"] = "missing"
    elif fault == "version":
        contract["interface_bindings"][0]["version"] = "2"
    elif fault == "question":
        contract["unresolved_questions"] = [{"question": "Jaký je návratový typ?", "critical": True}]
    elif fault == "acceptance":
        contract["acceptance_criteria"] = []
    elif fault == "scope":
        implementation["scopes"] = []
    elif fault == "facet":
        contract["required_facets"] = ["security"]
    elif fault == "source":
        contract["facets"] = [{"kind": "security", "definition": "Nutná kontrola.", "source_refs": ["/unknown"]}]
    else:
        source["structure"]["files"][1]["dependencies"] = []
    with pytest.raises(ContractError):
        ContextCompiler(source)


def test_cycles_and_waves_are_deterministic():
    source = snapshot()
    graph = dependency_groups(source)
    assert graph["cycles"] == []
    assert graph["waves"][0] == [["maths.py"], ["other.txt"]]
    source["structure"]["files"][0]["dependencies"] = ["main.py"]
    cyclic = dependency_groups(source)
    assert cyclic["cycles"] == [["main.py", "maths.py"]]
    source["structure"]["files"].reverse()
    assert dependency_groups(source) == cyclic


def test_selective_invalidation():
    source = snapshot()
    old = ContextCompiler(source)
    source["structure"]["files"][2]["behavior"] = "Jiný text."
    assert ContextCompiler(source).invalidated(old) == ["other.txt"]
    source = snapshot()
    source["structure"]["implementation"]["interfaces"][0]["signature"] += " / přesnější typ"
    assert ContextCompiler(source).invalidated(old) == ["main.py", "maths.py"]


def test_verified_dependency_requires_content_and_contract_hash():
    compiler = ContextCompiler(snapshot())
    content = "def add(a: int, b: int) -> int: return a + b\n"
    artifact = {"validation_status": "verified", "contract_hash": compiler.provider_hash("maths.py"),
                "content": content, "output_hash": hashlib.sha256(content.encode()).hexdigest()}
    result = compiler.compile("main.py", verified_artifacts={"maths.py": artifact})
    assert len(result["working_context"]["verified_dependency_artifacts"]) == 1
    artifact["content"] += "# změna"
    with pytest.raises(ContractError):
        compiler.compile("main.py", verified_artifacts={"maths.py": artifact})


def test_originals_are_selected_without_truncation():
    compiler = ContextCompiler(snapshot())
    text = "Původní text\n" * 10000
    result = compiler.compile("main.py", originals={"maths.py": text, "other.txt": "nepotřebné"})
    sources = result["working_context"]["relevant_source_excerpts"]
    assert [(s["path"], s["content"]) for s in sources] == [("maths.py", text)]


def test_legacy_does_not_silently_compile():
    source = snapshot()
    source["structure"].pop("implementation")
    with pytest.raises(ContractError, match="legacy"):
        ContextCompiler(source)


@pytest.mark.parametrize("tokens,warning,blocked", [(1000, False, False), (90000, True, False),
                                                     (160000, True, True), (210000, True, True)])
def test_soft_hard_and_justification(tokens, warning, blocked):
    body = {"model": "gpt-4.1", "input": "nekrátit", "max_output_tokens": 1000}
    before = deepcopy(body)
    report = measure_request(body, exact_input_tokens=tokens)
    assert bool(report["warnings"]) is warning
    assert bool(report["blockers"]) is blocked
    if blocked:
        with pytest.raises(ContractError):
            enforce_budget(report)
    else:
        enforce_budget(report)
    assert body == before


def test_model_context_safety_and_long_context_threshold():
    body = {"model": "gpt-4.1", "input": "data", "max_output_tokens": 1000}
    report = measure_request(body, policy=BudgetPolicy(long_context_threshold=10000), exact_input_tokens=11000)
    assert report["long_context_exceeded"] and report["blockers"]
    report = measure_request(body, exact_input_tokens=1000000, justification="Ověřená široká analýza")
    assert any("rezerva" in error for error in report["blockers"])


def test_unknown_external_input_is_explicit():
    report = measure_request({"model": "gpt-4.1", "input": [{"role": "user", "content": [
        {"type": "input_file", "file_id": "file_ref"}]}], "previous_response_id": "resp_ref"})
    assert set(report["unknown_components"]) == {"input_file", "serverová historie"}
    assert report["projected_cost"] is None


def test_complexity_and_output_budget_preserve_model():
    compiled = ContextCompiler(snapshot()).compile("maths.py")
    payload = {"model": "gpt-5.2", "input": "data", "temperature": 0.2}
    routing = configure_file_request(payload, compiled, maximum_quality=True)
    assert payload["model"] == "gpt-5.2"
    assert payload["reasoning"]["effort"] == "xhigh"
    assert payload["max_output_tokens"] > 16384
    assert payload["truncation"] == "disabled"
    assert routing["score"] > 0


def test_cost_report_reimport_does_not_double_usage(tmp_path):
    report = CostContextReport(tmp_path)
    payload = {"model": "gpt-4.1", "input": "data", "max_output_tokens": 1000}
    response = {"id": "resp_1", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 100, "output_tokens": 30, "output_tokens_details": {"reasoning_tokens": 20}}}
    for _ in range(2):
        report.record(payload, custom_id="task_1", response=response)
    data = json.loads(report.path.read_text("utf-8"))
    assert data["summary"]["actual_input_tokens"] == 100
    assert data["summary"]["actual_reasoning_tokens"] == 20
    assert data["summary"]["incomplete"] == ["task_1"]


def test_exact_artifact_is_not_redacted_and_tamper_blocks(tmp_path):
    data = {"text": "Řetězec Bearer popisuje HTTP kontrakt, nikoli klíč."}
    saved = save_artifact(tmp_path, "sample", data)
    assert artifact_path(tmp_path, "sample") == saved
    from pathlib import Path
    assert json.loads(Path(saved).read_text("utf-8")) == data
    Path(saved).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        artifact_path(tmp_path, "sample")


def test_journal_recovers_exact_payload_with_redactable_contract_text(tmp_path):
    from kajovo.core.runlog import RunLogger
    from kajovo.core.response_journal import ResponseJournal
    from kajovo.core.generate_batch import digest
    logger = RunLogger(str(tmp_path), "run_exact")
    payload = {"model": "gpt-4.1", "input": "Specifikace: Authorization Bearer je schéma hlavičky."}
    entries = {digest(payload): {"payload": payload, "status": "completed", "id": "resp_exact"}}
    log_path = logger.save_json("manifests", "response_journal", {"version": 1, "entries": entries})
    from pathlib import Path
    assert "REDACTED" in Path(log_path).read_text("utf-8")
    assert ResponseJournal(logger).entries == entries


def test_token_count_endpoint_never_creates_response():
    from unittest.mock import Mock
    from kajovo.core.openai_client import OpenAIClient
    client = object.__new__(OpenAIClient)
    client._req = Mock(return_value={"input_tokens": 123})
    payload = {"model": "gpt-4.1", "input": "data", "max_output_tokens": 2048}
    result = client.count_input_tokens(payload)
    assert result["input_tokens"] == 123
    assert result["request_hash"] == content_hash(payload)
    assert client._req.call_args.args == ("POST", "/responses/input_tokens")
    assert "max_output_tokens" not in client._req.call_args.kwargs["json_body"]


def test_poll_metadata_does_not_duplicate_immutable_work_payloads(tmp_path):
    from pathlib import Path
    from kajovo.core.runlog import RunLogger
    from kajovo.core.response_journal import ResponseJournal
    logger = RunLogger(str(tmp_path), "run_poll")
    journal = ResponseJournal(logger)
    payload = {"model": "gpt-4.1", "input": "Neměnný pracovní obsah"}
    journal.entries[content_hash(payload)] = {"payload": payload, "status": "in_progress", "checked_at": 1,
        "response": {"id": "resp_pending", "status": "in_progress", "_request_id": "get_1"}}
    journal.save()
    before = set((Path(logger.paths.run_dir) / "artifacts").glob("*.json"))
    entry = journal.entries[content_hash(payload)]
    entry["checked_at"] = 2
    entry["response"]["_request_id"] = "get_2"
    journal.save()
    assert set((Path(logger.paths.run_dir) / "artifacts").glob("*.json")) == before


def test_manifest_versions_and_file_context_integrity():
    from test_generate_batch import manifest
    from kajovo.core.generate_batch import encode_requests
    current = manifest()
    assert current["version"] == 3 and "prompt" in current["snapshot"]
    context = json.loads(current["requests"][0]["body"]["input"])
    assert "specification" not in context
    context["file_context"]["file_context_hash"] = "0" * 64
    broken = deepcopy(current)
    broken["requests"][0]["body"]["input"] = json.dumps(context)
    with pytest.raises(ContractError, match="FileContext"):
        encode_requests(broken)
    for version in (0, 4, 99):
        broken = deepcopy(current)
        broken["version"] = version
        with pytest.raises(ContractError, match="verze"):
            encode_requests(broken)


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_is_read_only_not_reinterpreted(tmp_path, version):
    from unittest.mock import Mock
    from test_generate_batch import manifest
    from kajovo.core.generate_batch import digest, encode_requests, repeat_saved_batch
    old = manifest()
    old["version"] = version
    old["snapshot"]["structure"].pop("implementation")
    old["snapshot_hash"] = digest(old["snapshot"])
    for row in old["requests"]:
        context = json.loads(row["body"]["input"])
        row["body"]["input"] = json.dumps({"specification": old["snapshot"], "file": context["file"]})
    before = deepcopy(old)
    assert encode_requests(old)
    assert old == before
    (tmp_path / "run_state.json").write_text(json.dumps({"generate_batch": old, "batch_id": "batch_old"}), encoding="utf-8")
    client = Mock()
    with pytest.raises(ContractError, match="Legacy"):
        repeat_saved_batch(client, tmp_path, "batch_old", ["main.py"])
    assert client.mock_calls == []


def test_empty_file_is_rejected_without_losing_other_output(tmp_path):
    from test_generate_batch import manifest, outputs, raw
    from kajovo.core.generate_batch import import_results
    source = manifest()
    rows = outputs(source)
    output = rows[0]["response"]["body"]
    value = json.loads(output["output_text"])
    value["content"] = ""
    output["output_text"] = json.dumps(value)
    result = import_results(source, [raw(rows)], str(tmp_path))
    assert result["errors"] and result["status"] == "partial"
    assert not (tmp_path / "maths.py").exists()
    assert (tmp_path / "main.py").exists()
