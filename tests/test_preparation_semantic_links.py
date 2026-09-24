"""Vazby přípravných kontraktů a úplný kontext bez síťových volání."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from change_v2_fixtures import default_files, file_spec, plan_data, requirements_data, spine_data, run, scenario
from kajovo.core.context_compiler import ContextCompiler
from kajovo.core.contracts import ContractError
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.orchestration.preparation import reconcile_modify_plan, validate_requirements_v2, validate_spine_v1
from kajovo.core.orchestration.projection import projection_from_file_context


def inputs():
    source = {"segments": [{"source_id": "S1", "segment_id": "S1-1",
                            "start_byte": 0, "end_byte": 1, "sha256": "a" * 64}]}
    return SimpleNamespace(source_context=source), requirements_data(source)


@pytest.mark.parametrize("fault", ["source", "cycle", "acceptance", "state", "duplicate"])
def test_requirements_reject_broken_relations(fault):
    worker, requirements = inputs()
    req = requirements["requirements"][0]
    if fault == "source":
        req["source_refs"] = []
    elif fault == "cycle":
        req.update(kind="derived", derived_from=["REQ-1"])
    elif fault == "acceptance":
        requirements["acceptance"][0]["requirement_ids"] = []
    elif fault == "state":
        requirements["lifecycles"] = [{"id": "L1", "requirement_ids": ["REQ-1"],
            "states": ["ready"], "transitions": [{"from_state": "ready", "to_state": "missing"}]}]
    else:
        requirements["invariants"] = [{"id": "SAME", "requirement_ids": ["REQ-1"]}]
        requirements["flows"] = [{"id": "SAME", "requirement_ids": ["REQ-1"]}]
    with pytest.raises(ContractError):
        validate_requirements_v2(worker, requirements)


@pytest.mark.parametrize("fault", ["provider", "consumer", "missing_owner", "unknown_owner"])
def test_spine_rejects_broken_relations(fault):
    _, requirements = inputs()
    spine = spine_data("GENERATE", default_files("GENERATE"))
    if fault in {"provider", "consumer"}:
        spine["interfaces"] = [{"id": "I1", "version": 1, "providers": ["hello.txt"],
            "consumers": ["hello.txt"] if fault == "consumer" else [], "requirement_ids": ["REQ-1"]}]
        if fault == "consumer":
            spine["files"][0]["provides"] = ["I1"]
    elif fault == "missing_owner":
        requirements["invariants"] = [{"id": "INV-1"}]
    else:
        spine["obligation_owners"] = [{"obligation_id": "UNKNOWN", "paths": ["hello.txt"], "reason": "Vlastník"}]
    with pytest.raises(ContractError):
        validate_spine_v1("GENERATE", requirements, plan_data(), spine)


def compiled_snapshot():
    _, requirements = inputs()
    requirements["invariants"] = [{"id": "INV-1", "statement": "Neukládat tajemství.", "requirement_ids": ["REQ-1"]}]
    spine = spine_data("GENERATE", default_files("GENERATE"))
    spine["obligation_owners"] = [{"obligation_id": "INV-1", "paths": ["hello.txt"], "reason": "Vlastník"}]
    return {"requirements": requirements, "plan": plan_data(), "structure": {
        "contract": "IMPLEMENTATION_GRAPH_V3", "spine": spine,
        "file_specs": [{"path": "hello.txt", "spec": file_spec(spine["files"][0], [])}]}}


@pytest.mark.parametrize("kind", ["invariants", "acceptance"])
def test_semantic_change_changes_context_and_projection(kind):
    source = compiled_snapshot()
    first = ContextCompiler(source).compile("hello.txt")
    modified = deepcopy(source)
    key = "statement" if kind == "invariants" else "assertion"
    modified["requirements"][kind][0][key] = "Jiná povinnost."
    second = ContextCompiler(modified).compile("hello.txt")
    assert first["file_context_hash"] != second["file_context_hash"]
    assert projection_from_file_context("A3", "hello.txt", first).hash != projection_from_file_context("A3", "hello.txt", second).hash


def test_projection_selectors_reference_actual_compiled_values():
    compiled = ContextCompiler(compiled_snapshot()).compile("hello.txt")
    projection = projection_from_file_context("A3", "hello.txt", compiled)
    for binding in projection.components:
        key = binding.selector.rsplit("/", 1)[-1]
        assert compiled["working_context"][key] == projection.payload[binding.role]
    assert projection.payload["obligations"][0]["definition"]["statement"] == "Neukládat tajemství."
    assert projection.payload["acceptance"][0]["assertion"]
    assert projection.payload["project"]["runtime"] == "none"


def test_polling_transport_does_not_multiply_journal_retries():
    client = OpenAIClient("test")
    client._req = Mock(return_value={"id": "resp_test", "status": "completed"})
    client.retrieve_response("resp_test")
    client._req.assert_called_once_with("GET", "/responses/resp_test", max_attempts=1)


def test_modify_file_plan_is_refined_from_graph_without_changing_requirements():
    _, requirements = inputs()
    wrapper = {"plan": plan_data(), "files_to_add": ["old.txt"],
               "files_to_modify": [], "preserved_files": [], "baseline_findings": []}
    spine = spine_data("MODIFY", default_files("MODIFY"))
    result = reconcile_modify_plan(requirements, wrapper, spine, [{"path": "existing.txt"}])
    assert result["files_to_add"] == ["hello.txt"]
    assert result["preserved_files"] == ["existing.txt"]
    assert result["plan"] == wrapper["plan"]
    assert wrapper["files_to_add"] == ["old.txt"]


def test_clarification_is_not_reported_as_technical_failure(tmp_path):
    def mutate(name, value, context):
        if name == "A0R_REQUIREMENTS_V2":
            value["result"] = {"status": "blocked", "questions": [{
                "code": "scope_conflict", "question": "Má být výstup česky nebo anglicky?",
                "blocking": True, "source_refs": [],
            }]}
        return value
    worker, client, _ = scenario(tmp_path, "GENERATE", mutate=mutate)
    results, errors = run(worker, client)
    assert not errors
    assert results[0]["status"] == "needs_clarification"
    assert results[0]["questions"][0]["code"] == "scope_conflict"
    assert client.create_response.call_count == 1
    assert worker.log.bundle.run_record()["status"] == "needs_clarification"
