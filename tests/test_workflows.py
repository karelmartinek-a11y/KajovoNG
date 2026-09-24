import json
from dataclasses import MISSING, fields
from typing import get_origin, get_type_hints
from unittest.mock import Mock, patch

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.runs.config import UiRunConfig
from kajovo.core.runs.executor import RunExecutor as RunWorker
from kajovo.core.runlog import RunLogger
from delivery_fixtures import delivery_payloads, plan_payload, requirements_payload, structure_payload


def make_worker(tmp_path, mode):
    values = {}
    resolved_types = get_type_hints(UiRunConfig)
    for field in fields(UiRunConfig):
        annotation = resolved_types[field.name]
        origin = get_origin(annotation)
        if annotation is bool:
            value = False
        elif origin is list:
            value = []
        elif origin is dict:
            value = {}
        elif annotation is float:
            value = 0.0
        elif field.default is not MISSING:
            value = field.default
        else:
            value = ""
        values[field.name] = value
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
    if mode in {"GENERATE", "MODIFY"}:
        from change_v2_fixtures import format_names, run, scenario, staged_path

        worker, client, responder = scenario(
            tmp_path,
            mode,
            batch=False,
            maximum_quality=maximum_quality,
        )
        results, errors = run(worker, client)
        assert errors == []
        assert results[0]["status"] == "files_complete_unverified"
        assert staged_path(worker, "hello.txt").read_text(encoding="utf-8") == (
            "content:hello.txt\n"
        )
        assert not (tmp_path / "out" / "hello.txt").exists()
        names = format_names(responder)
        assert names[-1] == "FILE_CONTENT_V1"
        prefix = "A" if mode == "GENERATE" else "B"
        assert (
            f"{prefix}2Q_QUALITY_GATE_V3" in names
        ) is maximum_quality
        state = json.loads(
            (tmp_path / "LOG" / worker.log.run_id / "run_state.json").read_text(
                encoding="utf-8"
            )
        )
        assert state["preparation_snapshot"]["graph"]["contract"] == (
            "IMPLEMENTATION_GRAPH_V3"
        )
        return

    worker = make_worker(tmp_path, mode)
    worker.cfg.maximum_quality = maximum_quality
    client = Mock()
    from kajovo.core.context_compiler import content_hash

    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000,
        "request_hash": content_hash(payload),
    }
    client.upload_file.return_value = {"id": "file_test"}
    client.retrieve_file.return_value = {
        "id": "file_test",
        "filename": "input.txt",
        "bytes": 100,
    }
    if mode == "QA":
        answer = {
            "result": {
                "status": "ready",
                "data": {
                    "answer": "Odpověď podle dostupných podkladů.",
                    "claims": [],
                    "limitations": [],
                },
            }
        }
        client.create_response.return_value = response(0, answer)
    else:
        worker.cfg.qfile_output_path = "hello.txt"
        worker.cfg.qfile_output_format = "txt"
        worker.cfg.qfile_suggest_path = False
        client.create_response.return_value = response(
            0, {"content": "hello\n"}
        )

    errors, results = [], []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
        worker.run()
    assert errors == []
    assert len(results) == 1
    if mode == "QA":
        assert results[0]["status"] == "completed"
        assert results[0]["text"].startswith("Odpověď")
    else:
        assert results[0]["status"] == "files_complete_unverified"
        state = json.loads(
            (tmp_path / "LOG" / worker.log.run_id / "run_state.json").read_text(
                encoding="utf-8"
            )
        )
        staged = next(
            row for row in state["staged_files"]
            if row["path"] == "hello.txt"
        )
        staged_path = (
            tmp_path / "LOG" / worker.log.run_id / staged["staged_path"]
        )
        assert staged_path.read_text(encoding="utf-8") == "hello\n"
        assert not (tmp_path / "out" / "hello.txt").exists()


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
    from kajovo.core.context_compiler import content_hash

    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 100,
        "request_hash": content_hash(payload),
    }
    client.create_response.return_value = response(1, "answer")
    original_save = worker.log.save_json

    def fail_only_after_provider(kind, *args, **kwargs):
        if kind == "responses":
            raise OSError("disk failure")
        return original_save(kind, *args, **kwargs)

    with patch.object(worker.log, "save_json", side_effect=fail_only_after_provider):
        with pytest.raises(OSError):
            worker._create_response(
                client, {"model": "gpt-4o-mini", "input": "test"}
            )
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


def test_invalid_single_file_contract_retries_and_preserves_output(tmp_path):
    from change_v2_fixtures import run, scenario

    def invalid(name, value, _data):
        if name == "FILE_CONTENT_V1":
            return {"content": 7}
        return value

    worker, client, _responder = scenario(
        tmp_path, "GENERATE", mutate=invalid
    )
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    target = out / "hello.txt"
    target.write_text("original", encoding="utf-8")
    results, errors = run(worker, client)
    assert errors and not results
    assert target.read_text(encoding="utf-8") == "original"
    assert not list(
        (tmp_path / "LOG" / worker.log.run_id / "staging").glob(
            "**/hello.txt"
        )
    )


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
    state = json.loads(
        (tmp_path / "LOG" / worker.log.run_id / "run_state.json").read_text(
            encoding="utf-8"
        )
    )
    staged = next(row for row in state["staged_files"] if row["path"] == "empty.txt")
    assert (
        tmp_path / "LOG" / worker.log.run_id / staged["staged_path"]
    ).read_bytes() == b""
    assert not (tmp_path / "out" / "empty.txt").exists()


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
    from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
    from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-5.6-luna", input_text="test")])
    cfg = CascadeRunConfig("project", definition, "", str(tmp_path / "out"), run_id="RUN_090920261200_TEST")
    worker = CascadeRunExecutor(cfg, settings, "test")
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
    assert not errors
    assert results[0]["run_id"] == cfg.run_id
    assert list((tmp_path / "LOG" / cfg.run_id / "responses").glob("*.json"))


def test_batch_uses_only_supported_jsonl_fields(tmp_path):
    from change_v2_fixtures import run, scenario

    worker, client, _responder = scenario(
        tmp_path, "MODIFY", batch=True
    )
    results, errors = run(worker, client)
    assert errors == []
    assert results[0]["batch_id"] == "batch_work"
    path = client.upload_file.call_args.args[0]
    with open(path, encoding="utf-8") as handle:
        request = json.loads(handle.readline())
    assert set(request) == {"custom_id", "method", "url", "body"}
    assert request["body"]["text"]["format"]["name"] == "FILE_CONTENT_V1"
    assert set(
        request["body"]["text"]["format"]["schema"]["properties"]
    ) == {"content"}
