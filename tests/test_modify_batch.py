"""Regrese samostatných B3 dávek, obnovy a ochrany původního OUT."""

import copy
import hashlib
import json
from unittest.mock import Mock, patch

import pytest

from kajovo.core.batch_completion import complete_saved_batch, recover_unknown_submission
from kajovo.core.batch_submit import submit_verified_batch
from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import build_manifest, digest, encode_requests, import_results, repeat_saved_batch
from kajovo.core.requirements import stage_instructions
from test_generate_batch import outputs, raw, specification


def modify_manifest(model="gpt-4.1-nano", maximum_quality=False):
    struct = specification()
    struct["contract"] = "B2_STRUCTURE"
    struct["touched_files"] = struct.pop("files")
    for file, action in zip(struct["touched_files"], ("modify", "add"), strict=True):
        file.update(action=action, intent="Dokončit změnu")
    return build_manifest("run_modify", "změna", {"contract": "B1_PLAN"}, struct, model, 0.3,
                          mode="MODIFY", originals={"maths.py": "původní\n" * 1000},
                          maximum_quality=maximum_quality)


def modify_outputs(manifest):
    result = outputs(manifest)
    if manifest.get("version") == 3:
        return result
    for item, row in zip(result, manifest["requests"], strict=True):
        payload = json.loads(item["response"]["body"]["output_text"])
        context, _ = json.JSONDecoder().raw_decode(row["body"]["input"])
        payload.update(contract="B3_FILE", action=context["file"]["action"])
        item["response"]["body"]["output_text"] = json.dumps(payload)
    return result


def save_state(tmp_path, manifest):
    run = tmp_path / "RUN_test"
    run.mkdir()
    (run / "requests").mkdir()
    out = tmp_path / "out"
    out.mkdir()
    original = "původní\n" * 1000
    (out / "maths.py").write_text(original, encoding="utf-8", newline="")
    manifest["overwrite_hashes"] = {"maths.py": hashlib.sha256(original.encode()).hexdigest()}
    state = {"generate_batch": manifest, "batch_id": "batch_original", "out_dir": str(out)}
    (run / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    return run, out


def test_modify_rows_include_full_original_and_action():
    manifest = modify_manifest()
    assert manifest["version"] == 3 and manifest["mode"] == "MODIFY"
    assert manifest["snapshot"]["requirements"] is None
    assert manifest["snapshot"]["maximum_quality"] is False
    assert len(manifest["requests"]) == 2
    for row, action in zip(manifest["requests"], ("modify", "add"), strict=True):
        body = row["body"]
        context = json.loads(body["input"])
        assert body["instructions"].startswith(stage_instructions("B3_FILE", batch=True))
        assert "additionalProperties" not in body["instructions"]
        sources = context["file_context"]["working_context"]["relevant_source_excerpts"]
        assert {s["path"]: s["content"] for s in sources} == {"maths.py": "původní\n" * 1000}
        assert set(body["text"]["format"]["schema"]["properties"]) == {"content"}
        assert context["file"]["action"] == action
        assert manifest["work_orders"][row["custom_id"]]["target_path"] == context["file"]["path"]
        assert "_B3_" in row["custom_id"]
        assert "originals" not in context


@pytest.mark.parametrize("model,effort", [("gpt-4.1-nano", None), ("gpt-5-mini", "high"), ("gpt-5.2", "xhigh")])
def test_batch_quality_uses_model_matrix(model, effort):
    manifest = modify_manifest(model, True)
    assert manifest["snapshot"]["maximum_quality"] is True
    for row in manifest["requests"]:
        assert row["body"].get("reasoning", {}).get("effort") == effort
        if effort:
            assert "temperature" not in row["body"]


def test_modify_requires_original_before_submission():
    manifest = modify_manifest()
    with pytest.raises(ContractError, match="původní obsah"):
        build_manifest("r", "p", {}, manifest["snapshot"]["structure"], "gpt-4.1-nano", 0, mode="MODIFY")


@pytest.mark.parametrize("fault", ["contract", "action", "path", "chunk"])
def test_modify_import_validates_file_contract(tmp_path, fault):
    manifest = modify_manifest()
    rows = modify_outputs(manifest)
    body = rows[0]["response"]["body"]
    payload = json.loads(body["output_text"])
    if fault == "chunk":
        payload["chunking"] = {"has_more": True}
    else:
        payload[fault] = {"contract": "A3_FILE", "action": "add", "path": "foreign.py"}[fault]
    body["output_text"] = json.dumps(payload)
    result = import_results(manifest, [raw(rows)], str(tmp_path))
    assert result["status"] == "partial"
    assert result["written"] == ["main.py"]
    assert not (tmp_path / "maths.py").exists()


def test_modify_completion_protects_original_and_reimport(tmp_path):
    manifest = modify_manifest()
    run, out = save_state(tmp_path, manifest)
    client = Mock()
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_results"}
    client.file_content.return_value = raw(modify_outputs(manifest))
    for _ in range(2):
        result = complete_saved_batch(client, run, "batch_original", None)
        assert result["status"] == "files_complete_unverified"
    (out / "maths.py").write_text("ruční změna", encoding="utf-8")
    result = complete_saved_batch(client, run, "batch_original", None)
    assert result["status"] == "partial"
    assert (out / "maths.py").read_text(encoding="utf-8") == "ruční změna"
    client.create_response.assert_not_called()
    client.create_batch.assert_not_called()


def test_modify_retry_keeps_source_manifest_and_original_hashes(tmp_path):
    from pathlib import Path
    from change_v2_fixtures import batch_output_rows, run as run_scenario, scenario

    worker, client, _ = scenario(
        tmp_path, "MODIFY", batch=True,
        files=[{"path": "maths.py", "action": "modify"}, {"path": "main.py", "action": "add"}],
    )
    out = Path(worker.cfg.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "maths.py").write_text("původní OUT\n", encoding="utf-8")
    original_hash = hashlib.sha256((out / "maths.py").read_bytes()).hexdigest()
    worker.cfg.auto_repair = "within_approval"
    results, errors = run_scenario(worker, client)
    assert results and not errors
    run = Path(worker.log.paths.run_dir)
    state = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
    manifest = state["generate_batch"]
    primary_batch = state["batch_id"]
    original_manifest = copy.deepcopy(manifest)
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_results"}
    rows = batch_output_rows(manifest)
    for row in rows:
        row["response"]["body"]["id"] = "resp_" + row["custom_id"]
    client.file_content.return_value = raw(rows)
    result = complete_saved_batch(client, run, primary_batch, worker.settings)
    assert result["status"] == "files_complete_unverified"
    client.reset_mock()
    client.upload_file.return_value = {"id": "file_retry"}
    client.create_batch.return_value = {"id": "batch_retry"}
    repeat_saved_batch(client, run, primary_batch, ["maths.py"])
    state = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
    assert state["generate_batch"] == original_manifest
    assert state["batch_id"] == primary_batch
    retry = state["generate_batches"]["batch_retry"]
    assert set(retry["expected"].values()) == {"maths.py"}
    order = next(iter(retry["work_orders"].values()))
    assert order["expected_target_hash"] == original_hash
    context = json.loads(retry["requests"][0]["body"]["input"])
    assert context["repair_current_artifact"]["sha256"] == state["generated_hashes"]["maths.py"]
    assert context["repair_current_artifact"]["content"] == "content:maths.py\n"
    assert retry["snapshot"] == manifest["snapshot"]
    assert "_B3_" in retry["requests"][0]["custom_id"]
    encode_requests(state["generate_batch"])
    encode_requests(retry)
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_results"}
    rows = batch_output_rows(retry)
    for row in rows:
        row["response"]["body"]["id"] = "resp_" + row["custom_id"]
    client.file_content.return_value = raw(rows)
    (out / "maths.py").write_text("po odeslání", encoding="utf-8")
    result = complete_saved_batch(client, run, "batch_retry", worker.settings)
    assert result["status"] == "files_complete_unverified"
    assert result["published"] is False
    staged = next(
        row for row in result["staged_files"] if row["path"] == "maths.py"
    )
    assert staged["expected_target_hash"] == original_hash
    from kajovo.core.orchestration.publish import prepare_publish
    from kajovo.core.orchestration.errors import OrchestrationError

    with pytest.raises(OrchestrationError, match="PUBLISH_CONFLICT"):
        prepare_publish([staged], out, {"maths.py": original_hash}, run_dir=run)
    assert (out / "maths.py").read_text(encoding="utf-8") == "po odeslání"
    client.create_response.assert_not_called()


def test_unknown_retry_recovery_keeps_primary_batch(tmp_path):
    from pathlib import Path
    from change_v2_fixtures import batch_output_rows, run as run_scenario, scenario

    worker, client, _ = scenario(
        tmp_path, "MODIFY", batch=True,
        files=[{"path": "maths.py", "action": "modify"}],
    )
    worker.cfg.auto_repair = "within_approval"
    results, errors = run_scenario(worker, client)
    assert results and not errors
    run = Path(worker.log.paths.run_dir)
    state = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
    manifest = state["generate_batch"]
    primary_batch = state["batch_id"]
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_results"}
    rows = batch_output_rows(manifest)
    for row in rows:
        row["response"]["body"]["id"] = "resp_" + row["custom_id"]
    client.file_content.return_value = raw(rows)
    result = complete_saved_batch(client, run, primary_batch, worker.settings)
    assert result["status"] == "files_complete_unverified"
    client.reset_mock()
    client.upload_file.return_value = {"id": "file_retry"}
    client.create_batch.side_effect = TimeoutError("neurčitý submit")
    with pytest.raises(TimeoutError):
        repeat_saved_batch(client, run, primary_batch, ["maths.py"])
    state_path = run / "run_state.json"
    before = json.loads(state_path.read_text(encoding="utf-8"))
    assert before["submission_unknown"] is True
    with pytest.raises(ContractError, match="Neznámý submit"):
        repeat_saved_batch(client, run, primary_batch, ["maths.py"])
    recover_unknown_submission(run, [{"id": "batch_recovered", "input_file_id": "file_retry", "endpoint": "/v1/responses"}])
    after = json.loads(state_path.read_text(encoding="utf-8"))
    assert after["batch_id"] == primary_batch
    assert after["generate_batch"] == manifest
    assert after["generate_batches"]["batch_recovered"] == before["pending_batch_submission"]["manifest"]
    assert after["submission_unknown"] is False
    assert "pending_batch_submission" not in after
    client.create_batch.assert_called_once()
    client.create_response.assert_not_called()


def test_legacy_a3_manifest_import(tmp_path):
    manifest = build_manifest("r", "p", {}, specification(), "gpt-4.1-nano", 0)
    manifest["version"] = 1
    manifest.pop("mode")
    manifest["snapshot"].pop("requirements")
    manifest["snapshot"].pop("maximum_quality")
    manifest["snapshot_hash"] = digest(manifest["snapshot"])
    for row in manifest["requests"]:
        context = json.loads(row["body"]["input"])
        context["specification"] = manifest["snapshot"]
        row["body"]["input"] = json.dumps(context)
        from kajovo.core.contracts import file_response_format
        row["body"]["text"] = file_response_format("A3_FILE", context["file"]["path"], 0)
    assert import_results(manifest, [raw(outputs(manifest))], str(tmp_path))["status"] == "files_complete_unverified"


def test_submit_rejects_invalid_rows_without_network():
    rows = modify_manifest()["requests"]
    rows[1]["custom_id"] = rows[0]["custom_id"]
    client = Mock()
    with pytest.raises(ValueError, match="jedinečná"):
        submit_verified_batch(client, "file_work", rows)
    client.create_batch.assert_not_called()


def test_modify_dry_run_validates_and_logs_without_out_writes(tmp_path):
    manifest = modify_manifest()
    manifest.update(dry_run=True, versing=True)
    run, out = save_state(tmp_path, manifest)
    before = (out / "maths.py").read_bytes()
    client = Mock()
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_results"}
    client.file_content.return_value = raw(modify_outputs(manifest))
    result = complete_saved_batch(client, run, "batch_original", None)
    assert result["dry_run"] and result["status"] == "dry_run"
    assert result["written"] == [] and result["hashes"] == {}
    assert {file["path"] for file in result["planned_files"]} == {"maths.py", "main.py"}
    assert (out / "maths.py").read_bytes() == before
    assert sorted(file.name for file in out.iterdir()) == ["maths.py"]
    saved = json.loads((run / "run_state.json").read_text(encoding="utf-8"))
    assert saved["batch_imports"]["batch_original"]["planned_files"] == result["planned_files"]


def test_modify_versing_copies_original_once_before_write(tmp_path):
    manifest = modify_manifest()
    manifest["versing"] = True
    _, out = save_state(tmp_path, manifest)
    before = (out / "maths.py").read_bytes()
    result = import_results(manifest, [raw(modify_outputs(manifest))], str(out))
    assert result["snapshot_dir"]
    from pathlib import Path
    snapshot = Path(result["snapshot_dir"])
    assert (snapshot / "maths.py").read_bytes() == before
    assert not (snapshot / "main.py").exists()
    assert (out / "maths.py").read_text(encoding="utf-8") == "content\n"
    again = import_results(manifest, [raw(modify_outputs(manifest))], str(out), result["hashes"])
    assert again["snapshot_dir"] is None
    assert len([file for file in out.iterdir() if file.is_dir()]) == 1


def test_versing_does_not_snapshot_rejected_results(tmp_path):
    manifest = modify_manifest()
    manifest["versing"] = True
    _, out = save_state(tmp_path, manifest)
    result = import_results(manifest, [], str(out))
    assert result["errors"] and result["snapshot_dir"] is None
    assert sorted(file.name for file in out.iterdir()) == ["maths.py"]


def test_enriched_generate_traceability_and_snapshot():
    from delivery_fixtures import plan_payload, implementation_fixture
    from kajovo.core.requirements import requirements_format

    schema = requirements_format("GENERATE")["format"]["schema"]
    requirements = {key: [] if value["type"] == "array" else "test"
                    for key, value in schema["properties"].items()}
    requirements["contract"] = "A0R_REQUIREMENTS"
    requirements["explicit_requirements"] = [{"id": "R1", "description": "Součet"}]
    plan = plan_payload(architecture_items=[
        {"id": "component", "requirement_ids": ["R1"], "responsibility": "Součet"},
    ])
    struct = specification()
    for file in struct["files"]:
        file.update(requirement_ids=["R1"], architecture_item_ids=["component"])
    implementation_fixture(struct, requirements, plan)
    manifest = build_manifest("r", "p", plan, struct, "gpt-4.1-nano", 0,
                              requirements=requirements, maximum_quality=True)
    assert manifest["snapshot"]["requirements"] == requirements
    assert manifest["snapshot"]["structure"] == struct
    assert manifest["snapshot"]["maximum_quality"] is True
    requirements["explicit_requirements"].append({"id": "R2", "description": "Nepokryto"})
    with pytest.raises(ContractError):
        build_manifest("r", "p", plan, struct, "gpt-4.1-nano", 0, requirements=requirements)


def test_modify_preserved_dependency_is_context_not_batch_task():
    manifest = modify_manifest()
    struct = manifest["snapshot"]["structure"]
    struct["touched_files"] = [struct["touched_files"][1]]
    struct["preserved_files"] = [{"path": "maths.py", "provides": ["add"]}]
    prepared = build_manifest("r", "p", {}, struct, "gpt-4.1-nano", 0, mode="MODIFY",
                              originals={"maths.py": "def add(a, b): return a + b"})
    assert list(prepared["expected"].values()) == ["main.py"]
    context = json.loads(prepared["requests"][0]["body"]["input"])
    assert {s["path"]: s["content"] for s in context["file_context"]["working_context"]["relevant_source_excerpts"]} == {"maths.py": "def add(a, b): return a + b"}
    assert prepared["omitted"] == []


def test_versing_preserves_edit_made_during_snapshot(tmp_path):
    manifest = modify_manifest()
    manifest["versing"] = True
    _, out = save_state(tmp_path, manifest)

    def snapshot(_target):
        (out / "maths.py").write_text("souběžná změna", encoding="utf-8")
        return "snapshot"

    with patch("kajovo.core.generate_batch._snapshot_before_import", side_effect=snapshot):
        result = import_results(manifest, [raw(modify_outputs(manifest))], str(out))
    assert result["errors"]
    assert (out / "maths.py").read_text(encoding="utf-8") == "souběžná změna"
