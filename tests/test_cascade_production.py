"""Binární kaskáda odebírá bajty nástroje, ne modelový přepis base64."""
import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.cascade_contract import output_machine_key, runtime_schema_for_step
from kajovo.core.cascade_production import produce_binary_outputs
from kajovo.core.cascade_types import CascadeOutput, CascadeStep, CascadeInput
from kajovo.core.contracts import ContractError
from kajovo.core.runlog import RunLogger
from test_cascade_v2 import _worker, _client


def document_worker(tmp_path):
    output = CascadeOutput(kind="file", file_type="pdf", file_name="report.pdf")
    step = CascadeStep(model="gpt-5.6-luna", deterministic=True, outputs=[output])
    logger = RunLogger(str(tmp_path), "RUN_BINARY", project_name="test")
    response = {"id": "resp_document", "status": "completed", "output": [
        {"type": "code_interpreter_call", "status": "completed", "container_id": "cntr_one"},
        {"type": "message", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": json.dumps({"filename": "report.pdf"}), "annotations": [
                {"type": "container_file_citation", "filename": "report.pdf", "container_id": "cntr_one", "file_id": "cfile_one"}
            ]}
        ]}
    ]}
    worker = SimpleNamespace(logger=logger, _schema_request=Mock(return_value=response), _step_input_bindings=[])
    descriptor = {"contract": "CASCADE_BINARY_TASK_V1", "path": output.file_name, "instructions": "Vytvoř PDF zprávu."}
    return worker, step, output, {"input": "Podklady zprávy"}, {output_machine_key(output): descriptor}


def test_document_uses_tool_identity_and_reuses_archived_bytes(tmp_path):
    worker, step, output, payload, decoded = document_worker(tmp_path)
    client = Mock()
    client.container_file_content.return_value = b"%PDF-1.7\nexact tool bytes"
    result = produce_binary_outputs(worker, client, step, 1, payload, decoded)
    assert base64.b64decode(result[output_machine_key(output)]["content"]) == client.container_file_content.return_value
    request = worker._schema_request.call_args.args[3]
    assert request["tools"] == [{"type": "code_interpreter", "container": {"type": "auto", "file_ids": []}}]
    assert "previous_response_id" not in request
    assert runtime_schema_for_step(step)["properties"][output_machine_key(output)]["properties"]["contract"]["enum"] == ["CASCADE_BINARY_TASK_V1"]
    assert produce_binary_outputs(worker, client, step, 1, payload, decoded) == result
    worker._schema_request.assert_called_once()
    client.container_file_content.assert_called_once_with("cntr_one", "cfile_one")


def test_expired_document_download_does_not_regenerate(tmp_path):
    worker, step, _, payload, decoded = document_worker(tmp_path)
    client = Mock()
    client.container_file_content.side_effect = FileNotFoundError("expired")
    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            produce_binary_outputs(worker, client, step, 1, payload, decoded)
    worker._schema_request.assert_called_once()


def test_first_step_child_resume_reuses_paid_responses_before_upload(tmp_path, monkeypatch):
    from kajovo.core.cascade_types import CascadeDefinition
    from kajovo.core.recoverable_artifacts import load_run_state
    from test_cascade_v2 import _response

    source = tmp_path / "source.txt"
    source.write_text("zmrazený podklad", encoding="utf-8")
    helper, step, output, _, decoded = document_worker(tmp_path / "helper")
    step.title, step.input_text = "Dokument", "Zpracuj podklad"
    step.inputs = [CascadeInput(name="Podklad", source="local_file", value=str(source))]
    definition = CascadeDefinition(name="Obnovit PDF", steps=[step])
    client = _client()
    client.upload_file.return_value = {"id": "file_original"}
    client.create_response.side_effect = [
        _response("resp_primary", output, decoded[output_machine_key(output)]),
        helper._schema_request.return_value,
    ]
    client.container_file_content.side_effect = OSError("Přerušený download")
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **k: client)
    first = _worker(definition, tmp_path)
    errors = []
    first.finished_err.connect(errors.append)
    first.execute()
    assert errors and client.create_response.call_count == 2
    state = load_run_state(first.logger.paths.run_dir)
    assert state["status"] == "failed"
    source.write_text("nová nesouvisející verze", encoding="utf-8")
    resumed = CascadeDefinition.from_dict(definition.to_dict())
    resumed.run_from_step_id = step.id
    child = _worker(resumed, tmp_path)
    child.cfg.resume_snapshot = state["cascade_runtime"]
    child.cfg.resume_source_dir = first.logger.paths.run_dir
    child.cfg.input_bindings = state["cascade_input_artifacts"]
    resumed_client = _client()
    resumed_client.upload_file.return_value = {"id": "file_downloaded_output"}
    resumed_client.container_file_content.return_value = b"%PDF-1.7\nprovider bytes"
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **k: resumed_client)
    failures, results = [], []
    child.finished_err.connect(failures.append)
    child.finished_ok.connect(results.append)
    child.execute()
    assert not failures and results
    resumed_client.create_response.assert_not_called()
    resumed_client.create_image.assert_not_called()
    resumed_client.upload_file.assert_called_once()
    assert resumed_client.upload_file.call_args.args[0].endswith("report.pdf")
    calls = [call[0] for call in resumed_client.mock_calls]
    assert calls.index("container_file_content") < calls.index("upload_file")
    resumed_client.container_file_content.assert_called_once_with("cntr_one", "cfile_one")


def test_document_rejects_foreign_container_before_download(tmp_path):
    worker, step, _, payload, decoded = document_worker(tmp_path)
    response = copy.deepcopy(worker._schema_request.return_value)
    response["output"][1]["content"][0]["annotations"][0]["container_id"] = "cntr_foreign"
    worker._schema_request.return_value = response
    client = Mock()
    with pytest.raises(ContractError, match="kontejneru"):
        produce_binary_outputs(worker, client, step, 1, payload, decoded)
    client.container_file_content.assert_not_called()


def test_file_id_image_keeps_input_identity_and_typed_slot(tmp_path):
    from kajovo.core.cascade_types import CascadeDefinition
    output = CascadeOutput(kind="file", file_type="png", file_name="picture.png", file_mode="modify", modify_input_id="original")
    step = CascadeStep(model="gpt-5.6-luna", deterministic=True, outputs=[output], inputs=[
        CascadeInput(id="reference", name="Inspirace", source="file_id", value="file_reference"),
        CascadeInput(id="original", name="Originál", source="file_id", value="file_original"),
    ])
    worker = _worker(CascadeDefinition(name="Obrazy", steps=[step]), tmp_path)
    client = _client()
    client.retrieve_file.side_effect = lambda identity: {"id": identity, "filename": identity + ".png"}
    payload, _, _ = worker._prepare_step(step=step, idx=1, context={}, context_response_ids={}, values={}, client=client)
    parts = [part for message in payload["input"] for part in message["content"] if part.get("file_id")]
    assert [part["type"] for part in parts] == ["input_image", "input_image"]
    assert "input_id=original" in payload["instructions"]
    assert [row["input_id"] for row in worker._step_input_bindings] == ["reference", "original"]


def test_image_modify_uses_actual_image_endpoint_with_selected_original_first(tmp_path):
    from kajovo.core.cascade_types import CascadeDefinition
    output = CascadeOutput(kind="file", file_type="png", file_name="result.png", file_mode="modify", modify_input_id="original")
    step = CascadeStep(model="gpt-5.6-luna", deterministic=True, outputs=[output])
    worker = _worker(CascadeDefinition(name="Editace", steps=[step]), tmp_path)
    worker.logger = RunLogger(str(tmp_path / "LOG"), "RUN_IMAGE", project_name="test")
    worker.cfg.run_id = worker.logger.run_id
    worker.cfg.execution_approval_id = "approved"
    worker._register_operation_run(worker.logger.run_id, "approved", [])
    worker._current_step_record_id = worker.logger.bundle.ensure_step(step.id)["step_id"]
    worker._cascade_expected_hashes = {"result.png": None}
    worker._step_input_bindings = [
        {"input_id": "reference", "name": "Reference", "type": "input_image", "file_id": "file_ref", "filename": "ref.png"},
        {"input_id": "original", "name": "Originál", "type": "input_image", "file_id": "file_original", "filename": "original.png"},
    ]
    client = Mock()
    client.create_image.return_value = {"id": "image_1", "data": [{"b64_json": base64.b64encode(b"provider bytes").decode()}]}
    decoded = {output_machine_key(output): {"contract": "CASCADE_BINARY_TASK_V1", "path": "result.png", "instructions": "Uprav originál."}}
    result = produce_binary_outputs(worker, client, step, 1, {"input": "Editace"}, decoded)
    endpoint, body = client.create_image.call_args.args
    assert endpoint == "/v1/images/edits"
    assert body["images"] == [{"file_id": "file_original"}, {"file_id": "file_ref"}]
    assert base64.b64decode(result[output_machine_key(output)]["content"]) == b"provider bytes"
    produce_binary_outputs(worker, client, step, 1, {"input": "Editace"}, decoded)
    client.create_image.assert_called_once()
    client.create_response.assert_not_called()
