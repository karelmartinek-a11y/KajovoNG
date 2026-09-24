"""Lokální příčina musí zůstat rozlišitelná od neurčitého submitu."""
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from change_v2_fixtures import scenario, run
from test_workflows import make_worker

from kajovo.core.openai_client import OpenAIClient
from kajovo.core.openai_transport import OpenAIError
from kajovo.core.response_journal import ResponseJournal
from kajovo.core.runs.response_execution import _work_order_for_payload
from kajovo.core.user_errors import describe_error


def test_generate_through_real_client_and_request_evidence(tmp_path):
    worker, _, responder = scenario(tmp_path, "GENERATE", batch=False, maximum_quality=False)
    client = OpenAIClient("fake-test-key")

    def request(method, url, **kwargs):
        path = urlsplit(url).path
        if method == "GET" and path == "/v1/models":
            body = {"data": [{"id": worker.cfg.model}]}
        elif method == "POST" and path == "/v1/responses/input_tokens":
            body = {"input_tokens": 1000}
        elif method == "POST" and path == "/v1/responses":
            body = responder(kwargs["json"])
        else:
            raise AssertionError(f"Neočekávaný HTTP požadavek: {method} {path}")
        return Mock(status_code=200, headers={"content-type": "application/json"},
                    content=json.dumps(body).encode("utf-8"))

    # Nahrazena je pouze síť; klient, validace a evidence zůstávají skutečné.
    client.session.request = Mock(side_effect=request)
    results, errors = run(worker, client)
    assert not errors, errors
    assert results
    assert any((call.kwargs.get("json") or {}).get("text", {}).get("format", {}).get("name")
               == "FILE_CONTENT_V1" for call in client.session.request.call_args_list)
    assert "request.response_received" in worker.log.bundle.events_path.read_text("utf-8")
    client.session.close()


def test_work_order_uses_actual_preparation_step(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    worker._progress_stage = "Lokální validace"
    step_id = worker.log.begin_validated_step("A0R", kind="preparation", model=worker.cfg.model)
    order = _work_order_for_payload(worker, {"model": worker.cfg.model, "input": "test"}, 0)
    assert order.step_id == step_id
    assert order.stage == "A0R"
    client = OpenAIClient("fake-test-key")
    client.evidence_bundle = worker.log.bundle
    client.evidence_step_id = order.step_id
    response = Mock(status_code=200, headers={"content-type": "application/json"},
                    content=b'{"id":"resp_ok","status":"completed"}')
    client.session.request = Mock(return_value=response)
    assert client._req("POST", "/responses", json_body={"model": worker.cfg.model, "input": "test"})["id"] == "resp_ok"
    client.session.request.assert_called_once()
    assert "request.response_received" in worker.log.bundle.events_path.read_text("utf-8")
    client.session.close()


def test_local_evidence_error_never_sends_or_becomes_unknown(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    client = OpenAIClient("fake-test-key")
    client.evidence_bundle = worker.log.bundle
    client.evidence_step_id = "Lokální validace"
    client.session.request = Mock(side_effect=AssertionError("Síť nesmí být volána"))
    journal = ResponseJournal(worker.log)
    # Testuje skutečný transport a logger, nikoli náhradu create_response.
    client.create_response = lambda body: client._req("POST", "/responses", json_body=body)
    with pytest.raises(OpenAIError) as failure:
        journal.execute(client, {"model": "gpt-5.4", "input": "test"},
                        stopped=lambda: False, cancelled=lambda: False, progress=Mock())
    assert failure.value.request_sent is False
    assert isinstance(failure.value.__cause__, KeyError)
    assert describe_error(failure.value).code == "local_request_evidence_failed"
    assert "KeyError" in describe_error(failure.value).detail
    client.session.request.assert_not_called()
    assert list(journal.entries.values())[0]["status"] == "rejected"
    client.session.close()


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_preparation_uses_source_pack_without_duplicate_in_upload(tmp_path, mode):
    worker, client, responder = scenario(tmp_path, mode, batch=False, maximum_quality=False)
    results, errors = run(worker, client)
    assert not errors
    assert results
    client.create_vector_store.assert_not_called()
    assert not any("in_dir_" in str(call) for call in client.upload_file.call_args_list)


def test_failed_run_has_terminal_lifecycle_and_original_cause(tmp_path):
    worker, client, _ = scenario(tmp_path, "GENERATE", batch=False, maximum_quality=False)
    client.create_response.side_effect = OpenAIError("Lokální evidence selhala", code="local_request_evidence_failed")
    client.create_response.side_effect.request_sent = False
    results, errors = run(worker, client)
    assert errors and not results
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["lifecycle_status"] == "failed"
    assert state["failure_detail"]["code"] == "local_request_evidence_failed"


def test_disabled_file_search_does_not_index_in(tmp_path):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.in_dir = str(tmp_path)
    worker.cfg.use_file_search = False
    worker.cfg.model_caps.update(supports_vector_store=True, supports_file_search=True)
    bundle = tmp_path / "input.txt"
    bundle.write_text("schválený text", encoding="utf-8")
    worker._zip_in_dir = Mock(return_value=str(bundle))
    client = Mock()
    client.upload_file.return_value = {"id": "file_input"}
    assert worker._prepare_in_dir_upload(client)["file_id"] == "file_input"
    client.create_vector_store.assert_not_called()


@pytest.mark.parametrize("successful_requests", [0, 1])
def test_unknown_submit_keeps_original_cause_and_terminal_lifecycle(tmp_path, successful_requests):
    worker, client, _ = scenario(tmp_path, "GENERATE", batch=False, maximum_quality=False)
    original = client.create_response.side_effect
    calls = 0
    def respond(payload):
        nonlocal calls
        calls += 1
        if calls > successful_requests:
            raise TimeoutError("Původní příčina výpadku")
        return original(payload)
    client.create_response.side_effect = respond
    details = []
    worker.failure_detail.connect(details.append)
    _, errors = run(worker, client)
    assert errors
    state = json.loads(Path(worker.log.state_path).read_text("utf-8"))
    assert state["status"] == "submission_unknown"
    assert state["lifecycle_status"] == "unknown_remote_submission"
    assert "TimeoutError" in state["failure_detail"]["detail"]
    assert details[0].domain == "timeout"
    events = worker.log.bundle.events_path.read_text("utf-8")
    assert "Původní příčina výpadku" in events
