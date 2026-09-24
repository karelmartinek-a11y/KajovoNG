"""Regrese projekcí přípravy, místního inventáře a obrazových parametrů."""
from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import jsonschema
import pytest
from PIL import Image

from change_v2_fixtures import default_files, file_spec, plan_data, requirements_data, spine_data, scenario, _input_json
from preparation_v2_helpers import preparation_scenario, prepare
from kajovo.core.context_compiler import ContextCompiler, owned_obligations
from kajovo.core.contracts import ContractError
from kajovo.core.filescan import scan_tree
from kajovo.core.orchestration import preparation, resource_delivery
from kajovo.core.orchestration.source_pack import freeze_run_sources, source_context
from test_workflows import make_worker


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_detail_receives_exact_owned_definitions(mode, tmp_path):
    captured = []

    def mutate(name, value, context):
        data = value["result"]["data"]
        if name in {"A0R_REQUIREMENTS_V2", "B0R_REQUIREMENTS_V2"}:
            requirements = data if mode == "GENERATE" else data["change_requirements"]
            requirements["invariants"] = [{"id": "INV-1", "statement": "Zachovat identitu.",
                "requirement_ids": ["REQ-1"], "source_refs": requirements["requirements"][0]["source_refs"]}]
            requirements["flows"] = [{"id": "FLOW-1", "actor": "Uživatel", "preconditions": [],
                "steps": ["Přijmout vstup."], "postconditions": ["Uložený výsledek."],
                "failure_paths": ["Oznámit chybu."], "requirement_ids": ["REQ-1"]}]
            requirements["lifecycles"] = [{"id": "LIFE-1", "owner": "Výstup", "states": ["ready"],
                "transitions": [], "requirement_ids": ["REQ-1"]}]
        elif name in {"A2_SPINE_V2", "B2_SPINE_V2"}:
            data["obligation_owners"] = [{"obligation_id": key, "paths": ["hello.txt"],
                "reason": "Cíl vlastní povinnost."} for key in ("INV-1", "FLOW-1", "LIFE-1")]
        elif name in {"A2_FILE_SPEC_V1", "B2_FILE_SPEC_V1"}:
            captured.extend(deepcopy(context["owned_obligations"]))
            data["facets"] = [{"kind": "invariants", "definition": "Implementovat vlastněné povinnosti.",
                "obligation_ids": [row["obligation_id"] for row in captured]}]
        return value

    worker, client, _ = preparation_scenario(tmp_path, mode, mutate=mutate)
    prepare(worker, client)
    compiled = ContextCompiler(worker._delivery_snapshot).compile("hello.txt")
    assert captured == compiled["working_context"]["applicable_invariants"]
    assert {row["definition"]["id"] for row in captured} == {"INV-1", "FLOW-1", "LIFE-1"}


def obligation_fixture():
    requirements = requirements_data({"segments": []})
    requirements["invariants"] = [
        {"id": "OWN", "statement": "Vlastněná povinnost."},
        {"id": "FOREIGN", "statement": "Cizí povinnost."},
    ]
    spine = spine_data("GENERATE", default_files("GENERATE"))
    spine["obligation_owners"] = [
        {"obligation_id": "OWN", "paths": ["hello.txt"], "reason": "Vlastník."},
        {"obligation_id": "FOREIGN", "paths": ["other.txt"], "reason": "Jiný vlastník."},
    ]
    return requirements, spine


@pytest.mark.parametrize("ids", [[], ["FOREIGN"]])
def test_detail_cannot_omit_or_borrow_obligations(ids):
    requirements, spine = obligation_fixture()
    target = spine["files"][0]
    spec = file_spec(target, [])
    if ids:
        spec["facets"] = [{"kind": "invariants", "definition": "Definice.", "obligation_ids": ids}]
    with pytest.raises(ContractError, match="povinnost"):
        preparation.validate_file_spec_v1(SimpleNamespace(source_context={}), target, spine, requirements, spec)


def test_owned_projection_is_isolated_and_rejects_unknown_definition():
    requirements, spine = obligation_fixture()
    projection = owned_obligations(requirements, spine, "hello.txt")
    assert len(projection) == 1
    projection[0]["definition"]["statement"] = "Změna kopie."
    assert requirements["invariants"][0]["statement"] == "Vlastněná povinnost."
    requirements["invariants"] = []
    with pytest.raises(ContractError, match="neznámá povinnost"):
        owned_obligations(requirements, spine, "hello.txt")


def test_empty_inventory_preserves_filters_and_never_becomes_upload(tmp_path, monkeypatch):
    worker = make_worker(tmp_path, "MODIFY")
    root = tmp_path / "empty_project"
    root.mkdir()
    for name in ("__init__.py", "marker.txt", ".env", "blocked.txt", "denied.bin"):
        (root / name).write_bytes(b"")
    worker.cfg.in_dir = str(root)
    policy = worker.settings.security
    policy.allow_upload_sensitive = False
    policy.deny_extensions_in = [".bin"]
    policy.allow_extensions_in = None
    policy.deny_globs_in = ["blocked.txt"]
    policy.allow_globs_in = None
    items = scan_tree(str(root), root.name, [], [".bin"], None, ["blocked.txt"], None)
    assert {item.rel_path for item in items if item.inventory_only} == {"__init__.py", "marker.txt"}
    assert not any(item.uploadable for item in items)
    pack = freeze_run_sources(worker.cfg, worker.settings, worker.log, client=Mock())
    inventory, originals = preparation._inventory(worker)
    assert {row["path"] for row in inventory} == {"__init__.py", "marker.txt"}
    assert all(row["content"] == "" for row in originals)
    assert all(row["sha256"] == hashlib.sha256(b"").hexdigest() for row in originals)
    evidence = source_context(worker.log, pack)
    assert evidence["_provider_inputs"] == []
    assert evidence["attachments"] == []
    for artifact in worker.log.bundle.artifacts():
        if artifact["role"] == "in_project_file":
            assert Path(worker.log.paths.run_dir, artifact["path_in_bundle"]).read_bytes() == b""
    from kajovo.core.runs.attachments import _approved_project_items
    from kajovo.core.delivery_preparation import validate_modify_sources
    rescan = Mock(side_effect=AssertionError("Zmrazený inventář nesmí znovu skenovat IN."))
    monkeypatch.setattr("kajovo.core.filescan.scan_tree", rescan)
    items = _approved_project_items(worker, str(root), include_inventory=True)
    assert {item.rel_path for item in items} == {"__init__.py", "marker.txt"}
    assert _approved_project_items(worker, str(root)) == []
    structure = {"contract": "IMPLEMENTATION_GRAPH_V3", "spine": {"files": [
        {"path": "marker.txt", "action": "modify"}, {"path": "__init__.py", "action": "preserve"},
    ]}}
    validate_modify_sources(structure, str(root), items)
    rescan.assert_not_called()
    (root / "marker.txt").write_bytes(b"zmena")
    with pytest.raises(ContractError):
        validate_modify_sources(structure, str(root), items)


def dependency_compiler():
    files = default_files("GENERATE")
    for path, dependencies in (("consumer.txt", ["hello.txt"]), ("other.txt", [])):
        files.append({**deepcopy(files[0]), "path": path, "dependencies": dependencies,
                      "content_dependencies": dependencies})
    spine = spine_data("GENERATE", files)
    return ContextCompiler({"requirements": requirements_data({"segments": []}), "plan": plan_data(),
        "structure": {"contract": "IMPLEMENTATION_GRAPH_V3", "spine": spine,
            "file_specs": [{"path": row["path"], "spec": file_spec(row, [])} for row in spine["files"]]}})


@pytest.mark.parametrize("action", ["modify", "preserve"])
def test_modify_preparation_keeps_empty_original_and_action(tmp_path, action):
    files = [*default_files("MODIFY"), {"path": "empty.txt", "action": action}]
    worker, client, responder = scenario(tmp_path, "MODIFY", files=files)
    Path(worker.cfg.in_dir, "empty.txt").write_bytes(b"")
    worker.source_pack = freeze_run_sources(worker.cfg, worker.settings, worker.log, client=client)
    worker.source_context = source_context(worker.log, worker.source_pack)
    prepare(worker, client)
    rows = worker._delivery_snapshot["structure"]["spine"]["files"]
    assert next(row for row in rows if row["path"] == "empty.txt")["action"] == action
    b1 = next(payload for payload in responder.calls if payload["text"]["format"]["name"] == "B1_PLAN_V2")
    assert any(row["path"] == "empty.txt" and row["byte_length"] == 0
               for row in _input_json(b1)["project_inventory"])
    if action == "modify":
        detail = next(_input_json(payload) for payload in responder.calls
            if payload["text"]["format"]["name"] == "B2_FILE_SPEC_V1"
            and _input_json(payload)["target"]["path"] == "empty.txt")
        assert detail["original_target"]["content"] == ""


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("action", ["modify", "preserve"])
def test_empty_original_survives_complete_modify_workflow(tmp_path, batch, action):
    from change_v2_fixtures import run, batch_output_rows, raw_jsonl, _file_input_json
    from kajovo.core.batch_completion import complete_saved_batch
    from kajovo.core.recoverable_artifacts import load_run_state
    from kajovo.core.runs.attachments import _approved_project_items

    files = [
        {"path": "empty.txt", "action": action},
        {"path": "consumer.txt", "action": "add", "dependencies": ["empty.txt"],
         "content_dependencies": ["empty.txt"] if action == "preserve" else []},
    ]
    outputs = {"empty.txt": "Nový obsah.\n", "consumer.txt": "Hotový konzument.\n"}
    worker, client, responder = scenario(tmp_path, "MODIFY", batch=batch, files=files, content_by_path=outputs)
    original = Path(worker.cfg.in_dir, "empty.txt")
    original.write_bytes(b"")
    results, errors = run(worker, client)
    assert results and not errors
    assert _approved_project_items(worker, worker.cfg.in_dir) == []
    items = _approved_project_items(worker, worker.cfg.in_dir, include_inventory=True)
    assert len(items) == 1
    assert items[0].inventory_only is True and items[0].uploadable is False
    assert items[0].sha256 == hashlib.sha256(b"").hexdigest()
    assert Path(items[0].frozen_path).read_bytes() == b""
    if batch:
        contexts = [json.loads(row["body"]["input"])["file_context"]["working_context"]
                    for row in client.create_batch.call_args.kwargs["_prevalidated_rows"]]
        state = load_run_state(worker.log.paths.run_dir)
        client.retrieve_batch.return_value = {
            "id": state["batch_id"], "status": "completed", "input_file_id": "file_batch_input",
            "endpoint": "/v1/responses", "output_file_id": "file_output",
        }
        client.file_content.return_value = raw_jsonl(batch_output_rows(state["generate_batch"], outputs))
        result = complete_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], worker.settings)
        assert result["status"] == "files_complete_unverified"
        assert client.upload_file.call_count == 1
        assert client.upload_file.call_args.kwargs["purpose"] == "batch"
    else:
        contexts = [_file_input_json(payload)["file_context"]["working_context"]
                    for payload in responder.calls if payload["text"]["format"]["name"] == "FILE_CONTENT_V1"]
        assert results[0]["status"] == "files_complete_unverified"
        client.upload_file.assert_not_called()
    if action == "preserve":
        consumer = next(context for context in contexts if context["target_file"]["path"] == "consumer.txt")
        artifact = consumer["verified_dependency_artifacts"][0]
        assert artifact["content"] == "" and artifact["origin"] == "preserved_original"
    else:
        target = next(context for context in contexts if context["target_file"]["path"] == "empty.txt")
        assert next(row for row in target["relevant_source_excerpts"] if row["path"] == "empty.txt")["content"] == ""
    staged = {row["path"]: row for row in load_run_state(worker.log.paths.run_dir)["staged_files"]}
    assert set(staged) == ({"consumer.txt", "empty.txt"} if action == "modify" else {"consumer.txt"})
    for path, row in staged.items():
        assert Path(worker.log.paths.run_dir, row["staged_path"]).read_text(encoding="utf-8") == outputs[path]
    assert original.read_bytes() == b""


def verified(compiler, content):
    return {"hello.txt": {"content": content, "output_hash": hashlib.sha256(content.encode()).hexdigest(),
        "contract_hash": compiler.provider_hash("hello.txt"), "validation_status": "verified"}}


def test_invalidated_compares_separate_verified_content_maps():
    compiler = dependency_compiler()
    old = verified(compiler, "Původní obsah.")
    assert compiler.invalidated(compiler, verified_artifacts=old, previous_verified_artifacts=old) == []
    assert compiler.invalidated(compiler, verified_artifacts=verified(compiler, "Nový obsah."),
                               previous_verified_artifacts=old) == ["consumer.txt"]
    with pytest.raises(ContractError, match="provider artefakt"):
        compiler.invalidated(compiler, verified_artifacts=old)


def test_empty_preserved_original_remains_verified_content_dependency():
    snapshot = dependency_compiler().snapshot
    snapshot["structure"]["spine"]["files"][0]["action"] = "preserve"
    snapshot["structure"]["file_specs"] = [row for row in snapshot["structure"]["file_specs"]
                                             if row["path"] != "hello.txt"]
    snapshot["original_hashes"] = {"hello.txt": hashlib.sha256(b"").hexdigest()}
    compiled = ContextCompiler(snapshot).compile("consumer.txt", originals={"hello.txt": ""})
    artifact = compiled["working_context"]["verified_dependency_artifacts"][0]
    assert artifact["content"] == ""
    assert artifact["output_hash"] == hashlib.sha256(b"").hexdigest()
    assert artifact["origin"] == "preserved_original"


def image_delivery():
    return {"path": "asset.png", "producer": "image_workflow", "source_or_task_id": "IMAGE-1",
        "criterion_ids": ["AC-1"], "image_production": {"version": 1, "size": "1536x1024", "background": "transparent"}}


@pytest.mark.parametrize("stage", ["A2_SPINE", "B2_SPINE", "A2Q", "B2Q"])
def test_image_parameters_are_required_on_new_wire_but_legacy_read_is_unchanged(stage):
    spine = spine_data("GENERATE", default_files("GENERATE"))
    spine["resource_deliveries"] = [image_delivery()]
    schema = preparation.FORMATS[stage]["format"]["schema"]
    data = spine if stage.endswith("SPINE") else {"corrected_spine": spine, "corrected_file_specs": [], "findings": []}
    candidate = {"result": {"status": "ready", "data": data}}
    jsonschema.validate(candidate, schema)
    del spine["resource_deliveries"][0]["image_production"]
    before = deepcopy(spine)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(candidate, schema)
    jsonschema.validate(spine, preparation.GRAPH_SCHEMA["properties"]["spine"])
    assert spine == before


def test_image_request_and_frozen_projection_keep_explicit_parameters(monkeypatch):
    delivery = image_delivery()
    target = {"path": "asset.png", "purpose": "Průhledné logo.", "requirement_ids": []}
    graph = {"spine": {"resource_deliveries": [delivery]}}
    worker = SimpleNamespace(log=Mock())
    client = Mock()
    client.create_image.return_value = {"id": "image_result", "data": [{"b64_json": "YQ=="}]}
    repo = Mock()
    order = SimpleNamespace(attempt_id="attempt", order_hash="order")
    prepare_order = Mock(return_value=(repo, order))
    monkeypatch.setattr(resource_delivery, "_prepare_image_order", prepare_order)
    monkeypatch.setattr(resource_delivery, "_load_saved_resource_image", lambda *args: None)
    monkeypatch.setattr(resource_delivery, "_decode_resource_image_response", lambda *args, **kwargs: b"image")
    resource_delivery._generate_image(worker, client, graph, target, delivery)
    body = client.create_image.call_args.args[1]
    assert body["size"] == "1536x1024" and body["background"] == "transparent"
    projection = prepare_order.call_args.args[3]
    assert projection["image_production"] == delivery["image_production"]
    delivery["image_production"]["size"] = "1024x1024"
    assert projection["image_production"]["size"] == "1536x1024"


def test_legacy_image_plan_is_not_silently_given_new_parameters():
    delivery = image_delivery()
    del delivery["image_production"]
    before = deepcopy(delivery)
    with pytest.raises(ContractError, match="legacy plán"):
        resource_delivery._image_request_body(SimpleNamespace(), {}, {"path": "asset.png"}, delivery)
    assert delivery == before


def test_image_order_archives_exact_request_before_submission(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker._delivery_expected_target_hashes = {"asset.png": None}
    delivery = image_delivery()
    target = {"path": "asset.png", "requirement_ids": []}
    graph = {"spine": {"resource_deliveries": [delivery]}}
    body = resource_delivery._image_request_body(worker, graph, target, delivery)
    projection = {"image_production": deepcopy(delivery["image_production"])}
    resource_delivery.repository_for_logger(worker.log).register_run(
        worker.log.run_id,
        lineage_id=worker.log.run_id,
        scope_hash=resource_delivery.canonical_sha256(graph),
        policy_hash=resource_delivery.canonical_sha256(resource_delivery.image_policy()),
        config={"mode": worker.cfg.mode},
        approval_id=f"user-start:{worker.log.run_id}",
        status="running",
    )
    _, order = resource_delivery._prepare_image_order(worker, "asset.png", body, projection)
    request = next(Path(worker.log.paths.run_dir).rglob("*resource_image_request_*.json"))
    evidence = json.loads(request.read_text(encoding="utf-8"))
    assert evidence["payload"] == body
    assert evidence["projection"] == projection
    assert evidence["work_order_hash"] == order.order_hash
    assert order.contract_name == "PROJECT_IMAGE_RESOURCE_V2"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
@pytest.mark.parametrize("saved_response", [False, True])
def test_legacy_image_checkpoint_restores_without_new_wire_validation(
    tmp_path, mode, quality, saved_response,
):
    from kajovo.core.delivery_preparation import validate_preparation_snapshot
    from kajovo.core.orchestration.contracts import canonical_sha256
    from kajovo.core.orchestration.manual_resources import graph_from_state

    worker, client, _ = preparation_scenario(tmp_path, mode, quality=quality)
    requirements = requirements_data(worker.source_context)
    plan = plan_data()
    files = default_files(mode)
    files[0].update(path="asset.png", kind="binary_required")
    spine = spine_data(mode, files)
    delivery = image_delivery()
    del delivery["image_production"]
    spine["resource_deliveries"] = [delivery]
    graph = {
        "contract": "IMPLEMENTATION_GRAPH_V3", "mode": mode,
        "source_snapshot_hash": worker.source_pack.hash,
        "requirements_hash": canonical_sha256(requirements), "plan_hash": canonical_sha256(plan),
        "spine": spine, "file_specs": [],
        "verification_profile_ids": list(worker.cfg.verification_profile_ids or []),
    }
    prefix = "A" if mode == "GENERATE" else "B"
    checkpoint = {
        "version": 2, "mode": mode, "maximum_quality": quality,
        "source_snapshot_hash": worker.source_pack.hash,
        "requirements": requirements, "plan": plan, "spine": spine,
        "file_specs": [], "graph": graph, "quality_gate_findings": [],
        "canonical_stage": prefix + ("2Q" if quality else "2"), "response_id": "resp_legacy",
    }
    if mode == "MODIFY":
        inventory, _ = preparation._inventory(worker)
        checkpoint["requirements"] = {
            "change_requirements": requirements, "preserve": [], "migration_requirements": [],
        }
        checkpoint["plan"] = {
            "plan": plan, "files_to_add": ["asset.png"], "files_to_modify": [],
            "preserved_files": sorted(row["path"] for row in inventory), "baseline_findings": [],
        }
    checkpoint["snapshot_hash"] = canonical_sha256(checkpoint)
    before = deepcopy(checkpoint)
    archived = Path(worker.log.save_json("manifests", "legacy_preparation_snapshot_v2", checkpoint))
    archived_bytes = archived.read_bytes()
    worker.cfg.preparation_snapshot = validate_preparation_snapshot(checkpoint, mode, quality)
    assert graph_from_state({"preparation_snapshot": checkpoint}) == graph
    assert graph_from_state({"generate_batch": {"snapshot": {"structure": graph}}}) == graph
    _, restored, _ = prepare(worker, client)
    client.create_response.assert_not_called()
    assert restored == graph
    assert worker.cfg.preparation_snapshot == before

    if saved_response:
        stream = io.BytesIO()
        Image.new("RGBA", (8, 8), (0, 0, 0, 0)).save(stream, format="PNG")
        binary = stream.getvalue()
        worker.log.save_json("responses", resource_delivery._resource_image_response_name("asset.png"), {
            "target_path": "asset.png", "provider_id": "image_legacy",
            "response": {"data": [{"b64_json": base64.b64encode(binary).decode("ascii")}]},
        })
        assert resource_delivery._generate_image(worker, client, restored, spine["files"][0], delivery) == binary
    else:
        with pytest.raises(ContractError, match="legacy plán"):
            resource_delivery._generate_image(worker, client, restored, spine["files"][0], delivery)
    client.create_image.assert_not_called()
    assert checkpoint == before
    assert archived.read_bytes() == archived_bytes
