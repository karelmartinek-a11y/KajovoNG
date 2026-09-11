"""Regrese na hranici odeslání, bez připojení ke skutečné službě."""
import copy
import json
from unittest.mock import Mock

import pytest

from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.response_policy import ResponsePolicy
from kajovo.core.structured_output import text_format, validate_output, obj, resolve_schema
from kajovo.core.contracts import ContractError


@pytest.fixture
def client(tmp_path):
    result = OpenAIClient("test-account")
    result.configure_validation(AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG")))
    result._policy.catalog = {"gpt-5.2"}
    result._send_response = Mock(return_value={"id": "resp_probe", "status": "completed", "output_text": '{"text":"OK"}', "output": []})
    result._req = Mock(return_value={"status": "completed", "input_tokens": 12})
    return result


def request(**extra):
    return {"model": "gpt-5.2", "input": "test", "text": text_format(), **extra}


def test_live_request_needs_no_price_or_token_count_endpoint(client):
    client.validate_resources = Mock()
    client._req.side_effect = AssertionError("Pomocný HTTP požadavek není potřeba.")
    result = client.create_response(request())
    assert result["status"] == "completed"
    assert client._send_response.call_count == 2
    client._req.assert_not_called()


@pytest.mark.parametrize("change", [
    {"model": "missing"}, {"reasoning": {"effort": "minimal"}}, {"max_output_tokens": 128001},
    {"temperature": .2}, {"stream": "false"}, {"text": {"format": {"type": "json_object"}}},
    {"text": {"format": {"type": "json_schema", "strict": False}}}, {"unknown_option": True},
    {"input": [{"role": "bogus", "content": "text"}]}, {"tools": [{"type": "bogus"}]},
])
def test_invalid_configuration_never_reaches_response_transport(client, change):
    with pytest.raises((ValueError, ContractError)):
        client.create_response(request(**change))
    client._send_response.assert_not_called()


def test_exact_combination_probed_once_and_persisted(client):
    client._policy.ensure(request())
    client._policy.ensure(request())
    assert client._send_response.call_count == 1
    root = json.loads(client._policy.path.read_text(encoding="utf-8"))
    assert list(root["proofs"].values())[0]["state"] == "verified"
    client._policy.ensure(request(reasoning={"effort": "none"}, temperature=.2))
    assert client._send_response.call_count == 2
    assert len(list(client._policy.log_dir.glob("PREFLIGHT_*"))) == 2


def test_unknown_probe_is_not_a_positive_capability(client):
    client._send_response.side_effect = TimeoutError("read timeout")
    with pytest.raises(OpenAIError):
        client.create_response(request())
    assert client._send_response.call_count == 1
    assert list(client._policy.proofs.values())[0]["state"] == "failed"


def test_failed_preflight_blocks_working_request(client):
    client._send_response.side_effect = OpenAIError("unsupported temperature", 400, param="temperature", code="unsupported_parameter")
    with pytest.raises(OpenAIError, match="Rozpor s pevnou maticí.*temperature"):
        client.create_response(request())
    assert client._send_response.call_count == 1


def test_auto_file_search_does_not_force_tool_use(client):
    payload = request(tools=[{"type": "file_search", "vector_store_ids": ["vs_test"]}], tool_choice="none")
    client._policy.ensure(payload)
    assert client._send_response.call_args.args[0]["tool_choice"] == "none"


def test_keys_and_endpoints_never_share_evidence(client):
    client._policy.ensure(request())
    other = OpenAIClient("another-account")
    policy = ResponsePolicy(other, str(client._policy.path.parent.parent), str(client._policy.log_dir))
    assert policy.path != client._policy.path and not policy.proofs
    other.list_models = Mock(return_value=[])
    with pytest.raises(ValueError, match="katalogu"):
        policy.ensure(request())


def test_legacy_success_is_not_migrated(client):
    client._policy.ensure(request())
    original = json.loads(client._policy.path.read_text(encoding="utf-8"))
    original["version"] = 1
    client._policy.path.write_text(json.dumps(original), encoding="utf-8")
    policy = ResponsePolicy(client, str(client._policy.path.parent.parent))
    assert not policy.proofs


@pytest.mark.parametrize("status", [403, 404])
def test_access_error_does_not_erase_shared_capabilities(client, status):
    client._policy.ensure(request())
    original = copy.deepcopy(client._policy.proofs)
    client._sdk = None
    client._req.side_effect = OpenAIError("access denied", status)
    with pytest.raises(OpenAIError):
        OpenAIClient._send_response(client, request())
    assert client._policy.proofs == original


def test_each_working_dispatch_requires_a_trial(client):
    client.create_response(request())
    client.create_response(request())
    assert client._send_response.call_count == 4


def test_previous_response_uses_selected_actual_id(client):
    client._policy.ensure(request(previous_response_id="resp_work"))
    assert client._send_response.call_count == 1
    assert client._send_response.call_args.args[0]["previous_response_id"] == "resp_work"


def test_expired_response_cannot_start_paid_work(client):
    client._req.side_effect = OpenAIError("expired", 404)
    with pytest.raises(OpenAIError):
        client.create_response(request(previous_response_id="resp_expired"))
    client._send_response.assert_not_called()


@pytest.mark.parametrize("status,text", [(None, '{"text":"ok"}'), ("incomplete", '{"text":"ok"}'),
    ("completed", 'prefix {"text":"ok"}'), ("completed", '{"text":1}'), ("completed", '{"text":"a","text":"b"}')])
def test_response_requires_completion_and_exact_contract(status, text):
    with pytest.raises(ContractError):
        validate_output({"status": status, "output_text": text}, request())


def test_invalid_late_batch_row_prevents_any_probe_or_upload(client, tmp_path):
    rows = [{"custom_id": "a", "method": "POST", "url": "/v1/responses", "body": request()},
            {"custom_id": "b", "method": "POST", "url": "/v1/responses", "body": request(stream=True)}]
    path = tmp_path / "batch.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    client._sdk = Mock()
    with pytest.raises(ValueError):
        client.upload_file(str(path), purpose="batch")
    client._sdk.files.create.assert_not_called()
    client._send_response.assert_not_called()


def test_remote_batch_is_validated_before_creation(client):
    client.file_content = Mock(return_value=b'{"custom_id":"a","method":"POST","url":"/v1/responses","body":{"model":"gpt-5.2"}}')
    client._req = Mock()
    with pytest.raises(ValueError):
        client.create_batch("file_batch")
    client._req.assert_not_called()


def test_schema_preparation_repairs_without_user_input():
    client = Mock()
    target = obj({"answer": {"type": "string"}})
    client.create_response.side_effect = [
        {"status": "completed", "output_text": json.dumps({"schema_json": "{}"})},
        {"status": "completed", "output_text": json.dumps({"schema_json": json.dumps(target)})}]
    assert resolve_schema(client, "gpt-5.2", "Vrať odpověď v answer") == target
    assert client.create_response.call_count == 2
    for call in client.create_response.call_args_list:
        assert call.args[0]["text"]["format"]["strict"] is True


def test_schema_preparation_has_bounded_failure():
    client = Mock()
    client.create_response.return_value = {"status": "completed", "output_text": '{"schema_json":"{}"}'}
    with pytest.raises(ContractError):
        resolve_schema(client, "gpt-5.2", "test")
    assert client.create_response.call_count == 3


@pytest.mark.parametrize("bad", [{"$ref": "https://example.com"}, {"type": "object"},
    {"type": "object", "properties": {"x": {"type": "string"}}, "required": [], "additionalProperties": False}])
def test_unsupported_schema_cannot_reach_api(client, bad):
    payload = request()
    payload["text"]["format"]["schema"] = copy.deepcopy(bad)
    with pytest.raises(ValueError):
        client.create_response(payload)
    client._send_response.assert_not_called()


def test_known_contracts_are_native_for_gpt52(tmp_path):
    from test_workflows import make_worker
    from kajovo.core.generate_batch import build_manifest
    from test_generate_batch import specification
    worker = make_worker(tmp_path, "MODIFY")
    for contract in ("B1_PLAN", "B2_STRUCTURE"):
        payload = worker._payload_base("gpt-5.2", f"KONTRAKT {contract}: test", [], None)
        assert payload["text"]["format"]["strict"] is True
        assert payload["text"]["format"]["name"] == contract
    manifest = build_manifest("r", "test", {}, specification(), "gpt-5.2", None)
    assert all(row["body"]["text"]["format"]["strict"] for row in manifest["requests"])


def test_sdk_and_rest_use_same_response_timeout(client):
    client._sdk = None
    client._req = Mock(return_value={})
    client._send_response = OpenAIClient._send_response.__get__(client)
    client._send_response(request())
    assert client._req.call_args.kwargs["timeout"] == 300


def test_generate_batch_a3_does_not_inherit_preparation_tools(client):
    from types import SimpleNamespace
    cfg = SimpleNamespace(model="gpt-5.2", mode="GENERATE", model_a1="gpt-5.2", model_a2="gpt-5.2",
        model_a3="gpt-oss-120b", temperature=.3, send_as_c=True, attached_vector_store_ids=["vs_actual"])
    client._policy.catalog.add("gpt-oss-120b")
    client.preflight_run(cfg)
    assert cfg.caps_by_model["gpt-5.2"]["supports_file_search"]
    assert not cfg.caps_by_model["gpt-oss-120b"]["supports_file_search"]
    client._send_response.assert_not_called()


@pytest.mark.parametrize("filename,size,model,part_type,message", [
    ("test.pdf", 100, "o3-mini", "input_file", "PDF"),
    ("test.exe", 100, "gpt-5.2", "input_file", "formát"),
    ("test.txt", 50_000_000, "gpt-5.2", "input_file", "50"),
    ("test.pdf", 100, "gpt-5.2", "input_image", "formát"),
])
def test_file_metadata_blocks_incompatible_reference(client, filename, size, model, part_type, message):
    client.retrieve_file = Mock(return_value={"filename": filename, "bytes": size})
    body = request(model=model, input=[{"role": "user", "content": [{"type": part_type, "file_id": "file_actual"}]}])
    with pytest.raises(ValueError, match=message):
        client.validate_resources(body)
    client._send_response.assert_not_called()


@pytest.mark.parametrize("state,counts", [("expired", {}), ("completed", {"failed": 1}), ("completed", {"in_progress": 1})])
def test_vector_store_must_be_fully_indexed(client, state, counts):
    client._req.return_value = {"status": state, "file_counts": counts}
    with pytest.raises(ValueError, match="vector_store_ids"):
        client.validate_resources(request(tools=[{"type": "file_search", "vector_store_ids": ["vs_actual"]}]))
    client._send_response.assert_not_called()
