"""Odeslání pracovní dávky musí být podmíněno výsledkem skutečné zkušební dávky."""
import copy
import json
from unittest.mock import Mock

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.response_policy import PreflightPending, ResponsePolicy
from kajovo.core.structured_output import text_format


def payload(**extra):
    return {"model": "gpt-5.2", "input": "test", "text": text_format(), **extra}


def data(bodies):
    return ('\n'.join(json.dumps({"custom_id": str(i), "method": "POST", "url": "/v1/responses", "body": b})
                      for i, b in enumerate(bodies)) + '\n').encode()


@pytest.fixture
def client(tmp_path):
    c = OpenAIClient("test", timeout_s=0)
    c.configure_validation(AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG"), db_path=str(tmp_path / "db.sqlite")))
    c._policy.catalog = {"gpt-5.2", "gpt-4.1", "gpt-5.1-codex"}
    c.count_input_tokens = Mock(return_value=12)
    c.validate_resources = Mock()
    c._send_response = Mock(side_effect=AssertionError("Batch se nesmí ověřovat přes live Responses"))
    c.delete_file = Mock()
    c._trial_rows = []
    def req(method, path, **kwargs):
        if path == "/files":
            c._trial_rows = [json.loads(line) for line in kwargs["files"]["file"][1].getvalue().splitlines()]
            return {"id": "file_trial"}
        if path == "/batches": return {"id": "batch_trial"}
        raise AssertionError(path)
    c._req = Mock(side_effect=req)
    c.retrieve_batch = Mock(return_value={"id": "batch_trial", "status": "completed", "output_file_id": "file_output"})
    def content(fid):
        return '\n'.join(json.dumps({"custom_id": r["custom_id"], "response": {"status_code": 200,
            "body": {"id": "resp_"+str(i), "status": "completed", "output_text": '{"text":"OK"}'}}})
                         for i,r in enumerate(c._trial_rows)).encode()
    c.file_content = Mock(side_effect=content)
    return c


def test_batch_uses_actual_batch_and_preserves_every_parameter(client):
    body = payload(reasoning={"effort": "none"}, temperature=.4, top_p=.8, max_output_tokens=100,
        tools=[{"type": "file_search", "vector_store_ids": ["vs_actual"], "max_num_results": 5}],
        tool_choice="none", store=False, include=["file_search_call.results"],
        input=[{"role": "user", "content": [{"type": "input_file", "file_id": "file_actual"}]}])
    client.validate_batch_data(data([body]))
    assert client._trial_rows[0]["body"] == body
    assert client._req.call_args_list[1].args == ("POST", "/batches")
    client._send_response.assert_not_called()
    assert list(client._policy.proofs.values())[0]["state"] == "verified"


def test_all_rows_checked_before_any_trial(client):
    with pytest.raises(ValueError):
        client.validate_batch_data(data([payload(), payload(reasoning={"effort": "minimal"})]))
    client._req.assert_not_called()
    client.validate_resources.assert_not_called()


def test_mixed_models_rejected_before_upload(client):
    with pytest.raises(ValueError, match="jediný model"):
        client.validate_batch_data(data([payload(), payload(model="gpt-4.1")]))
    client._req.assert_not_called()


def test_no_batch_codex_rejected_before_upload(client):
    with pytest.raises(ValueError, match="Batch"):
        client.validate_batch_data(data([payload(model="gpt-5.1-codex")]))
    client._req.assert_not_called()


def test_pending_trial_blocks_and_resumes_without_duplicate_submission(client):
    client.retrieve_batch.return_value = {"id": "batch_trial", "status": "validating"}
    with pytest.raises(PreflightPending, match="nebyla odeslána"):
        client.validate_batch_data(data([payload()]))
    first = client._req.call_count
    old = client._policy
    client._policy = ResponsePolicy(client, str(old.path.parent.parent), str(old.log_dir))
    client._policy.catalog = old.catalog
    client.retrieve_batch.return_value = {"id": "batch_trial", "status": "completed", "output_file_id": "file_output"}
    client.validate_batch_data(data([payload()]))
    assert client._req.call_count == first


def test_batch_parameter_error_has_exact_param_and_blocks_working_batch(client):
    def content(_):
        return json.dumps({"custom_id": client._trial_rows[0]["custom_id"], "response": {"status_code": 400,
            "body": {"error": {"param": "tools[0].vector_store_ids", "code": "unsupported_parameter", "message": "Unsupported parameter"}}}}).encode()
    client.file_content.side_effect = content
    with pytest.raises(OpenAIError, match=r"BATCH.*tools\[0\].vector_store_ids"):
        client.validate_batch_data(data([payload()]))
    count = client._req.call_count
    with pytest.raises(OpenAIError): client.validate_batch_data(data([payload()]))
    assert client._req.call_count == count


def test_late_row_missing_blocks_entire_batch(client):
    client.file_content.side_effect = lambda _: b''
    with pytest.raises(OpenAIError, match="chybí"):
        client.validate_batch_data(data([payload(), payload(input="second")]))
    assert all(p["state"] == "failed" for p in client._policy.proofs.values())


def test_fingerprint_includes_schema_files_limits_and_all_flags(client):
    base = payload()
    original = client._policy.key(base, True)
    variations = [payload(store=False), payload(max_output_tokens=99), payload(metadata={"a": "b"}),
        payload(input=[{"role": "user", "content": [{"type": "input_file", "file_id": "file_a"}]}])]
    changed = copy.deepcopy(base)
    changed["text"]["format"]["schema"]["properties"]["text"]["enum"] = ["OK"]
    variations.append(changed)
    assert all(client._policy.key(p, True) != original for p in variations)
    assert client._policy.key(base, False) != original


def test_live_success_does_not_authorize_batch(client):
    client._policy.proofs[client._policy.key(payload())] = {"state": "verified"}
    client.validate_batch_data(data([payload()]))
    assert client._trial_rows


def test_submission_timeout_not_retried_automatically(client):
    previous = client._req.side_effect
    def req(method, path, **kwargs):
        if path == "/batches": raise OpenAIError("timeout")
        return previous(method, path, **kwargs)
    client._req.side_effect = req
    with pytest.raises(OpenAIError): client.validate_batch_data(data([payload()]))
    count = client._req.call_count
    with pytest.raises(OpenAIError): client.validate_batch_data(data([payload()]))
    assert client._req.call_count == count
