"""Ruční podklady jsou samostatné immutable vazby a nepřepisují plán."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.manual_resources import bind_manual_resource, inherit_resources, manual_resource_bytes
from kajovo.core.recoverable_artifacts import load_run_state
from kajovo.core.runlog import RunLogger


def test_manual_resource_survives_child_without_provider_call(tmp_path):
    graph = {"contract": "IMPLEMENTATION_GRAPH_V3", "mode": "GENERATE", "spine": {
        "resource_deliveries": [{"path": "asset.bin", "producer": "manual_input", "source_or_task_id": "source-1"}],
    }}
    parent = RunLogger(str(tmp_path), "RUN_parent", project_name="Test")
    parent.update_state({"preparation_snapshot": {"version": 2, "graph": graph}, "status": "waiting_manual_resource"})
    source = tmp_path / "supplied.bin"
    source.write_bytes(b"skutecny obsah")
    binding = bind_manual_resource(parent.paths.run_dir, "asset.bin", source)
    source.write_bytes(b"pozdejsi zmena")
    assert load_run_state(parent.paths.run_dir)["preparation_snapshot"]["graph"] == graph
    assert binding["source_or_task_id"] == "source-1"
    child = RunLogger(str(tmp_path), "RUN_child", project_name="Test")
    inherit_resources(parent.paths.run_dir, child)
    assert manual_resource_bytes(SimpleNamespace(log=child), graph, "asset.bin", "source-1") == b"skutecny obsah"
    child_state = load_run_state(child.paths.run_dir)
    identifier = child_state["manual_resource_bindings"]["bindings"][0]["artifact_id"]
    artifact = next(row for row in child.bundle.artifacts() if row["artifact_id"] == identifier)
    Path(child.paths.run_dir, artifact["path_in_bundle"]).write_bytes(b"poskozeno")
    with pytest.raises(ContractError, match="hash"):
        manual_resource_bytes(SimpleNamespace(log=child), graph, "asset.bin", "source-1")


def test_binary_content_provider_is_rejected_before_resource_dispatch():
    from kajovo.core.orchestration.resource_delivery import validate_resource_plan
    graph = {"spine": {"files": [
        {"path": "asset.bin", "kind": "binary_required", "content_dependencies": []},
        {"path": "reader.txt", "kind": "text", "content_dependencies": ["asset.bin"]},
    ], "resource_deliveries": []}}
    with pytest.raises(ContractError, match="textový provider"):
        validate_resource_plan(None, graph)


@pytest.mark.parametrize("batch", [False, True])
def test_skipped_original_is_available_as_exact_consumer_content(tmp_path, batch):
    import json
    from change_v2_fixtures import scenario, run, _file_input_json
    worker, client, responder = scenario(tmp_path, "MODIFY", batch=batch,
        files=[{"path": "provider.txt", "action": "modify"},
               {"path": "consumer.md", "action": "modify", "dependencies": ["provider.txt"],
                "content_dependencies": ["provider.txt"]}],
        content_by_path={"provider.txt": "jedinecny schvaleny provider\n"})
    worker.cfg.skip_exts = [".txt"]
    results, errors = run(worker, client)
    assert not errors and results
    requests = client.create_batch.call_args.kwargs["_prevalidated_rows"] if batch else [
        {"body": payload} for payload in responder.calls if payload["text"]["format"]["name"] == "FILE_CONTENT_V1"]
    assert len(requests) == 1
    context = json.loads(requests[0]["body"]["input"]) if batch else _file_input_json(requests[0]["body"])
    assert context["file_context"]["working_context"]["target_file"]["path"] == "consumer.md"
    assert "jedinecny schvaleny provider" in json.dumps(context, ensure_ascii=False)


@pytest.mark.parametrize("batch", [False, True])
def test_manual_input_completes_real_workflow_without_repeating_text(tmp_path, batch):
    import copy
    from change_v2_fixtures import scenario, run, batch_output_rows, raw_jsonl
    from kajovo.core.runs.executor import RunExecutor
    from kajovo.core.batch_completion import complete_saved_batch
    def manual(name, value, data):
        if name == "A2_SPINE_V2":
            value["result"]["data"]["resource_deliveries"] = [{
                "path": "asset.bin", "producer": "manual_input", "source_or_task_id": "user-asset",
                "criterion_ids": ["AC-1"],
            }]
        return value
    worker, client, _ = scenario(tmp_path, "GENERATE", batch=batch, mutate=manual,
                                  files=[{"path": "hello.txt", "action": "generate"},
                                         {"path": "asset.bin", "action": "generate", "kind": "binary_required"}])
    results, errors = run(worker, client)
    assert not errors
    state = load_run_state(worker.log.paths.run_dir)
    from kajovo.core.recovery import recover_run
    recovered, previous, files = recover_run(worker.settings.log_dir, worker.log.run_id)
    assert recovered["preparation_snapshot"]["version"] == 2 and previous is None
    assert {row["path"] for row in files} == {"hello.txt", "asset.bin"}
    if batch:
        client.retrieve_batch.return_value = {"id": state["batch_id"], "status": "completed",
                                              "input_file_id": "file_batch_input", "endpoint": "/v1/responses",
                                              "output_file_id": "file_output"}
        client.file_content.return_value = raw_jsonl(batch_output_rows(state["generate_batch"]))
        result = complete_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], worker.settings)
        assert result["status"] == "waiting_manual_resource"
    else:
        assert results[0]["status"] == "waiting_manual_resource"
    source = tmp_path / "user.bin"
    source.write_bytes(b"dodany podklad")
    bind_manual_resource(worker.log.paths.run_dir, "asset.bin", source)
    parent_state = Path(worker.log.state_path).read_bytes()
    client.create_response.reset_mock()
    client.create_batch.reset_mock()
    if batch:
        result = complete_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], worker.settings)
        assert result["status"] == "files_complete_unverified"
        target_log = worker.log
    else:
        child = RunLogger(worker.settings.log_dir, "RUN_child_live", "test")
        Path(worker.cfg.out_dir).mkdir(exist_ok=True)
        Path(worker.cfg.out_dir, "asset.bin").write_bytes(b"cizi pozdejsi obsah")
        inherit_resources(worker.log.paths.run_dir, child)
        cfg = copy.deepcopy(worker.cfg)
        cfg.execution_approval_id = ""
        resumed = RunExecutor(cfg, worker.settings, "test", child)
        results, errors = run(resumed, client)
        assert not errors
        assert results[0]["status"] == "files_complete_unverified"
        assert Path(worker.log.state_path).read_bytes() == parent_state
        target_log = child
    client.create_response.assert_not_called()
    client.create_batch.assert_not_called()
    rows = {row["path"]: row for row in load_run_state(target_log.paths.run_dir)["staged_files"]}
    assert set(rows) == {"hello.txt", "asset.bin"}
    assert rows["asset.bin"]["expected_target_hash"] is None
    assert Path(target_log.paths.run_dir, rows["asset.bin"]["staged_path"]).read_bytes() == b"dodany podklad"
