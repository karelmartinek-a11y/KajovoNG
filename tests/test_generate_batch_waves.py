"""Regrese skutečných navazujících dávek, jejich identity, importu a ochrany OUT."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from change_v2_fixtures import batch_output_rows, raw_jsonl, run, scenario
from kajovo.core import generate_batch
from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.publish import prepare_publish
from kajovo.core.orchestration.repository import OrchestrationRepository
from kajovo.core.orchestration.work_order import WorkOrder


@pytest.fixture
def wave_run(tmp_path, request):
    mode, existing = getattr(request, "param", ("GENERATE", False))
    action = "generate" if mode == "GENERATE" else "modify"
    worker, client, _ = scenario(
        tmp_path, mode, batch=True,
        files=[
            {"path": "provider.txt", "action": action},
            {
                "path": "consumer.txt", "action": action,
                "dependencies": ["provider.txt"],
                "content_dependencies": ["provider.txt"],
            },
        ],
    )
    target = Path(worker.cfg.out_dir) / "consumer.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    if existing:
        target.write_bytes(b"puvodni OUT\n")
    initial_hash = hashlib.sha256(target.read_bytes()).hexdigest() if existing else None
    results, errors = run(worker, client)
    assert not errors and results[0]["status"] == "batch_pending"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    client.reset_mock()
    client.retrieve_batch.return_value = {"id": state["batch_id"], "input_file_id": "file_batch_input", "endpoint": "/v1/responses", "status": "completed", "output_file_id": "file_output"}
    client.upload_file.return_value = {"id": "file_wave2"}
    client.create_batch.return_value = {"id": "batch_wave2", "input_file_id": "file_wave2", "endpoint": "/v1/responses"}
    return worker, client, state, initial_hash


def output(manifest):
    rows = batch_output_rows(manifest)
    for row in rows:
        row["response"]["body"]["id"] = "resp_" + row["custom_id"]
    return raw_jsonl(rows)


def saved_state(worker):
    return json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))


def import_batch(worker, client, batch_id, manifest, progress=None):
    client.file_content.return_value = output(manifest)
    current = saved_state(worker)
    record = (current.get("batch_records") or {}).get(batch_id) or {}
    client.retrieve_batch.return_value.update(id=batch_id, input_file_id=record.get("input_file_id") or "file_batch_input", endpoint="/v1/responses")
    return generate_batch.process_saved_batch(
        client, worker.log.paths.run_dir, batch_id, worker.settings, progress=progress,
    )


def provider_operation(worker, order):
    repo = OrchestrationRepository(
        Path(worker.log.paths.run_dir).parent / "orchestration.sqlite3"
    )
    with repo.connect() as db:
        return db.execute(
            "SELECT state,provider_id,work_order_hash "
            "FROM provider_operations WHERE attempt_id=?",
            (order.attempt_id,),
        ).fetchone()


def work_order(manifest):
    raw = next(iter(manifest["work_orders"].values()))
    order = WorkOrder(**{k: v for k, v in raw.items() if k != "order_hash"})
    assert order.order_hash == raw["order_hash"]
    return order


def test_rejected_followup_can_use_new_uploaded_file(wave_run):
    from kajovo.core.openai_transport import OpenAIError
    worker, client, state, _ = wave_run
    primary = state["generate_batch"]
    client.create_batch.side_effect = OpenAIError("Dočasný limit", status_code=429)
    with pytest.raises(OpenAIError):
        import_batch(worker, client, state["batch_id"], primary)
    client.create_batch.side_effect = None
    client.upload_file.return_value = {"id": "file_wave2_retry"}
    client.create_batch.return_value = {
        "id": "batch_wave2_retry", "input_file_id": "file_wave2_retry", "endpoint": "/v1/responses"
    }
    result = import_batch(worker, client, state["batch_id"], primary)
    assert result["next_batch_id"] == "batch_wave2_retry"
    second = saved_state(worker)["generate_batches"]["batch_wave2_retry"]
    assert work_order(second).attempt_no == 1
    assert provider_operation(worker, work_order(second))[0:2] == ("submitted", "batch_wave2_retry")


@pytest.mark.parametrize("wave_run", [
    ("GENERATE", False), ("GENERATE", True), ("MODIFY", True),
], indirect=True)
def test_wave_end_to_end_preserves_identity_hashes_and_idempotent_import(wave_run):
    worker, client, state, initial_hash = wave_run
    primary = copy.deepcopy(state["generate_batch"])
    assert set(primary["expected"].values()) == {"provider.txt"}
    assert primary["deferred_paths"] == ["consumer.txt"]
    assert primary["snapshot"]["expected_target_hashes"]["consumer.txt"] == initial_hash
    target = Path(worker.cfg.out_dir) / "consumer.txt"
    target.write_bytes(b"uzivatelska zmena po prvni wave\n")
    events = []
    result = import_batch(worker, client, state["batch_id"], primary, events.append)
    assert result["next_batch_id"] == "batch_wave2" and result["status"] == "batch_pending"
    saved = saved_state(worker)
    second = saved["generate_batches"]["batch_wave2"]
    assert saved["generate_batch"] == primary and saved["batch_id"] == state["batch_id"]
    assert second["snapshot"] == primary["snapshot"]
    assert second["snapshot_hash"] == primary["snapshot_hash"]
    assert set(second["expected"].values()) == {"consumer.txt"}
    assert set(second["work_orders"]).isdisjoint(primary["work_orders"])
    request = second["requests"][0]
    order = work_order(second)
    assert order.task_id != work_order(primary).task_id
    assert order.attempt_no == 1 and order.expected_target_hash == initial_hash
    assert request["body"]["model"] == primary["requests"][0]["body"]["model"] == order.model
    context = json.loads(request["body"]["input"])
    assert context["file"]["path"] == order.target_path == "consumer.txt"
    assert order.approval_id == state["execution_authorization"]["approval_id"]
    assert order.input_projection_hash == generate_batch.digest(context["file_context"])
    assert order.prompt_hash == generate_batch.digest(request["body"]["instructions"] + "\n" + request["body"]["input"])
    verified = second["verified_dependency_artifacts"]["provider.txt"]
    assert verified["content"] == "content:provider.txt\n"
    assert verified["output_hash"] == hashlib.sha256(verified["content"].encode()).hexdigest()
    upload = Path(client.upload_file.call_args.args[0])
    assert [json.loads(line) for line in upload.read_text(encoding="utf-8").splitlines()] == second["requests"]
    assert saved["submission_jsonl_sha256"] == hashlib.sha256(upload.read_bytes()).hexdigest()
    assert saved["submission_unknown"] is False and "pending_batch_submission" not in saved
    v4 = next(value for value in saved["batch_manifests_v4"].values() if value["provider_batch_id"] == "batch_wave2")
    assert v4["state"] == "submitted" and v4["wave_no"] == 1
    assert v4["rows"][0]["work_order_hash"] == order.order_hash
    assert v4["rows"][0]["body_hash"] == generate_batch.digest(request["body"])
    assert provider_operation(worker, order) == ("submitted", "batch_wave2", order.order_hash)
    assert any("dependency-wave" in event.detail for event in events) or len(events) >= 2
    assert import_batch(worker, client, state["batch_id"], primary)["status"] == "batch_pending"
    client.create_batch.assert_called_once()
    completed = import_batch(worker, client, "batch_wave2", second)
    assert completed["status"] == "files_complete_unverified" and completed["published"] is False
    assert completed["staged_files"][0]["expected_target_hash"] == initial_hash
    with pytest.raises(OrchestrationError, match="PUBLISH_CONFLICT"):
        prepare_publish(completed["staged_files"], worker.cfg.out_dir, {"consumer.txt": initial_hash}, run_dir=worker.log.paths.run_dir)
    assert target.read_bytes() == b"uzivatelska zmena po prvni wave\n"
    assert import_batch(worker, client, state["batch_id"], primary)["status"] == "files_complete_unverified"
    assert import_batch(worker, client, "batch_wave2", second)["status"] == "files_complete_unverified"
    final = saved_state(worker)
    assert final["generate_batch"] == primary and final["batch_id"] == state["batch_id"]
    assert set(final["generate_batches"]) == {"batch_wave2"}
    assert len(final["staged_files"]) == 2
    assert set(final["generated_hashes"]) == {"provider.txt", "consumer.txt"}
    for staged in final["staged_files"]:
        content = (Path(worker.log.paths.run_dir) / staged["staged_path"]).read_bytes()
        assert content == f"content:{staged['path']}\n".encode()
        assert hashlib.sha256(content).hexdigest() == staged["sha256"]
    client.create_batch.assert_called_once()
    client.upload_file.assert_called_once()
    client.create_response.assert_not_called()


@pytest.mark.parametrize("failure", ["timeout", "missing_id", "not_sent", "rejected"])
def test_wave_submit_failure_retains_recovery_evidence(wave_run, failure):
    worker, client, state, _ = wave_run
    primary = copy.deepcopy(state["generate_batch"])
    error = TimeoutError("Neurčitý výsledek odeslání")
    if failure == "not_sent":
        error.request_sent = False
    if failure == "rejected":
        error.status_code = 422
    if failure == "missing_id":
        client.create_batch.return_value = {}
    else:
        client.create_batch.side_effect = error
    from kajovo.core.openai_transport import SubmissionOutcomeUnknown
    with pytest.raises(SubmissionOutcomeUnknown if failure == "missing_id" else TimeoutError):
        import_batch(worker, client, state["batch_id"], primary)
    saved = saved_state(worker)
    pending = (next(iter(saved["rejected_batch_submissions"].values())) if failure in {"not_sent", "rejected"}
               else saved["pending_batch_submission"])
    manifest = pending["manifest"]
    assert pending["input_file_id"] == "file_wave2"
    assert saved["submission_endpoint"] == "/v1/responses"
    assert saved["submission_jsonl_sha256"] == hashlib.sha256(generate_batch.encode_requests(manifest)).hexdigest()
    assert saved["generate_batch"] == primary and saved["batch_id"] == state["batch_id"]
    assert "batch_wave2" not in saved.get("generate_batches", {})
    v4 = saved["batch_manifests_v4"][pending["manifest_v4_id"]]
    order = work_order(manifest)
    status, provider, order_hash = provider_operation(worker, order)
    assert order_hash == order.order_hash
    if failure in {"not_sent", "rejected"}:
        assert "pending_batch_submission" not in saved
        assert v4["state"] == "failed" and saved["submission_unknown"] is False
        assert status == "not_submitted" and provider is None
    else:
        assert status == "submission_unknown"
        assert v4["state"] == saved["status"] == "submission_unknown"
        assert saved["submission_unknown"] is True and provider is None
        with pytest.raises(ContractError, match="Neznámý submit"):
            import_batch(worker, client, state["batch_id"], primary)
    client.create_batch.assert_called_once()
    client.upload_file.assert_called_once()
    client.create_response.assert_not_called()


def test_wave_upload_without_file_id_never_submits(wave_run):
    worker, client, state, _ = wave_run
    client.upload_file.return_value = {}
    with pytest.raises(ContractError, match="nemá Files ID"):
        import_batch(worker, client, state["batch_id"], state["generate_batch"])
    saved = saved_state(worker)
    assert saved["generate_batch"] == state["generate_batch"] and saved["batch_id"] == state["batch_id"]
    assert "pending_batch_submission" not in saved
    client.create_batch.assert_not_called()


def test_missing_frozen_hash_map_blocks_followup_without_network(wave_run):
    from dataclasses import replace
    from kajovo.core.orchestration.work_order import response_payload_hash

    worker, client, state, _ = wave_run
    source = copy.deepcopy(state["generate_batch"])
    source["snapshot"].pop("expected_target_hashes")
    source["snapshot_hash"] = generate_batch.digest(source["snapshot"])
    request = source["requests"][0]
    context = json.loads(request["body"]["input"])
    context["file_context"] = generate_batch.ContextCompiler(source["snapshot"]).compile("provider.txt")
    request["body"]["input"] = generate_batch.canonical(context)
    order = replace(
        work_order(source),
        input_projection_hash=generate_batch.digest(context["file_context"]),
        prompt_hash=generate_batch.digest(request["body"]["instructions"] + "\n" + request["body"]["input"]),
        request_payload_hash=response_payload_hash(request["body"]),
    )
    source["work_orders"][request["custom_id"]] = {**order.to_dict(), "order_hash": order.order_hash}
    legacy_target = Path(worker.log.paths.run_dir) / "legacy_import"
    result = generate_batch.import_results(source, [output(source)], str(legacy_target))
    assert result["status"] == "files_complete_unverified"
    assert (legacy_target / "provider.txt").read_bytes() == b"content:provider.txt\n"
    before = Path(worker.log.state_path).read_bytes()
    with pytest.raises(ContractError, match="zmrazené původní hashe"):
        generate_batch._submit_v3_followup_wave(client, worker.log.paths.run_dir, state, source, {})
    assert Path(worker.log.state_path).read_bytes() == before
    assert client.mock_calls == []


def test_no_deferred_wave_has_no_side_effects(wave_run):
    worker, client, state, _ = wave_run
    source = copy.deepcopy(state["generate_batch"])
    source["deferred_paths"] = []
    before = Path(worker.log.state_path).read_bytes()
    assert generate_batch._submit_v3_followup_wave(client, worker.log.paths.run_dir, state, source, {}) is None
    assert Path(worker.log.state_path).read_bytes() == before
    assert client.mock_calls == []


def test_dependency_evidence_rejects_changed_or_escaping_content(wave_run, tmp_path):
    worker, _, state, _ = wave_run
    root = Path(worker.log.paths.run_dir)
    path = root / "staging" / "provider.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"overeny obsah\n")
    row = {"path": "provider.txt", "staged_path": "staging/provider.txt", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "batch_id": state["batch_id"]}
    manifest = state["generate_batch"]
    verified = generate_batch._v3_verified_artifacts(root, manifest, [row])
    assert verified["provider.txt"]["content"] == "overeny obsah\n"
    assert verified["provider.txt"]["output_hash"] == row["sha256"]
    path.write_bytes(b"rucni zmena")
    with pytest.raises(ContractError, match="hash mismatch"):
        generate_batch._v3_verified_artifacts(root, manifest, [row])
    outside = tmp_path / "outside.txt"
    outside.write_text("cizi obsah", encoding="utf-8")
    with pytest.raises(ContractError, match="escapes Run Bundle"):
        generate_batch._v3_verified_artifacts(root, manifest, [{**row, "staged_path": str(outside)}])
    path.write_bytes(b"\xff")
    invalid_utf8 = {**row, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    assert generate_batch._v3_verified_artifacts(root, manifest, [
        None, {}, {**row, "path": "foreign.txt"},
        {**row, "staged_path": "missing.txt"}, invalid_utf8,
    ]) == {}


def test_target_baseline_is_protected_by_snapshot_hash(wave_run):
    _, client, state, _ = wave_run
    manifest = copy.deepcopy(state["generate_batch"])
    manifest["snapshot"]["expected_target_hashes"]["consumer.txt"] = "f" * 64
    with pytest.raises(ContractError, match="Specifikace dávky byla změněna"):
        generate_batch.encode_requests(manifest)
    assert client.mock_calls == []


def test_consumer_without_verified_dependency_cannot_be_built(wave_run):
    worker, client, state, _ = wave_run
    snapshot = state["generate_batch"]["snapshot"]
    with pytest.raises(ContractError, match="verified content evidence"):
        generate_batch.build_manifest(
            worker.log.run_id, snapshot["prompt"], snapshot["plan"], snapshot["structure"],
            state["generate_batch"]["requests"][0]["body"]["model"], None,
            ["consumer.txt"], requirements=snapshot["requirements"],
            expected_target_hashes=snapshot["expected_target_hashes"],
        )
    assert client.mock_calls == []


def test_terminal_error_without_usage_allows_manual_retry(wave_run):
    worker, client, state, _ = wave_run
    manifest = state["generate_batch"]
    custom_id = next(iter(manifest["expected"]))
    client.file_content.return_value = raw_jsonl([{"custom_id": custom_id, "response": None, "error": {"code": "failed", "message": "Provider selhal"}}])
    generate_batch.process_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], worker.settings)
    assert provider_operation(worker, work_order(manifest))[0] == "completed"
    client.create_batch.return_value = {"id": "batch_manual_error_retry", "input_file_id": "file_wave2", "endpoint": "/v1/responses"}
    generate_batch.repeat_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], ["provider.txt"])
    retry = saved_state(worker)["generate_batches"]["batch_manual_error_retry"]
    assert work_order(retry).attempt_kind == "manual"


def test_manual_retry_keeps_selected_dependency_waves_and_old_import_cannot_revert(wave_run):
    worker, client, state, _ = wave_run
    primary = state["generate_batch"]
    import_batch(worker, client, state["batch_id"], primary)
    second = saved_state(worker)["generate_batches"]["batch_wave2"]
    import_batch(worker, client, "batch_wave2", second)
    client.create_batch.return_value = {"id": "batch_retry_provider", "input_file_id": "file_wave2", "endpoint": "/v1/responses"}
    generate_batch.repeat_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], ["provider.txt", "consumer.txt"])
    retry = saved_state(worker)["generate_batches"]["batch_retry_provider"]
    assert set(retry["expected"].values()) == {"provider.txt"}
    assert retry["deferred_paths"] == ["consumer.txt"]
    rows = batch_output_rows(retry)
    rows[0]["response"]["body"]["id"] = "resp_retry_provider"
    rows[0]["response"]["body"]["output_text"] = json.dumps({"content": "novy provider\n"})
    client.file_content.return_value = raw_jsonl(rows)
    client.retrieve_batch.return_value.update(id="batch_retry_provider", input_file_id="file_wave2")
    client.create_batch.return_value = {"id": "batch_retry_consumer", "input_file_id": "file_wave2", "endpoint": "/v1/responses"}
    generate_batch.process_saved_batch(client, worker.log.paths.run_dir, "batch_retry_provider", worker.settings)
    final_wave = saved_state(worker)["generate_batches"]["batch_retry_consumer"]
    assert set(final_wave["expected"].values()) == {"consumer.txt"}
    assert work_order(final_wave).attempt_kind == "manual"
    assert final_wave["verified_dependency_artifacts"]["provider.txt"]["content"] == "novy provider\n"
    import_batch(worker, client, "batch_retry_consumer", final_wave)
    current = saved_state(worker)["staged_files"]
    client.create_batch.reset_mock()
    import_batch(worker, client, state["batch_id"], primary)
    assert saved_state(worker)["staged_files"] == current
    client.create_batch.assert_not_called()
    client.create_batch.return_value = {"id": "batch_failed_retry", "input_file_id": "file_wave2", "endpoint": "/v1/responses"}
    generate_batch.repeat_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], ["provider.txt"])
    failed = saved_state(worker)["generate_batches"]["batch_failed_retry"]
    custom_id = next(iter(failed["expected"]))
    client.retrieve_batch.return_value.update(id="batch_failed_retry", input_file_id="file_wave2")
    client.file_content.return_value = raw_jsonl([{"custom_id": custom_id, "response": None,
                                                 "error": {"code": "failed", "message": "Chyba poskytovatele"}}])
    generate_batch.process_saved_batch(client, worker.log.paths.run_dir, "batch_failed_retry", worker.settings)
    result = import_batch(worker, client, state["batch_id"], primary)
    assert result["status"] == "partial"
    assert "provider.txt" not in {row["path"] for row in saved_state(worker)["staged_files"]}


def test_partial_import_can_finish_after_local_write_failure(wave_run):
    worker, client, state, _ = wave_run
    primary = state["generate_batch"]
    collision = Path(worker.log.paths.run_dir) / "staging" / "batch" / state["batch_id"] / "generated" / "provider.txt"
    collision.mkdir(parents=True)
    result = import_batch(worker, client, state["batch_id"], primary)
    assert result["status"] == "partial"
    assert saved_state(worker)["batch_manifest_v4"]["state"] == "partial"
    client.create_batch.assert_not_called()
    collision.rmdir()
    result = import_batch(worker, client, state["batch_id"], primary)
    assert result["status"] == "batch_pending"
    assert saved_state(worker)["batch_manifest_v4"]["state"] == "imported"
    client.create_batch.assert_called_once()


@pytest.mark.parametrize("wave_run", [("MODIFY", True)], indirect=True)
@pytest.mark.parametrize("fault", ["changed", "missing"])
def test_followup_rejects_changed_or_missing_original_archive(wave_run, fault):
    worker, client, state, _ = wave_run
    root = Path(worker.log.paths.run_dir)
    artifact = next(row for row in worker.log.bundle.artifacts() if row["role"] == "in_project_file")
    archived = root / artifact["path_in_bundle"]
    if fault == "changed":
        archived.write_bytes(b"zmeneny original\n")
    else:
        archived.unlink()
    with pytest.raises(ContractError, match="snapshot|SourcePack"):
        generate_batch._submit_v3_followup_wave(client, root, state, state["generate_batch"], {})
    client.upload_file.assert_not_called()
    client.create_batch.assert_not_called()


def test_old_consumer_manifest_cannot_restore_failed_producer(wave_run):
    worker, client, state, _ = wave_run
    primary = state["generate_batch"]
    import_batch(worker, client, state["batch_id"], primary)
    consumer = saved_state(worker)["generate_batches"]["batch_wave2"]
    import_batch(worker, client, "batch_wave2", consumer)
    client.create_batch.return_value = {"id": "batch_bad", "input_file_id": "file_wave2", "endpoint": "/v1/responses"}
    generate_batch.repeat_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], ["provider.txt"])
    retry = saved_state(worker)["generate_batches"]["batch_bad"]
    client.retrieve_batch.return_value.update(id="batch_bad", input_file_id="file_wave2")
    client.file_content.return_value = raw_jsonl([{"custom_id": next(iter(retry["expected"])), "error": {"code": "failed"}}])
    generate_batch.process_saved_batch(client, worker.log.paths.run_dir, "batch_bad", worker.settings)
    client.create_batch.reset_mock()
    result = import_batch(worker, client, "batch_wave2", consumer)
    assert result["status"] == "partial"
    assert "provider.txt" not in generate_batch._v3_verified_artifacts(
        worker.log.paths.run_dir, consumer, saved_state(worker)["staged_files"],
    )
    client.create_batch.assert_not_called()


@pytest.mark.parametrize("usage", [[], {"input_tokens": None}, {"output_tokens": -1},
                                  {"input_tokens": True}, {"output_tokens_details": []}])
def test_malformed_usage_preserves_valid_content_and_records_diagnostic(tmp_path, usage):
    worker, client, _ = scenario(tmp_path, "GENERATE", batch=True,
                                 files=[{"path": "good.txt", "action": "generate"},
                                        {"path": "bad.txt", "action": "generate"}])
    results, errors = run(worker, client)
    assert results and not errors
    state = saved_state(worker)
    manifest = state["generate_batch"]
    rows = batch_output_rows(manifest)
    for row in rows:
        row["response"]["body"]["id"] = "resp_" + row["custom_id"]
        row["response"]["body"]["usage"] = usage if manifest["expected"][row["custom_id"]] == "bad.txt" else {
            "input_tokens": 3, "output_tokens": 4, "output_tokens_details": {"reasoning_tokens": 2},
        }
    client.retrieve_batch.return_value = {"id": state["batch_id"], "status": "completed",
                                         "input_file_id": "file_batch_input", "endpoint": "/v1/responses", "output_file_id": "output"}
    client.file_content.return_value = raw_jsonl(rows)
    events = []
    result = generate_batch.process_saved_batch(client, worker.log.paths.run_dir, state["batch_id"], worker.settings,
                                                progress=events.append)
    assert result["status"] == "files_complete_unverified"
    assert [row["path"] for row in saved_state(worker)["staged_files"]] == ["bad.txt", "good.txt"]
    assert result["usage"] == {"input_tokens": 3, "output_tokens": 4, "reasoning_tokens": 2,
                               "complete": False, "missing_count": 1,
                               "invalid_count": 0 if usage == {"input_tokens": None} else 1}
    assert any("neúplná evidence" in event.detail for event in events)
    assert result["errors"] == {}
    bad_id = next(cid for cid, path in manifest["expected"].items() if path == "bad.txt")
    assert set(result["usage_diagnostics"]) == {bad_id}
    assert "usage" in result["usage_diagnostics"][bad_id]
    saved = saved_state(worker)
    assert saved["batch_imports"][state["batch_id"]]["usage_diagnostics"] == result["usage_diagnostics"]
    from kajovo.core.orchestration.publish import publish_staged_run
    report = publish_staged_run(worker.log.paths.run_dir)
    assert report["status"] == "committed"
    for name in ("bad.txt", "good.txt"):
        assert (Path(worker.cfg.out_dir) / name).read_bytes() == f"content:{name}\n".encode()


@pytest.mark.parametrize("usage", [None, {}, {"input_tokens": None}, {"output_tokens_details": None}])
def test_missing_usage_is_unknown_not_zero(usage):
    assert generate_batch._validated_usage({"usage": usage}) == {
        "input_tokens": None, "output_tokens": None, "reasoning_tokens": None,
    }
    assert generate_batch._validated_usage({}) == {
        "input_tokens": None, "output_tokens": None, "reasoning_tokens": None,
    }


def test_explicit_zero_usage_remains_known():
    assert generate_batch._validated_usage({"usage": {
        "input_tokens": 0, "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0},
    }}) == {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}


@pytest.mark.parametrize("usage", [None, {}, []])
def test_batch_without_known_usage_stages_content_and_reports_unknown(wave_run, usage):
    worker, client, state, _ = wave_run
    rows = batch_output_rows(state["generate_batch"])
    for row in rows:
        if usage is not None:
            row["response"]["body"]["usage"] = usage
        else:
            row["response"]["body"].pop("usage", None)
    client.file_content.return_value = raw_jsonl(rows)
    events = []
    result = generate_batch.process_saved_batch(
        client, worker.log.paths.run_dir, state["batch_id"], worker.settings, progress=events.append,
    )
    assert result["errors"] == {}
    assert result["usage"] == {
        "input_tokens": None, "output_tokens": None, "reasoning_tokens": None,
        "complete": False, "missing_count": 1, "invalid_count": int(isinstance(usage, list)),
    }
    assert [row["path"] for row in saved_state(worker)["staged_files"]] == ["provider.txt"]
    detail = next(event.detail for event in events if event.stage == "Spotřeba BATCH")
    assert "neznámé" in detail and "neúplná evidence" in detail
