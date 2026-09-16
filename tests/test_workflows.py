import json
from dataclasses import fields
from unittest.mock import Mock, patch

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.pipeline import UiRunConfig, RunWorker
from kajovo.core.runlog import RunLogger
from delivery_fixtures import delivery_payloads, plan_payload, requirements_payload, structure_payload


def make_worker(tmp_path, mode):
    values = {}
    for field in fields(UiRunConfig):
        annotation = str(field.type)
        values[field.name] = (False if annotation == "bool" else [] if annotation.startswith("List")
                              else {} if annotation.startswith("Dict") else 0.0 if annotation == "float" else "")
    values.update(project="test", prompt="Write a file", mode=mode, model="gpt-4o-mini",
                  model_a1="gpt-4o-mini", model_a2="gpt-4o-mini", model_a3="gpt-4o-mini",
                  out_dir=str(tmp_path / "out"), resume_files=None, resume_prev_id=None,
                  preparation_snapshot=None,
                  available_models=["gpt-4o-mini"])
    cfg = UiRunConfig(**values)
    cfg.model_caps = {"ok_basic": True, "supports_temperature": True, "supports_previous_response_id": True}
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    logger = RunLogger(settings.log_dir, "RUN_090920261200_TEST", "test")
    return RunWorker(cfg, settings, "test", logger)


def response(index, payload):
    # Úplné vzorky API pro testy orchestrace; záměrné vady souborů zůstávají zachované.
    if isinstance(payload, str):
        payload = {"text": payload}
    elif payload.get("contract") in ("A0R_REQUIREMENTS", "B0R_REQUIREMENTS"):
        mode = "GENERATE" if payload["contract"].startswith("A") else "MODIFY"
        payload = requirements_payload(mode, **payload)
    elif payload.get("contract") == "A1_PLAN":
        payload = plan_payload("GENERATE", **payload)
    elif payload.get("contract") == "B1_PLAN":
        payload = plan_payload("MODIFY", **payload)
    elif payload.get("contract") == "A2_STRUCTURE" and "version" not in payload:
        payload = structure_payload("GENERATE", **payload)
    elif payload.get("contract") == "B2_STRUCTURE":
        payload = structure_payload("MODIFY", files=payload["touched_files"],
                                    **{key: value for key, value in payload.items() if key != "touched_files"})
    return {"id": f"resp_{index}", "object": "response", "model": "gpt-4o-mini", "status": "completed",
            "output_text": json.dumps(payload), "usage": {"input_tokens": 10, "output_tokens": 20}}


@pytest.mark.parametrize("mode", ["QA", "QFILE", "GENERATE", "MODIFY"])
@pytest.mark.parametrize("maximum_quality", [False, True])
def test_complete_offline_workflow(tmp_path, mode, maximum_quality):
    worker = make_worker(tmp_path, mode)
    worker.cfg.maximum_quality = maximum_quality
    file = {"contract": "A3_FILE", "path": "hello.txt", "content": "hello\n",
            "chunking": {"chunk_index": 0, "chunk_count": 1, "has_more": False, "next_chunk_index": None}}
    payloads = ["answer"] if mode == "QA" else [file]
    if mode == "GENERATE":
        payloads = [*delivery_payloads(mode), file]
    if mode == "MODIFY":
        in_dir = tmp_path / "in"
        in_dir.mkdir()
        worker.cfg.in_dir = str(in_dir)
        file["contract"] = "B3_FILE"
        file["action"] = "add"
        payloads = [*delivery_payloads(mode), file]
    if maximum_quality and mode in ("GENERATE", "MODIFY"):
        canonical = structure_payload(mode)
        key = "files" if mode == "GENERATE" else "touched_files"
        canonical[key][0]["behavior"] = "Úplný obsah po nezávislé kontrole návrhu."
        payloads.insert(-1, canonical)
    client = Mock()
    from kajovo.core.context_compiler import content_hash
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000, "request_hash": content_hash(payload)}
    client.upload_file.return_value = {"id": "file_test"}
    client.retrieve_file.return_value = {"id": "file_test", "filename": "input.txt", "bytes": 100}
    client.create_response.side_effect = [response(index, item) for index, item in enumerate(payloads)]
    errors, results = [], []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert len(results) == 1
    assert client.create_response.call_count == len(payloads)
    if mode in ("GENERATE", "MODIFY"):
        from kajovo.core.requirements import CORE_INSTRUCTIONS

        calls = [call.args[0] for call in client.create_response.call_args_list]
        assert all(CORE_INSTRUCTIONS in call["instructions"] for call in calls)
        assert all("reasoning" not in call for call in calls)
        assert all("previous_response_id" not in call for call in calls)
        state = json.loads((tmp_path / "LOG" / worker.log.run_id / "run_state.json").read_text(encoding="utf-8"))
        snapshot = state["preparation_snapshot"]
        assert snapshot["requirements"] == payloads[0]
        assert snapshot["plan"] == payloads[1]
        assert snapshot["structure"] == payloads[-2]
        prefix = "A" if mode == "GENERATE" else "B"
        assert snapshot["canonical_stage"] == prefix + ("2Q" if maximum_quality else "2")
        assert snapshot["maximum_quality"] is maximum_quality
    if mode != "QA":
        assert (tmp_path / "out" / "hello.txt").read_text(encoding="utf-8") == "hello\n"
    assert json.loads(
        (tmp_path / "LOG" / worker.log.run_id / "run_state.json").read_text(encoding="utf-8")
    )["status"] == ("files_complete_unverified" if mode in ("GENERATE", "MODIFY") else "completed")
    if mode in ("GENERATE", "MODIFY"):
        assert worker.log.bundle.verify_integrity()["valid"]
        step = worker.log.bundle.steps()[-1]
        assert step["status"] == "completed"
        assert step["validation_required"] is True


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_delivery_fixtures_have_complete_traceability_and_independent_values(mode):
    from copy import deepcopy
    from kajovo.core.requirements import validate_traceability

    requirements, plan, structure = delivery_payloads(mode)
    before = deepcopy((requirements, plan, structure))
    validate_traceability(requirements, plan, structure)
    assert (requirements, plan, structure) == before
    key = "files" if mode == "GENERATE" else "touched_files"
    structure[key][0]["requirement_ids"].clear()
    assert delivery_payloads(mode) == before
    assert plan["architecture_items"][0]["requirement_ids"] == ["REQ-1", "REQ-2"]


def test_log_disk_error_does_not_repeat_api(tmp_path):
    worker = make_worker(tmp_path, "QA")
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    with patch.object(worker.log, "save_json", side_effect=OSError("disk failure")):
        with pytest.raises(OSError):
            worker._create_response(client, {"model": "gpt-4o-mini", "input": "test"})
    assert client.create_response.call_count == 1


def test_incomplete_response_stops_workflow(tmp_path):
    worker = make_worker(tmp_path, "QA")
    client = Mock()
    client.create_response.return_value = {**response(1, "partial"), "status": "incomplete"}
    from kajovo.core.contracts import RemoteResponseError
    with pytest.raises(RemoteResponseError) as caught:
        worker._create_response(client, {"model": "gpt-4o-mini", "input": "test"})
    assert caught.value.status == "incomplete"
    assert caught.value.response_id == "resp_1"
    assert client.create_response.call_count == 1


def test_premature_file_termination_preserves_output(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    out = tmp_path / "out"
    out.mkdir()
    target = out / "hello.txt"
    target.write_text("original", encoding="utf-8")
    payloads = [*delivery_payloads(),
                {"contract": "A3_FILE", "path": "hello.txt", "content": "partial",
                 "chunking": {"chunk_index": 0, "chunk_count": 3, "has_more": False, "next_chunk_index": None}}]
    client = Mock()
    client.create_response.side_effect = [response(i, item) for i, item in enumerate(payloads)]
    errors, results = [], []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert errors and not results
    assert "chunk" in errors[0].lower() or "část" in errors[0].lower()
    assert client.create_response.call_count == len(payloads)
    assert target.read_text(encoding="utf-8") == "original"


def test_worker_keeps_independent_settings_snapshot(tmp_path):
    worker = make_worker(tmp_path, "QA")
    settings = AppSettings()
    other = RunWorker(worker.cfg, settings, "test", worker.log)
    settings.security.allow_upload_sensitive = True
    settings.retry.max_attempts = 1
    assert not other.settings.security.allow_upload_sensitive
    assert other.settings.retry.max_attempts == 6


@pytest.mark.parametrize("invalid", [{}, {"content": None}, {"content": 7}])
def test_output_manifest_requires_text_before_any_write(tmp_path, invalid):
    from kajovo.core.contracts import ContractError

    worker = make_worker(tmp_path, "GENERATE")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    target = out_dir / "keep.txt"
    target.write_text("zachovat", encoding="utf-8")
    with pytest.raises(ContractError):
        worker._save_out_files([
            {"path": "new.txt", "content": "nový soubor"},
            {"path": "keep.txt", **invalid},
        ])
    assert target.read_text(encoding="utf-8") == "zachovat"
    assert not (out_dir / "new.txt").exists()


def test_output_manifest_accepts_explicit_empty_text(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker._save_out_files([{"path": "empty.txt", "content": ""}])
    assert (tmp_path / "out" / "empty.txt").read_bytes() == b""


def test_worker_keeps_independent_run_configuration(tmp_path):
    worker = make_worker(tmp_path, "QA")
    other = RunWorker(worker.cfg, AppSettings(), "test", worker.log)
    worker.cfg.out_dir = "jiný-výstup"
    worker.cfg.model_caps["supports_previous_response_id"] = False
    worker.cfg.attached_file_ids.append("file_changed")
    assert other.cfg.out_dir == str(tmp_path / "out")
    assert other.cfg.model_caps["supports_previous_response_id"]
    assert not other.cfg.attached_file_ids


def test_custom_cascade_records_each_step(tmp_path):
    from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
    from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-4o-mini", input_text="test")])
    cfg = CascadeRunConfig("project", definition, "", str(tmp_path / "out"), run_id="RUN_090920261200_TEST")
    worker = CascadeRunWorker(cfg, settings, "test")
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert results[0]["run_id"] == cfg.run_id
    assert list((tmp_path / "LOG" / cfg.run_id / "responses").glob("*.json"))


def test_batch_uses_only_supported_jsonl_fields(tmp_path):
    worker = make_worker(tmp_path, "MODIFY")
    worker.cfg.send_as_c = True
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    worker.cfg.in_dir = str(in_dir)
    client = Mock()
    from kajovo.core.context_compiler import content_hash
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000, "request_hash": content_hash(payload)}
    client.create_response.side_effect = [response(i, value) for i, value in enumerate(delivery_payloads("MODIFY"))]
    client.upload_file.return_value = {"id": "file_batch"}
    client.retrieve_file.return_value = {"id": "file_batch", "filename": "input.txt", "bytes": 100}
    client.create_batch.return_value = {"id": "batch_test"}
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors
    assert results[0]["batch_id"] == "batch_test"
    path = client.upload_file.call_args.args[0]
    with open(path, encoding="utf-8") as handle:
        request = json.loads(handle.readline())
    assert set(request) == {"custom_id", "method", "url", "body"}
    assert client.create_response.call_count == 3
    assert request["body"]["text"]["format"]["schema"]["properties"]["contract"]["enum"] == ["B3_FILE"]
