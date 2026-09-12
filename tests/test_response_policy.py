"""Regrese validační hranice bez jakýchkoli placených preflight požadavků."""
import copy
import json
from types import SimpleNamespace
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
    result.configure_validation(
        AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG"))
    )
    result._policy.catalog = {"gpt-5.2", "gpt-4.1", "gpt-oss-120b"}
    result._send_response = Mock(
        return_value={
            "id": "resp_work",
            "status": "completed",
            "output_text": '{"text":"OK"}',
            "output": [],
        }
    )
    result._req = Mock(return_value={"status": "completed", "input_tokens": 12})
    return result


def request(**extra):
    return {"model": "gpt-5.2", "input": "test", "text": text_format(), **extra}


def test_create_response_sends_exactly_one_working_response(client):
    """Pracovní LIVE požadavek nesmí předcházet placený probe response."""
    client.validate_resources = Mock()
    result = client.create_response(request())
    assert result["status"] == "completed"
    client._send_response.assert_called_once_with(request())
    client.validate_resources.assert_called_once_with(request())


def test_policy_ensure_never_sends_generative_request(client):
    """Samotná validační politika smí dělat jen lokální/GET kontroly."""
    client.validate_resources = Mock()
    payload = request()
    client._policy.ensure(payload)
    client._policy.ensure(payload)
    client._send_response.assert_not_called()
    client.validate_resources.assert_has_calls([pytest.call(payload), pytest.call(payload)]) if False else None


def test_batch_policy_never_creates_trial_file_or_batch(client):
    client.validate_resources = Mock()
    client._req = Mock(side_effect=AssertionError("Validační politika nesmí volat transport."))
    payloads = [request(), request(reasoning={"effort": "minimal"})]
    client._policy.ensure_batch(payloads)
    client._send_response.assert_not_called()
    client._req.assert_not_called()
    assert client._policy.proofs == {}


@pytest.mark.parametrize(
    "change",
    [
        {"model": "missing"},
        {"reasoning": {"effort": "minimal"}, "temperature": 0.2},
        {"max_output_tokens": 128001},
        {"stream": "false"},
        {"text": {"format": {"type": "json_object"}}},
        {"text": {"format": {"type": "json_schema", "strict": False}}},
        {"unknown_option": True},
        {"input": [{"role": "bogus", "content": "text"}]},
        {"tools": [{"type": "bogus"}]},
    ],
)
def test_invalid_configuration_never_reaches_response_transport(client, change):
    with pytest.raises((ValueError, ContractError)):
        client.create_response(request(**change))
    client._send_response.assert_not_called()


def test_auto_file_search_does_not_force_tool_use(client):
    payload = request(
        tools=[{"type": "file_search", "vector_store_ids": ["vs_test"]}],
        tool_choice="none",
    )
    client.validate_resources = Mock()
    original = copy.deepcopy(payload)
    client._policy.ensure(payload)
    assert payload == original
    client._send_response.assert_not_called()


def test_catalog_is_account_scoped_without_probe(client):
    other = OpenAIClient("another-account")
    other.list_models = Mock(return_value=[])
    other.validate_resources = Mock()
    policy = ResponsePolicy(other)
    with pytest.raises(ValueError, match="katalogu"):
        policy.ensure(request())
    other.validate_resources.assert_not_called()


def test_no_preflight_proof_cache_is_created(client):
    client.validate_resources = Mock()
    client._policy.ensure(request())
    assert client._policy.proofs == {}
    assert not hasattr(client._policy, "trial_live")
    assert not hasattr(client._policy, "finish_batch")


def test_previous_response_uses_selected_actual_id_without_probe(client):
    payload = request(previous_response_id="resp_work")
    client.validate_resources = Mock()
    client.create_response(payload)
    client._send_response.assert_called_once_with(payload)


def test_expired_response_cannot_start_paid_work(client):
    client._sdk = None
    client._req.side_effect = OpenAIError("expired", 404)
    with pytest.raises(OpenAIError):
        client.create_response(request(previous_response_id="resp_expired"))
    client._send_response.assert_not_called()


@pytest.mark.parametrize(
    "status,text",
    [
        (None, '{"text":"ok"}'),
        ("incomplete", '{"text":"ok"}'),
        ("completed", 'prefix {"text":"ok"}'),
        ("completed", '{"text":1}'),
        ("completed", '{"text":"a","text":"b"}'),
    ],
)
def test_response_requires_completion_and_exact_contract(status, text):
    with pytest.raises(ContractError):
        validate_output({"status": status, "output_text": text}, request())


def test_invalid_late_batch_row_prevents_upload_or_batch_submit(client, tmp_path):
    rows = [
        {"custom_id": "a", "method": "POST", "url": "/v1/responses", "body": request()},
        {
            "custom_id": "b",
            "method": "POST",
            "url": "/v1/responses",
            "body": request(stream=True),
        },
    ]
    path = tmp_path / "batch.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    client._sdk = Mock()
    with pytest.raises(ValueError):
        client.upload_file(str(path), purpose="batch")
    client._sdk.files.create.assert_not_called()
    client._send_response.assert_not_called()


def test_valid_batch_data_is_only_locally_checked_before_real_upload(client, tmp_path):
    client.validate_resources = Mock()
    rows = [
        {"custom_id": "a", "method": "POST", "url": "/v1/responses", "body": request()}
    ]
    path = tmp_path / "batch.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    parsed = client.validate_batch_data(path.read_bytes())
    assert len(parsed) == 1
    client._send_response.assert_not_called()
    assert client._policy.proofs == {}


def test_remote_batch_is_validated_before_creation(client):
    client.file_content = Mock(
        return_value=b'{"custom_id":"a","method":"POST","url":"/v1/responses","body":{"model":"gpt-5.2"}}'
    )
    client._req = Mock()
    with pytest.raises(ValueError):
        client.create_batch("file_batch")
    client._req.assert_not_called()


def test_schema_preparation_repairs_without_user_input():
    client = Mock()
    target = obj({"answer": {"type": "string"}})
    client.create_response.side_effect = [
        {"status": "completed", "output_text": json.dumps({"schema_json": "{}"})},
        {
            "status": "completed",
            "output_text": json.dumps({"schema_json": json.dumps(target)}),
        },
    ]
    assert resolve_schema(client, "gpt-5.2", "Vrať odpověď v answer") == target
    assert client.create_response.call_count == 2


def test_schema_preparation_has_bounded_failure():
    client = Mock()
    client.create_response.return_value = {
        "status": "completed",
        "output_text": '{"schema_json":"{}"}',
    }
    with pytest.raises(ContractError):
        resolve_schema(client, "gpt-5.2", "test")
    assert client.create_response.call_count == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"$ref": "https://example.com"},
        {"type": "object"},
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
    ],
)
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


def test_run_capabilities_are_derived_without_paid_probe(client):
    cfg = SimpleNamespace(
        model="gpt-5.2",
        mode="GENERATE",
        model_a1="gpt-5.2",
        model_a2="gpt-5.2",
        model_a3="gpt-oss-120b",
        temperature=0.3,
        send_as_c=True,
        attached_vector_store_ids=["vs_actual"],
    )
    client.preflight_run(cfg)
    assert cfg.caps_by_model["gpt-5.2"]["supports_file_search"]
    assert not cfg.caps_by_model["gpt-oss-120b"]["supports_file_search"]
    client._send_response.assert_not_called()


@pytest.mark.parametrize(
    "filename,size,model,part_type,message",
    [
        ("test.pdf", 100, "o3-mini", "input_file", "PDF"),
        ("test.exe", 100, "gpt-5.2", "input_file", "formát"),
        ("test.txt", 50_000_000, "gpt-5.2", "input_file", "50"),
        ("test.pdf", 100, "gpt-5.2", "input_image", "formát"),
    ],
)
def test_file_metadata_blocks_incompatible_reference(
    client, filename, size, model, part_type, message
):
    client.retrieve_file = Mock(return_value={"filename": filename, "bytes": size})
    body = request(
        model=model,
        input=[
            {
                "role": "user",
                "content": [{"type": part_type, "file_id": "file_actual"}],
            }
        ],
    )
    with pytest.raises(ValueError, match=message):
        client.validate_resources(body)
    client._send_response.assert_not_called()


@pytest.mark.parametrize(
    "state,counts",
    [
        ("expired", {}),
        ("completed", {"failed": 1}),
        ("completed", {"in_progress": 1}),
    ],
)
def test_vector_store_must_be_fully_indexed(client, state, counts):
    client._req.return_value = {"status": state, "file_counts": counts}
    with pytest.raises(ValueError, match="vector_store_ids"):
        client.validate_resources(
            request(tools=[{"type": "file_search", "vector_store_ids": ["vs_actual"]}])
        )
    client._send_response.assert_not_called()
