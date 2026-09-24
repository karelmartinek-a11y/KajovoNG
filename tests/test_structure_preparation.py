"""Příprava vztahů A2 a opravy specifikace před generováním souborů."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import (
    build_manifest, encode_requests, prepare_structure, validate_structure,
)
from kajovo.core.orchestration.preparation import _prepare_spine, validate_spine_v1
from change_v2_fixtures import (
    format_names, plan_data, requirements_data, run, scenario, spine_data,
)


def graph_specification():
    def file(path, provides=(), requires=(), dependencies=()):
        return {"path": path, "purpose": "Testovací modul", "language": "text", "kind": "text",
                "dependencies": list(dependencies), "provides": list(provides),
                "requires": list(requires), "behavior": "Použije deklarovaná rozhraní."}
    return {
        "contract": "A2_STRUCTURE", "version": 2, "rules": [], "packages": [],
        "interfaces": [{"id": value, "definition": value + "() -> None"}
                       for value in ("entry", "controller", "engine")],
        "files": [
            file("launcher.bat", requires=["entry"], dependencies=["requirements.txt"]),
            file("screen_start.py", requires=["controller"]),
            file("screen_game.py", requires=["controller", "engine"], dependencies=["engine.py"]),
            file("dialog.py", requires=["controller", "engine"]),
            file("main.py", provides=["entry"]),
            file("controller.py", provides=["controller"]),
            file("engine.py", provides=["engine"]),
            file("requirements.txt"),
        ],
    }


def test_all_five_missing_links_are_prepared_without_mutating_response():
    original = graph_specification()
    before = copy.deepcopy(original)
    with pytest.raises(ContractError) as failure:
        validate_structure(original)
    for path in ("launcher.bat", "screen_start.py", "screen_game.py", "dialog.py"):
        assert path in str(failure.value)
    prepared, additions = prepare_structure(original)
    assert len(additions) == 5
    assert original == before
    assert prepared["files"][0]["dependencies"] == ["requirements.txt", "main.py"]
    assert prepared["files"][2]["dependencies"] == ["engine.py", "controller.py"]
    validate_structure(prepared)
    repeated, second_additions = prepare_structure(prepared)
    assert repeated == prepared and second_additions == []


def test_multiple_requirements_do_not_duplicate_dependency():
    spec = graph_specification()
    spec["files"][5]["provides"].append("engine")
    spec["files"][6]["provides"] = []
    prepared, _ = prepare_structure(spec)
    assert prepared["files"][3]["dependencies"] == ["controller.py"]
    validate_structure(prepared)


def test_self_provided_interface_never_adds_self_dependency():
    spec = graph_specification()
    spec["files"][5]["requires"] = ["controller"]
    prepared, _ = prepare_structure(spec)
    assert prepared["files"][5]["dependencies"] == []
    validate_structure(prepared)


def test_multiple_providers_are_not_guessed_and_existing_choice_is_respected():
    spec, _ = prepare_structure(graph_specification())
    alternative = copy.deepcopy(spec["files"][5])
    alternative["path"] = "alternative.py"
    spec["files"].append(alternative)
    spec["files"][1]["dependencies"] = []
    spec["files"][2]["dependencies"] = ["engine.py", "alternative.py"]
    prepared, additions = prepare_structure(spec)
    assert prepared == spec and not additions
    with pytest.raises(ContractError) as failure:
        validate_structure(prepared)
    message = str(failure.value)
    assert "screen_start.py" in message
    assert "controller" in message and "controller.py" in message and "alternative.py" in message
    assert "screen_game.py" not in message


def test_all_unresolvable_relationship_errors_are_reported_together():
    spec, _ = prepare_structure(graph_specification())
    spec["files"][5]["provides"] = []
    spec["files"][2]["dependencies"].append("missing.py")
    spec["files"][2]["requires"].append("unknown-interface")
    spec["files"][3]["provides"].append("unknown-export")
    prepared, _ = prepare_structure(spec)
    with pytest.raises(ContractError) as failure:
        validate_structure(prepared)
    message = str(failure.value)
    for value in ("screen_start.py", "screen_game.py", "dialog.py", "controller",
                  "missing.py", "unknown-interface", "unknown-export"):
        assert value in message
    assert prepared["files"][2]["dependencies"] == spec["files"][2]["dependencies"]
    assert prepared["files"][5]["provides"] == []


@pytest.mark.parametrize("fault", ["shape", "duplicate_path", "duplicate_interface", "unsafe_path"])
def test_preparation_rejects_invalid_identity_before_deriving_links(fault):
    spec = graph_specification()
    if fault == "shape":
        spec["files"][0]["requires"] = "entry"
    elif fault == "duplicate_path":
        spec["files"][0]["path"] = spec["files"][1]["path"]
    elif fault == "duplicate_interface":
        spec["interfaces"].append(copy.deepcopy(spec["interfaces"][0]))
    else:
        spec["files"][0]["path"] = "../escape.py"
    before = copy.deepcopy(spec)
    with pytest.raises((ContractError, ValueError)):
        prepare_structure(spec)
    assert spec == before


def test_manifest_creation_remains_strict_and_existing_requests_stay_unchanged():
    original = graph_specification()
    with pytest.raises(ContractError):
        build_manifest("run", "test", {}, original, "gpt-4.1", 0)
    prepared, _ = prepare_structure(original)
    from delivery_fixtures import implementation_fixture
    implementation_fixture(prepared)
    manifest = build_manifest("run", "test", {}, prepared, "gpt-4.1", 0)
    before = copy.deepcopy(manifest)
    raw = encode_requests(manifest)
    prepare_structure(manifest["snapshot"]["structure"])
    assert manifest == before
    assert encode_requests(manifest) == raw


def spine_specification():
    prepared, _ = prepare_structure(graph_specification())
    files = [{**row, "action": "generate"} for row in prepared["files"]]
    spine = spine_data("GENERATE", files)
    spine["interfaces"] = [
        {
            **interface, "version": 1, "kind": "symbol",
            "input_contract": "Bez argumentů.", "output_contract": "None",
            "error_semantics": "Výjimka se předá volajícímu.",
            "lifecycle": "Volání bez trvalého stavu.",
            "providers": [row["path"] for row in files if interface["id"] in row["provides"]],
            "consumers": [row["path"] for row in files if interface["id"] in row["requires"]],
            "requirement_ids": ["REQ-1"],
        }
        for interface in prepared["interfaces"]
    ]
    return spine


def graph_scenario(tmp_path, batch, candidates):
    spine = spine_specification()
    remaining = iter(candidates)

    def mutate(name, value, data):
        if name == "A2_SPINE_V2":
            value["result"]["data"] = copy.deepcopy(next(remaining))
        elif name == "A2_FILE_SPEC_V1":
            value["result"]["data"]["interface_bindings"] = [
                {"id": row["id"], "version": row["version"]}
                for row in data["interfaces"]
            ]
        return value

    return scenario(tmp_path, "GENERATE", batch=batch, files=spine["files"], mutate=mutate)


def test_v2_dependency_preparation_is_idempotent_and_keeps_content_edges_explicit():
    original = spine_specification()
    original["files"][1]["dependencies"] = []
    before = copy.deepcopy(original)
    prepared, additions = _prepare_spine(original)
    assert original == before
    assert prepared["files"][1]["dependencies"] == ["controller.py"]
    assert prepared["files"][1]["content_dependencies"] == []
    assert prepared["files"][1]["dependency_content_mode"] == "contract"
    assert len(additions) == 1
    assert _prepare_spine(prepared) == (prepared, [])


def test_v2_dependency_preparation_never_guesses_provider_or_adds_self_dependency():
    spine = spine_specification()
    alternative = copy.deepcopy(spine["files"][5])
    alternative["path"] = "alternative.py"
    spine["files"].append(alternative)
    controller = next(row for row in spine["interfaces"] if row["id"] == "controller")
    controller["providers"].append("alternative.py")
    spine["files"][1]["dependencies"] = []
    spine["files"][2]["dependencies"] = ["engine.py", "alternative.py"]
    spine["files"][5]["requires"] = ["controller"]
    controller["consumers"].append("controller.py")
    before = copy.deepcopy(spine)
    assert _prepare_spine(spine) == (before, [])
    assert spine == before


def test_v2_reports_all_unresolvable_relationships_together():
    spine = spine_specification()
    spine["files"][5]["provides"] = []
    next(row for row in spine["interfaces"] if row["id"] == "controller")["providers"] = []
    spine["files"][2]["dependencies"].append("missing.py")
    spine["files"][2]["requires"].append("unknown-interface")
    spine["files"][3]["provides"].append("unknown-export")
    before = copy.deepcopy(spine)
    prepared, additions = _prepare_spine(spine)
    assert not additions
    with pytest.raises(ContractError) as failure:
        validate_spine_v1("GENERATE", requirements_data({}), plan_data(), prepared)
    for value in ("screen_start.py", "screen_game.py", "dialog.py", "controller",
                  "missing.py", "unknown-interface", "unknown-export"):
        assert value in str(failure.value)
    assert spine == before


@pytest.mark.parametrize("batch", [False, True])
def test_new_a2_is_prepared_before_live_or_batch_generation(tmp_path, batch):
    prepared = spine_specification()
    original = copy.deepcopy(prepared)
    for row, legacy in zip(original["files"], graph_specification()["files"], strict=True):
        row["dependencies"] = legacy["dependencies"]
    before = copy.deepcopy(original)
    worker, client, responder = graph_scenario(tmp_path, batch, [original])
    results, errors = run(worker, client)
    assert results and not errors
    assert original == before
    names = format_names(responder)
    assert names[:3] == ["A0R_REQUIREMENTS_V2", "A1_PLAN_V2", "A2_SPINE_V2"]
    assert names[3:11] == ["A2_FILE_SPEC_V1"] * len(prepared["files"])
    run_dir = Path(worker.log.paths.run_dir)
    original_record = json.loads(next((run_dir / "responses").glob("*A2_SPINE_v2_response_0*.json")).read_text(encoding="utf-8"))
    assert json.loads(original_record["output_text"])["result"]["data"] == original
    evidence = json.loads(next((run_dir / "manifests").glob("*A2_SPINE_prepared_candidate_0*.json")).read_text(encoding="utf-8"))
    assert evidence["spine"] == prepared
    assert len(evidence["added_dependencies"]) == 5
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    checkpoint = state["preparation_snapshot"]
    graph = checkpoint["graph"]
    assert graph["contract"] == "IMPLEMENTATION_GRAPH_V3"
    assert graph["spine"] == prepared
    assert len(graph["file_specs"]) == len(prepared["files"])
    assert checkpoint["canonical_stage"] == "A2"
    if batch:
        assert state["generate_batch"]["snapshot"]["structure"] == graph
        rows = [json.loads(line) for line in Path(client.upload_file.call_args.args[0]).read_text(encoding="utf-8").splitlines()]
        assert len(rows) == len(prepared["files"])
        assert all(json.loads(row["body"]["input"])["file_context"]["working_context"]["target_file"] in prepared["files"] for row in rows)
        assert "FILE_CONTENT_V1" not in names
    else:
        assert results[0]["structure"] == graph
        assert names[11:] == ["FILE_CONTENT_V1"] * len(prepared["files"])
        client.create_batch.assert_not_called()
        resume = json.loads(next((run_dir / "manifests").glob("*resume_structure*.json")).read_text(encoding="utf-8"))
        assert resume["resume_files"] == prepared["files"]


def test_repair_receives_all_errors_and_current_prepared_manifest(tmp_path):
    fixed = spine_specification()
    bad = copy.deepcopy(fixed)
    bad["files"][1]["requires"].append("unknown-a")
    bad["files"][3]["requires"].append("unknown-b")
    worker, client, responder = graph_scenario(tmp_path, True, [bad, fixed])
    worker.cfg.auto_repair = "within_approval"
    results, errors = run(worker, client)
    assert results and not errors
    requests = [payload for payload in responder.calls if payload["text"]["format"]["name"] == "A2_SPINE_V2"]
    assert len(requests) == 2
    original_input = json.loads(requests[0]["input"][0]["content"][0]["text"])
    run_dir = Path(worker.log.paths.run_dir)
    context = json.loads(requests[1]["input"][0]["content"][0]["text"])
    assert context["input"] == original_input
    assert context["repair"]["candidate"] == bad
    for value in ("screen_start.py", "dialog.py", "unknown-a", "unknown-b"):
        assert value in context["repair"]["error"]
    request_log = json.loads(next((run_dir / "requests").glob("*A2_SPINE_v2_request_1*.json")).read_text(encoding="utf-8"))
    assert json.loads(request_log["payload"]["input"][0]["content"][0]["text"]) == context
    for attempt, candidate in enumerate((bad, fixed)):
        record = json.loads(next((run_dir / "responses").glob(f"*A2_SPINE_v2_response_{attempt}*.json")).read_text(encoding="utf-8"))
        assert json.loads(record["output_text"])["result"]["data"] == candidate
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["generate_batch"]["snapshot"]["structure"]["spine"] == fixed


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("repeated", [False, True])
def test_unrepairable_manifest_blocks_all_file_generation(tmp_path, batch, repeated):
    spec = spine_specification()
    spec["files"][5]["provides"] = []
    next(row for row in spec["interfaces"] if row["id"] == "controller")["providers"] = []
    candidates = [copy.deepcopy(spec) for _ in range(3)]
    if not repeated:
        for index, candidate in enumerate(candidates):
            candidate["files"][0]["purpose"] = f"Odlišný kandidát {index}"
    worker, client, responder = graph_scenario(tmp_path, batch, candidates)
    worker.cfg.auto_repair = "within_approval"
    with patch.object(worker, "_gen_file_chunks") as generate:
        results, errors = run(worker, client)
    assert not results and errors and "controller" in errors[0]
    assert ("NO_PROGRESS" in errors[0]) is repeated
    assert format_names(responder) == ["A0R_REQUIREMENTS_V2", "A1_PLAN_V2"] + ["A2_SPINE_V2"] * (2 if repeated else 3)
    client.create_batch.assert_not_called()
    client.upload_file.assert_not_called()
    generate.assert_not_called()


def test_repeated_wire_invalid_spine_stops_before_third_paid_request(tmp_path):
    def malformed(name, value, data):
        if name == "A2_SPINE_V2":
            value["result"]["data"]["files"][0]["requires"] = "není seznam"
        return value

    worker, client, responder = scenario(tmp_path, "GENERATE", batch=True, mutate=malformed)
    worker.cfg.auto_repair = "within_approval"
    results, errors = run(worker, client)
    assert not results and errors and "NO_PROGRESS" in errors[0]
    assert format_names(responder) == ["A0R_REQUIREMENTS_V2", "A1_PLAN_V2"] + ["A2_SPINE_V2"] * 2
    client.upload_file.assert_not_called()
    client.create_batch.assert_not_called()
