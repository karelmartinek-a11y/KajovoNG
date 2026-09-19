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
    client.retrieve_batch.return_value = {"status": "completed", "output_file_id": "file_output"}
    client.upload_file.return_value = {"id": "file_wave2"}
    client.create_batch.return_value = {"id": "batch_wave2"}
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
    return generate_batch.process_saved_batch(
        client, worker.log.paths.run_dir, batch_id, worker.settings, progress=progress,
    )


def reservation(worker, order):
    repo = OrchestrationRepository(Path(worker.log.paths.run_dir).parent / "orchestration.sqlite3")
    with repo.connect() as db:
        return db.execute(
            "SELECT state,provider_id,work_order_hash FROM reservations WHERE reservation_id=?",
            (order.budget_reservation_id,),
        ).fetchone()


def work_order(manifest):
    raw = next(iter(manifest["work_orders"].values()))
    order = WorkOrder(**{k: v for k, v in raw.items() if k != "order_hash"})
    assert order.order_hash == raw["order_hash"]
    return order


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
    assert reservation(worker, order) == ("submitted", "batch_wave2", order.order_hash)
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
    with pytest.raises(ContractError if failure == "missing_id" else TimeoutError):
        import_batch(worker, client, state["batch_id"], primary)
    saved = saved_state(worker)
    pending = saved["pending_batch_submission"]
    manifest = pending["manifest"]
    assert pending["input_file_id"] == "file_wave2"
    assert saved["submission_endpoint"] == "/v1/responses"
    assert saved["submission_jsonl_sha256"] == hashlib.sha256(generate_batch.encode_requests(manifest)).hexdigest()
    assert saved["generate_batch"] == primary and saved["batch_id"] == state["batch_id"]
    assert "batch_wave2" not in saved.get("generate_batches", {})
    v4 = saved["batch_manifests_v4"][pending["manifest_v4_id"]]
    order = work_order(manifest)
    status, provider, order_hash = reservation(worker, order)
    assert order_hash == order.order_hash
    if failure in {"not_sent", "rejected"}:
        assert v4["state"] == "failed" and saved["submission_unknown"] is False
        assert status == "released" and provider is None
    else:
        assert status == "unknown"
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
    row = {"path": "provider.txt", "staged_path": "staging/provider.txt", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
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
