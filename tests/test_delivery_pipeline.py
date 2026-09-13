"""Offline průchody dodání souborů a obnovy analytických checkpointů."""

from copy import deepcopy
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from delivery_fixtures import delivery_payloads, implementation_fixture
from kajovo.core.requirements import CORE_INSTRUCTIONS, validate_traceability
from kajovo.core.generate_batch import import_results
from test_workflows import make_worker, response


def _scenario(tmp_path, mode, batch, maximum_quality):
    worker = make_worker(tmp_path, mode)
    worker.cfg.send_as_c = batch
    worker.cfg.maximum_quality = maximum_quality
    files = [{"path": "hello.txt", "action": "modify"}] if mode == "MODIFY" else None
    if mode == "MODIFY":
        source = tmp_path / "in"
        source.mkdir(parents=True)
        (source / "hello.txt").write_text("Původní obsah.\n", encoding="utf-8")
        (source / "keep.txt").write_text("Zachovaný obsah.\n", encoding="utf-8")
        worker.cfg.in_dir = str(source)
    requirements, plan, structure = delivery_payloads(mode, files)
    canonical = deepcopy(structure)
    key = "files" if mode == "GENERATE" else "touched_files"
    if maximum_quality:
        canonical[key][0]["behavior"] = "KANONICKÁ OPRAVA MQ: úplný výstup včetně zotavení."
    preparation = [requirements, plan, structure]
    if maximum_quality:
        preparation.append(canonical)
    prefix = "A" if mode == "GENERATE" else "B"
    file = {
        "contract": prefix + "3_FILE", "path": "hello.txt", "content": "Nový úplný obsah.\n",
        "chunking": {"chunk_index": 0, "chunk_count": 1, "has_more": False, "next_chunk_index": None},
    }
    if mode == "MODIFY":
        file["action"] = "modify"
    return worker, preparation, file


def _client(payloads):
    client = Mock()
    client.upload_file.return_value = {"id": "file_input"}
    client.retrieve_file.return_value = {"id": "file_input", "filename": "input.txt", "bytes": 100}
    client.create_batch.return_value = {"id": "batch_work"}
    replies = iter(payloads)
    calls = []

    def create(payload):
        calls.append(deepcopy(payload))
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        return response(len(calls) - 1, value)

    client.create_response.side_effect = create
    return client, calls


def _run(worker, client):
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    return results, errors


def _input(payload):
    value = payload["input"]
    if isinstance(value, str):
        return json.loads(value)
    return json.loads("".join(part["text"] for message in value for part in message["content"]
                             if part["type"] == "input_text"))


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("maximum_quality", [False, True])
def test_delivery_eight_variants_use_canonical_preparation(tmp_path, mode, batch, maximum_quality):
    worker, preparation, file = _scenario(tmp_path, mode, batch, maximum_quality)
    client, calls = _client(preparation + ([] if batch else [file]))
    results, errors = _run(worker, client)
    assert not errors
    assert len(results) == 1
    assert len(calls) == 3 + int(maximum_quality) + int(not batch)
    assert all(CORE_INSTRUCTIONS in call["instructions"] for call in calls)
    assert all("reasoning" not in call for call in calls)
    for index in range(1, len(preparation)):
        assert calls[index]["previous_response_id"] == f"resp_{index - 1}"
        context = _input(calls[index])
        assert context["requirements"] == preparation[0]
        if index >= 2:
            assert context["plan"] == preparation[1]
        if index == 3:
            assert context["structure"] == preparation[2]
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    checkpoint = state["preparation_snapshot"]
    assert checkpoint["structure"] == preparation[-1]
    assert checkpoint["maximum_quality"] is maximum_quality
    prefix = "A" if mode == "GENERATE" else "B"
    assert checkpoint["canonical_stage"] == prefix + ("2Q" if maximum_quality else "2")
    validate_traceability(checkpoint["requirements"], checkpoint["plan"], checkpoint["structure"])
    if batch:
        client.create_batch.assert_called_once()
        rows = [json.loads(line) for line in Path(client.upload_file.call_args.args[0]).read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        final = rows[0]["body"]
        assert set(rows[0]) == {"custom_id", "method", "url", "body"}
        assert "previous_response_id" not in final
        working = _input(final)["file_context"]["working_context"]
        assert working["target_file"] == preparation[-1]["files" if mode == "GENERATE" else "touched_files"][0]
        assert len(working["relevant_requirements"]) == 2
        assert "specification" not in _input(final)
        assert not (tmp_path / "out" / "hello.txt").exists()
    else:
        client.create_batch.assert_not_called()
        final = calls[-1]
        assert "previous_response_id" not in final
        assert (tmp_path / "out" / "hello.txt").read_text(encoding="utf-8") == file["content"]
    assert CORE_INSTRUCTIONS in final["instructions"]
    assert final["text"]["format"]["schema"]["properties"]["contract"]["enum"] == [prefix + "3_FILE"]
    if maximum_quality:
        assert preparation[-1] != preparation[2]
        assert "KANONICKÁ OPRAVA MQ" in json.dumps(final, ensure_ascii=False)
    if mode == "MODIFY":
        assert (tmp_path / "in" / "hello.txt").read_text(encoding="utf-8") == "Původní obsah.\n"
        assert (tmp_path / "in" / "keep.txt").read_text(encoding="utf-8") == "Zachovaný obsah.\n"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("maximum_quality", [False, True])
@pytest.mark.parametrize("completed_stages", [1, 2])
def test_partial_checkpoint_resumes_only_missing_stages(tmp_path, mode, maximum_quality, completed_stages):
    worker, preparation, file = _scenario(tmp_path, mode, False, maximum_quality)
    interruption = RuntimeError("Přerušená pracovní odpověď.")
    interruption.request_sent = False
    client, calls = _client(preparation[:completed_stages] + [interruption])
    results, errors = _run(worker, client)
    assert not results and errors
    assert "Přerušená pracovní odpověď" in errors[0]
    assert len(calls) == completed_stages + 1
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    checkpoint = state["preparation_snapshot"]
    assert checkpoint["response_id"] == f"resp_{completed_stages - 1}"
    assert checkpoint["structure"] is None
    assert not (tmp_path / "out" / "hello.txt").exists()
    resumed = make_worker(tmp_path / "resumed", mode)
    resumed.cfg.in_dir = worker.cfg.in_dir
    resumed.cfg.maximum_quality = maximum_quality
    resumed.cfg.preparation_snapshot = deepcopy(checkpoint)
    next_client, next_calls = _client(preparation[completed_stages:] + [file])
    results, errors = _run(resumed, next_client)
    assert not errors and len(results) == 1
    assert len(next_calls) == len(preparation) - completed_stages + 1
    assert next_calls[0]["previous_response_id"] == checkpoint["response_id"]
    expected = preparation[completed_stages]["contract"]
    assert next_calls[0]["text"]["format"]["schema"]["properties"]["contract"]["enum"] == [expected]
    assert (tmp_path / "resumed" / "out" / "hello.txt").read_text(encoding="utf-8") == file["content"]
    assert checkpoint == state["preparation_snapshot"]


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("maximum_quality", [False, True])
def test_invalid_canonical_structure_blocks_batch_submission(tmp_path, mode, maximum_quality):
    worker, preparation, _file = _scenario(tmp_path, mode, True, maximum_quality)
    invalid = deepcopy(preparation[-1])
    key = "files" if mode == "GENERATE" else "touched_files"
    invalid[key][0]["requirement_ids"] = ["NEZNÁMÝ-POŽADAVEK"]
    before = preparation[:3] if maximum_quality else preparation[:2]
    client, calls = _client(before + [invalid, invalid, invalid])
    results, errors = _run(worker, client)
    assert not results and errors
    assert "neznámý požadavek" in errors[0]
    assert len(calls) == len(before) + 3
    client.create_batch.assert_not_called()
    assert all(call.kwargs.get("purpose") != "batch" for call in client.upload_file.call_args_list)
    assert not (tmp_path / "out" / "hello.txt").exists()
    for call in calls[-2:]:
        context = _input(call)
        assert "neznámý požadavek" in context["validation_errors"]
        assert context["structure"] == invalid
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    prefix = "A" if mode == "GENERATE" else "B"
    assert state["preparation_snapshot"]["canonical_stage"] == prefix + ("2" if maximum_quality else "1")


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
def test_real_batch_payload_and_import_deliver_more_than_500_lines(tmp_path, mode, quality):
    worker, preparation, file = _scenario(tmp_path, mode, True, quality)
    client, calls = _client(preparation)
    results, errors = _run(worker, client)
    assert not errors and results[0]["status"] == "batch_pending"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    manifest = state["generate_batch"]
    row = manifest["requests"][0]
    assert "500 řádků" not in row["body"]["instructions"]
    assert "v jediné úplné části" in row["body"]["instructions"]
    assert len(calls) == 3 + int(quality)
    file["content"] = "".join(f"Řádek {index}\n" for index in range(750))
    raw = json.dumps({"custom_id": row["custom_id"], "response": {
        "status_code": 200, "body": response(10, file)}, "error": None}).encode()
    result = import_results(manifest, [raw], worker.cfg.out_dir)
    assert result["status"] == "files_complete_unverified"
    assert Path(worker.cfg.out_dir, file["path"]).read_text(encoding="utf-8") == file["content"]


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("missing", ["", "missing"])
def test_modify_requires_project_before_any_client_operation(tmp_path, batch, missing):
    worker = make_worker(tmp_path, "MODIFY")
    worker.cfg.send_as_c = batch
    worker.cfg.in_dir = str(tmp_path / missing) if missing else ""
    client = Mock()
    results, errors = _run(worker, client)
    assert not results and "existující vstupní adresář IN" in errors[0]
    assert client.mock_calls == []


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("no_changes", [False, True])
def test_nonexistent_preserved_file_blocks_delivery_and_no_changes(tmp_path, batch, no_changes):
    worker, preparation, _file = _scenario(tmp_path, "MODIFY", batch, False)
    structure = preparation[2]
    changed = structure["touched_files"][0]
    structure["preserved_files"] = [{
        "path": "neexistuje.py", "provides": [], "behavior": "Zachované chování.",
        "requirement_ids": changed["requirement_ids"],
        "architecture_item_ids": changed["architecture_item_ids"],
    }]
    if no_changes:
        structure["touched_files"] = []
        preparation[1]["change_plan"]["files_to_modify"] = []
    implementation_fixture(structure, preparation[0], preparation[1])
    validate_traceability(*preparation)
    client, calls = _client(preparation)
    results, errors = _run(worker, client)
    assert not results and "Zachovaný soubor" in errors[0]
    assert len(calls) == 3
    client.create_batch.assert_not_called()
    assert not Path(worker.cfg.out_dir, "hello.txt").exists()


@pytest.mark.parametrize("batch", [False, True])
def test_modify_binary_deliverable_stays_partial_without_file_generation(tmp_path, batch):
    worker, preparation, _file = _scenario(tmp_path, "MODIFY", batch, False)
    preparation[2]["touched_files"][0]["kind"] = "binary"
    client, calls = _client(preparation)
    results, errors = _run(worker, client)
    assert not errors and results[0]["status"] == "partial"
    assert results[0]["missing_deliverables"] == ["hello.txt"]
    assert len(calls) == 3
    client.create_batch.assert_not_called()


def test_modify_live_dry_run_reports_no_output_and_keeps_evidence(tmp_path):
    worker, preparation, file = _scenario(tmp_path, "MODIFY", False, False)
    worker.settings.dry_run_modify = True
    worker.cfg.versing = True
    client, _ = _client([*preparation, file])
    results, errors = _run(worker, client)
    assert not errors and results[0]["status"] == "dry_run"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "dry_run" and state["written_files"] == []
    records = [json.loads(path.read_text(encoding="utf-8"))
               for path in Path(worker.log.paths.manifests_dir).glob("*modify_dry_run*.json")]
    assert records[0]["files"][0]["content"] == file["content"]
    assert not Path(worker.cfg.out_dir).exists()


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
def test_completed_file_rerun_uses_hash_and_does_not_generate(tmp_path, mode, batch):
    worker, preparation, _ = _scenario(tmp_path, mode, batch, False)
    out = Path(worker.cfg.out_dir)
    out.mkdir()
    (out / "hello.txt").write_bytes(b"done")
    worker.cfg.skip_paths = ["hello.txt"]
    worker.cfg.completed_hashes = {"hello.txt": hashlib.sha256(b"done").hexdigest()}
    client, calls = _client(preparation)
    results, errors = _run(worker, client)
    assert not errors and results[0]["status"] == "completed"
    assert len(calls) == 3
    client.create_batch.assert_not_called()
    assert (out / "hello.txt").read_bytes() == b"done"
    from kajovo.core.runlog import verified_output_evidence
    assert verified_output_evidence(worker.log.paths.run_dir, str(out))[0]["sha256"] == worker.cfg.completed_hashes["hello.txt"]


@pytest.mark.parametrize("fault", ["missing", "changed", "no_hash"])
def test_rerun_rejects_missing_or_changed_output_before_api(tmp_path, fault):
    worker, _, _ = _scenario(tmp_path, "GENERATE", True, False)
    worker.cfg.skip_paths = ["hello.txt"]
    worker.cfg.completed_hashes = {"hello.txt": hashlib.sha256(b"done").hexdigest()}
    if fault != "missing":
        Path(worker.cfg.out_dir).mkdir()
        Path(worker.cfg.out_dir, "hello.txt").write_bytes(b"edit" if fault == "changed" else b"done")
    if fault == "no_hash":
        worker.cfg.completed_hashes = {}
    client = Mock()
    results, errors = _run(worker, client)
    assert not results and "platný důkaz zápisu" in errors[0]
    assert client.mock_calls == []


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("quality", [False, True])
def test_reasoning_and_progress_follow_actual_delivery_stages(tmp_path, mode, batch, quality):
    worker, preparation, file = _scenario(tmp_path, mode, batch, quality)
    worker.cfg.model = "gpt-5.2"
    worker.cfg.available_models = ["gpt-5.2"]
    worker.cfg.model_a1 = worker.cfg.model_a2 = worker.cfg.model_a3 = "gpt-5.2"
    events = []
    worker.progress_event.connect(events.append)
    client, calls = _client(preparation + ([] if batch else [file]))
    results, errors = _run(worker, client)
    assert not errors and results
    prefix = "A" if mode == "GENERATE" else "B"
    stages = [prefix + suffix for suffix in ("0R", "1", "2")]
    if quality:
        stages.append(prefix + "2Q")
    if not batch:
        stages.append(prefix + "3")
    assert [event.stage for event in events if event.state == "waiting"] == stages
    if batch:
        state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
        calls += [row["body"] for row in state["generate_batch"]["requests"]]
    if quality:
        assert all(call["reasoning"]["effort"] == "xhigh" for call in calls)
    else:
        assert all("reasoning" not in call for call in calls[:-1])
        assert calls[-1]["reasoning"]["effort"] == "medium"
    assert all("temperature" not in call for call in calls)


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("size", [150_000, 150_001])
def test_long_prompt_ingestion_preserves_source_without_paid_acknowledgements(tmp_path, mode, batch, size):
    worker, preparation, file = _scenario(tmp_path, mode, batch, False)
    worker.cfg.prompt = ("Obsah zadání bez zkrácení.\n" * 10_000)[:size]
    count = 0
    client, calls = _client(["Přijato"] * count + preparation + ([] if batch else [file]))
    results, errors = _run(worker, client)
    assert not errors and results
    assert len(calls) == count + 3 + int(not batch)
    assert worker.cfg.prompt in _input(calls[0])["source"]
    assert all(worker.cfg.prompt not in call["instructions"] for call in calls)


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("batch", [False, True])
def test_quality_gate_added_file_is_really_delivered(tmp_path, mode, batch):
    worker, preparation, file = _scenario(tmp_path, mode, batch, True)
    key = "files" if mode == "GENERATE" else "touched_files"
    added = deepcopy(preparation[-1][key][0])
    added["path"] = "added.txt"
    if mode == "MODIFY":
        added["action"] = "add"
    preparation[-1][key].append(added)
    implementation_fixture(preparation[-1], preparation[0], preparation[1])
    additional = {**file, "path": "added.txt"}
    if mode == "MODIFY":
        additional["action"] = "add"
    client, _calls = _client(preparation + ([] if batch else [file, additional]))
    results, errors = _run(worker, client)
    assert not errors and results
    if batch:
        state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
        assert set(state["generate_batch"]["expected"].values()) == {"hello.txt", "added.txt"}
    else:
        assert Path(worker.cfg.out_dir, "added.txt").read_text(encoding="utf-8") == additional["content"]


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("quality", [False, True])
def test_live_file_chunks_keep_order_content_and_canonical_context(tmp_path, mode, quality):
    worker, preparation, file = _scenario(tmp_path, mode, False, quality)
    first, last = deepcopy(file), deepcopy(file)
    first.update(content="První část\n" * 500,
                 chunking={"chunk_index": 0, "chunk_count": 2, "has_more": True, "next_chunk_index": 1})
    last.update(content="Dokončení\n" * 250,
                chunking={"chunk_index": 1, "chunk_count": 2, "has_more": False, "next_chunk_index": None})
    client, calls = _client([*preparation, first, last])
    results, errors = _run(worker, client)
    assert not errors and results[0]["status"] == "completed"
    assert Path(worker.cfg.out_dir, file["path"]).read_text(encoding="utf-8") == first["content"] + last["content"]
    assert calls[-1]["previous_response_id"] == f"resp_{len(preparation)}"
    for index, call in enumerate(calls[-2:]):
        assert "500 řádků" in call["instructions"]
        text = "".join(part["text"] for message in call["input"] for part in message["content"]
                       if part["type"] == "input_text")
        context = json.loads(text.split("\n")[1])
        if index == 0:
            assert context["file_context"]["working_context"]["target_file"]["path"] == file["path"]
        else:
            assert "file_context_hash" in context and "file_context" not in context
        assert "specification" not in context


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("changed_after_submit", [False, True])
def test_batch_completion_distinguishes_missing_from_previously_completed(tmp_path, mode, changed_after_submit):
    worker, preparation, file = _scenario(tmp_path, mode, True, False)
    key = "files" if mode == "GENERATE" else "touched_files"
    existing = deepcopy(preparation[-1][key][0])
    existing["path"] = "done.txt"
    preparation[-1][key].append(existing)
    implementation_fixture(preparation[-1], preparation[0], preparation[1])
    out = Path(worker.cfg.out_dir)
    out.mkdir()
    (out / "done.txt").write_bytes(b"done")
    worker.cfg.skip_paths = ["done.txt"]
    worker.cfg.completed_hashes = {"done.txt": hashlib.sha256(b"done").hexdigest()}
    client, _calls = _client(preparation)
    results, errors = _run(worker, client)
    assert not errors and results
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    manifest = state["generate_batch"]
    assert manifest["omitted"] == []
    assert manifest["completed_hashes"] == worker.cfg.completed_hashes
    if changed_after_submit:
        (out / "done.txt").write_bytes(b"user edit")
    row = manifest["requests"][0]
    raw = json.dumps({"custom_id": row["custom_id"], "response": {
        "status_code": 200, "body": response(12, file)}}).encode()
    client.file_content.return_value = raw
    from kajovo.core.generate_batch import process_saved_batch
    result = process_saved_batch(client, worker.log.paths.run_dir, results[0]["batch_id"], worker.settings,
                                 batch={"id": results[0]["batch_id"], "status": "completed", "output_file_id": "file_result"})
    assert result["status"] == ("partial" if changed_after_submit else "files_complete_unverified")
    assert (out / "done.txt").read_bytes() == (b"user edit" if changed_after_submit else b"done")
    if changed_after_submit:
        assert "done.txt" in result["missing"]
