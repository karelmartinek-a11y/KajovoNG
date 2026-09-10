"""Regrese na hranici odeslání, bez připojení ke skutečné službě."""
import copy
import json
import time
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
    result.configure_validation(AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG"), db_path=str(tmp_path / "db.sqlite")))
    result._policy.catalog = {"gpt-5.2"}
    result._policy.docs["gpt-5.2"] = {"checked_at": time.time(), "source": "https://developers.openai.com/api/docs/models/gpt-5.2.md",
        "responses": True, "batch": True, "features": ["structured_outputs", "file_uploads", "image_input", "file_search"],
        "max_output_tokens": 128000, "context_window": 400000, "reasoning": ["none", "low", "medium", "high", "xhigh"]}
    result._send_response = Mock(return_value={"id": "resp_probe", "status": "completed", "output_text": '{"text":"OK"}', "output": []})
    result._req = Mock(return_value={"status": "completed"})
    return result


def request(**extra):
    return {"model": "gpt-5.2", "input": "test", "text": text_format(), **extra}


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
    client.validate_access(request())
    client.validate_access(request())
    assert client._send_response.call_count == 1
    root = json.loads(client._policy.path.read_text(encoding="utf-8"))
    assert list(root["proofs"].values())[0]["state"] == "verified"
    client.validate_access(request(reasoning={"effort": "none"}, temperature=.2))
    assert client._send_response.call_count == 2
    assert len(list(client._policy.log_dir.glob("PROBE_*"))) == 2


def test_unknown_probe_is_not_a_positive_capability(client):
    client._send_response.side_effect = TimeoutError("read timeout")
    with pytest.raises(TimeoutError):
        client.create_response(request())
    assert client._send_response.call_count == 1
    assert list(client._policy.proofs.values())[0]["state"] == "unknown"


def test_failed_probe_blocks_working_request(client):
    client._send_response.side_effect = OpenAIError("unsupported", 400)
    with pytest.raises(OpenAIError):
        client.create_response(request())
    with pytest.raises(ValueError):
        client.create_response(request())
    assert client._send_response.call_count == 1


def test_unavailable_model_is_published_for_ui(client):
    from kajovo.core.model_capabilities import ModelCapabilitiesCache
    client._send_response.side_effect = OpenAIError("model_not_found", 404)
    with pytest.raises(OpenAIError):
        client.create_response(request())
    cache = ModelCapabilitiesCache(str(client._policy.path.parent.parent / "model_capabilities.json"))
    cache.bind(client.api_key)
    assert cache.get("gpt-5.2").ok_basic is False


def test_file_search_requires_actual_tool_call(client):
    with pytest.raises(ValueError, match="file search"):
        client.validate_access(request(tools=[{"type": "file_search", "vector_store_ids": ["vs_test"]}]))
    assert list(client._policy.proofs.values())[0]["state"] == "unknown"


def test_keys_share_evidence_but_endpoints_do_not(client):
    client.validate_access(request())
    other = OpenAIClient("another-account")
    policy = ResponsePolicy(other, str(client._policy.path.parent.parent), str(client._policy.log_dir))
    assert policy.path == client._policy.path and policy.proofs == client._policy.proofs
    other.list_models = Mock(return_value=[])
    with pytest.raises(ValueError, match="katalogu"):
        policy.ensure(request())
    other.base_url = "https://example.invalid/v1"
    isolated = ResponsePolicy(other, str(client._policy.path.parent.parent), str(client._policy.log_dir))
    assert isolated.path != policy.path and not isolated.proofs


def test_legacy_success_is_migrated_without_refreshing_timestamp(client):
    client.validate_access(request())
    original = json.loads(client._policy.path.read_text(encoding="utf-8"))
    original["version"] = 1
    original["identity"] = "old-key-fingerprint"
    legacy = client._policy.path.with_name("legacy.json")
    legacy.write_text(json.dumps(original), encoding="utf-8")
    client._policy.path.unlink()
    policy = ResponsePolicy(OpenAIClient("new-key"), str(client._policy.path.parent.parent))
    assert policy.proofs == original["proofs"]
    from kajovo.core.model_capabilities import ModelCapabilitiesCache
    cache = ModelCapabilitiesCache(str(policy.path.parent.parent / "model_capabilities.json"))
    cache.bind("third-key")
    assert cache.get("gpt-5.2").ok_basic


@pytest.mark.parametrize("status", [403, 404])
def test_access_error_does_not_erase_shared_capabilities(client, status):
    client.validate_access(request())
    original = copy.deepcopy(client._policy.proofs)
    client._sdk = None
    client._req.side_effect = OpenAIError("access denied", status)
    with pytest.raises(OpenAIError):
        OpenAIClient._send_response(client, request())
    assert client._policy.proofs == original


def test_expired_proof_is_retested(client):
    client.validate_access(request())
    for proof in client._policy.proofs.values():
        proof["tested_at"] = time.time() - 90000
    client.validate_access(request())
    assert client._send_response.call_count == 2


def test_previous_response_uses_real_probe_id(client):
    client.validate_access(request(previous_response_id="resp_work"))
    assert client._send_response.call_count == 2
    assert client._send_response.call_args.args[0]["previous_response_id"] == "resp_probe"


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
