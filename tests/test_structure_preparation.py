"""Příprava vztahů A2 a opravy specifikace před generováním souborů."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import (
    build_manifest, encode_requests, prepare_structure, validate_structure,
)
from test_workflows import make_worker, response
from delivery_fixtures import plan_payload, requirements_payload, structure_payload


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


@pytest.mark.parametrize("batch", [False, True])
def test_new_a2_is_prepared_before_live_or_batch_generation(tmp_path, batch):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = batch
    original = structure_payload(files=graph_specification()["files"], interfaces=graph_specification()["interfaces"])
    base_prepared, additions = prepare_structure(graph_specification())
    prepared = structure_payload(files=base_prepared["files"], interfaces=base_prepared["interfaces"])
    client = Mock()
    client.create_response.side_effect = [response(0, requirements_payload()), response(1, plan_payload()), response(2, original)]
    client.upload_file.return_value = {"id": "file_input"}
    client.create_batch.return_value = {"id": "batch_work"}
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client), patch.object(worker, "_gen_file_chunks", return_value=("obsah", "resp_file")) as generate:
        worker.run()
    assert not errors
    assert client.create_response.call_count == 3
    run = Path(worker.log.paths.run_dir)
    evidence = json.loads(next((run / "manifests").glob("*A2_prepared_candidate_0*.json")).read_text(encoding="utf-8"))
    assert evidence["structure"] == prepared
    assert evidence["added_dependencies"] == additions
    original_record = json.loads(next((run / "responses").glob("*A2_response*.json")).read_text(encoding="utf-8"))
    assert json.loads(original_record["output_text"]) == original
    snapshot = json.loads((run / "run_state.json").read_text(encoding="utf-8"))["preparation_snapshot"]
    assert snapshot["structure"] == prepared
    assert snapshot["canonical_stage"] == "A2"
    if batch:
        state = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
        assert state["generate_batch"]["snapshot"]["structure"] == prepared
        rows = [json.loads(line) for line in Path(client.upload_file.call_args.args[0]).read_text(encoding="utf-8").splitlines()]
        assert len(rows) == len(prepared["files"])
        assert all(json.loads(row["body"]["input"])["file_context"]["working_context"]["target_file"] in prepared["files"] for row in rows)
        generate.assert_not_called()
    else:
        assert results[0]["structure"] == prepared
        assert generate.call_count == len(prepared["files"])
        client.create_batch.assert_not_called()
        resume = json.loads(next((run / "manifests").glob("*resume_structure*.json")).read_text(encoding="utf-8"))
        assert resume["resume_files"] == prepared["files"]


def test_repair_receives_all_errors_and_current_prepared_manifest(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = True
    bad = structure_payload(files=graph_specification()["files"], interfaces=graph_specification()["interfaces"])
    bad["files"][1]["requires"].append("unknown-a")
    bad["files"][3]["requires"].append("unknown-b")
    fixed_base, _ = prepare_structure(graph_specification())
    fixed = structure_payload(files=fixed_base["files"], interfaces=fixed_base["interfaces"])
    good = structure_payload(files=graph_specification()["files"], interfaces=graph_specification()["interfaces"])
    client = Mock()
    client.create_response.side_effect = [response(0, requirements_payload()), response(1, plan_payload()), response(2, bad), response(3, good)]
    client.upload_file.return_value = {"id": "file_input"}
    client.create_batch.return_value = {"id": "batch_work"}
    errors = []
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert client.create_response.call_count == 4
    repair = client.create_response.call_args_list[3].args[0]
    context = json.loads(repair["input"][0]["content"][0]["text"])
    assert "unknown-a" in context["validation_errors"] and "unknown-b" in context["validation_errors"]
    assert context["structure"] == bad
    assert context["requirements"] == requirements_payload()
    assert context["plan"] == plan_payload()
    request_log = json.loads(next((Path(worker.log.paths.run_dir) / "requests").glob("*A2_request_1*.json")).read_text(encoding="utf-8"))
    assert json.loads(request_log["payload"]["input"][0]["content"][0]["text"]) == context
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["generate_batch"]["snapshot"]["structure"] == fixed


@pytest.mark.parametrize("batch", [False, True])
def test_unrepairable_manifest_blocks_all_file_generation(tmp_path, batch):
    worker = make_worker(tmp_path, "GENERATE")
    worker.cfg.send_as_c = batch
    spec = structure_payload(files=graph_specification()["files"], interfaces=graph_specification()["interfaces"])
    spec["files"][5]["provides"] = []
    client = Mock()
    client.create_response.side_effect = [response(0, requirements_payload()), response(1, plan_payload())] + [response(i + 2, spec) for i in range(3)]
    errors = []
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client), patch.object(worker, "_gen_file_chunks") as generate:
        worker.run()
    assert errors and "controller" in errors[0]
    assert client.create_response.call_count == 4
    client.create_batch.assert_not_called()
    client.upload_file.assert_not_called()
    generate.assert_not_called()
