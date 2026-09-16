import json
from dataclasses import MISSING, fields
from typing import get_origin, get_type_hints
from unittest.mock import Mock, patch

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.pipeline import UiRunConfig, RunWorker
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


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_file_chunks_use_file_contract(mode, tmp_path):
    worker = make_worker(tmp_path, mode)
    from delivery_fixtures import delivery_payloads
    requirements, plan, structure = delivery_payloads(mode)
    worker._delivery_snapshot = {
        "requirements": requirements, "plan": plan, "structure": structure,
        "mode": mode, "maximum_quality": False,
    }
    worker.cfg.preparation_snapshot = worker._delivery_snapshot
    client = Mock()
    client.count_input_tokens.return_value = {"input_tokens": 100, "request_hash": "hash"}
    client.create_response.return_value = response(0, {
        "contract": ("A3_FILE" if mode == "GENERATE" else "B3_FILE"),
        "path": "hello.txt", "action": "add" if mode == "MODIFY" else None,
        "content": "hello\n", "chunking": {"chunk_index": 0, "chunk_count": 1,
        "has_more": False, "next_chunk_index": None},
    })
    result, response_id = worker._gen_file_chunks(
        client,
        prev_id="",
        contract="A3_FILE" if mode == "GENERATE" else "B3_FILE",
        path="hello.txt",
        action="add" if mode == "MODIFY" else None,
        diag_file_ids=[],
    )
    assert result == "hello\n"
    assert response_id == "resp_0"
