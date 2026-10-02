"""Nativní obálky se ověřují odděleně od doménového výsledku Responses."""
from __future__ import annotations

import copy

import jsonschema
import pytest
from native_provider_fixtures import native_fixture
from test_openai_transport import FakeResponse, FakeSession, transport

from kajovo.core.openai_transport import OpenAIError, SubmissionOutcomeUnknown, operation_spec
from kajovo.core.provider_contracts import (
    DELETES,
    LISTS,
    MODELS,
    native_contract,
    validate_native_response,
    validate_native_schema,
)

CASES = [
    ("list_models", "GET", "/models", {"data": [{"id": "gpt-5.6-luna"}]}, {}),
    ("list_files", "GET", "/files", {"data": [{"id": "file_x"}], "has_more": False}, {}),
    ("list_vector_stores", "GET", "/vector_stores", {"data": [{"id": "vs_x"}], "has_more": False}, {}),
    ("list_vector_store_files", "GET", "/vector_stores/vs_x/files", {"data": [{"id": "file_x"}], "has_more": False}, {}),
    ("list_batches", "GET", "/batches", {"data": [{"id": "batch_x"}], "has_more": False}, {}),
    ("upload_file", "POST", "/files", {"id": "file_x"}, {"purpose": "user_data"}),
    ("retrieve_file", "GET", "/files/file_x", {"id": "file_x"}, {}),
    ("create_vector_store", "POST", "/vector_stores", {"id": "vs_x"}, {"name": "Test"}),
    ("retrieve_vector_store", "GET", "/vector_stores/vs_x", {"id": "vs_x"}, {}),
    ("attach_vector_store_file", "POST", "/vector_stores/vs_x/files", {"id": "file_x"}, {"file_id": "file_x"}),
    ("retrieve_vector_store_file", "GET", "/vector_stores/vs_x/files/file_x", {"id": "file_x"}, {}),
    ("update_vector_store_file", "POST", "/vector_stores/vs_x/files/file_x", {"id": "file_x", "attributes": {"category": "test"}}, {"attributes": {"category": "test"}}),
    ("create_batch", "POST", "/batches", {"id": "batch_x"}, {"input_file_id": "file_input", "endpoint": "/v1/responses", "completion_window": "24h"}),
    ("retrieve_batch", "GET", "/batches/batch_x", {"id": "batch_x"}, {}),
    ("cancel_batch", "POST", "/batches/batch_x/cancel", {"id": "batch_x"}, {}),
    ("create_image", "POST", "/images/generations", {"data": [{"b64_json": "aW1hZ2U="}]}, {"n": 1}),
    ("input_token_count", "POST", "/responses/input_tokens", {"input_tokens": 1}, {}),
    ("delete_file", "DELETE", "/files/file_x", {"id": "file_x", "deleted": True}, {}),
    ("delete_vector_store", "DELETE", "/vector_stores/vs_x", {"id": "vs_x", "deleted": True}, {}),
    ("delete_vector_store_file", "DELETE", "/vector_stores/vs_x/files/file_x", {"id": "file_x", "deleted": True}, {}),
]


def test_every_native_operation_has_a_test_case():
    assert {case[0] for case in CASES} == set(MODELS) | set(LISTS) | set(DELETES)


@pytest.mark.parametrize("operation,method,path,raw,request_body", CASES)
def test_native_envelope_validated_before_return(operation, method, path, raw, request_body):
    value = native_fixture(method, path, copy.deepcopy(raw), request_body)
    assert validate_native_response(operation, value, path=path, request=request_body) == value
    session = FakeSession(FakeResponse(payload=value, headers={"x-request-id": "req_x"}))
    result = transport(session).request(operation_spec(method, path), method, path, json_body=request_body)
    assert result == {**value, "_request_id": "req_x"}


@pytest.mark.parametrize("operation,method,path,raw,request_body", CASES)
@pytest.mark.parametrize("fault", ["unknown", "missing", "type", "null"])
def test_native_envelope_rejects_invalid_data(operation, method, path, raw, request_body, fault):
    value = native_fixture(method, path, copy.deepcopy(raw), request_body)
    required = native_contract(operation)["required"][0]
    if fault == "unknown":
        value["uncontracted"] = 1
    elif fault == "missing":
        del value[required]
    elif fault == "type":
        value[required] = []
    else:
        value[required] = None
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_native_response(operation, value, path=path, request=request_body)
    session = FakeSession(FakeResponse(payload=value))
    with pytest.raises(OpenAIError):
        transport(session).request(operation_spec(method, path), method, path, json_body=request_body)
    assert len(session.calls) == 1


@pytest.mark.parametrize("operation,method,path,raw,request_body", [case for case in CASES if "id" in case[3] and case[0] not in {"upload_file", "create_vector_store", "create_batch"}])
def test_native_envelope_rejects_foreign_identity(operation, method, path, raw, request_body):
    value = native_fixture(method, path, copy.deepcopy(raw), request_body)
    value["id"] = "foreign_x"
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_native_response(operation, value, path=path, request=request_body)


@pytest.mark.parametrize("field,value", [
    ("input_file_id", "foreign_x"), ("endpoint", "/v1/images/generations"), ("completion_window", "other"),
])
def test_batch_acknowledgement_preserves_request_semantics(field, value):
    request_body = CASES[12][4]
    response = native_fixture("POST", "/batches", {"id": "batch_x"}, request_body)
    response[field] = value
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_native_response("create_batch", response, path="/batches", request=request_body)


def test_nested_counts_metadata_and_membership_are_exact():
    value = native_fixture("GET", "/vector_stores/vs_x", {"id": "vs_x"})
    value["file_counts"]["total"] = 2
    with pytest.raises(ValueError, match="součet"):
        validate_native_response("retrieve_vector_store", value, path="/vector_stores/vs_x")
    value["file_counts"]["total"] = 0
    value["metadata"] = {"key": {"nested": "untyped"}}
    with pytest.raises(jsonschema.ValidationError):
        validate_native_response("retrieve_vector_store", value, path="/vector_stores/vs_x")
    value["metadata"] = {"x" * 65: "value"}
    with pytest.raises(jsonschema.ValidationError):
        validate_native_response("retrieve_vector_store", value, path="/vector_stores/vs_x")
    member = native_fixture("GET", "/vector_stores/vs_x/files/file_x", {"id": "file_x"})
    member["vector_store_id"] = "vs_foreign"
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        validate_native_response("retrieve_vector_store_file", member, path="/vector_stores/vs_x/files/file_x")


@pytest.mark.parametrize("mask", [{}, True, {"type": "object"}, {"type": "array"},
    {"enum": ["untyped"]}, {"const": {"untyped": "object"}},
    {"type": "array", "items": {"type": "string"}, "contains": {}},
    {"type": "object", "properties": {}, "additionalProperties": True},
    {"$ref": "#/$defs/missing"}, {"anyOf": [{"type": "string"}, {}]},
    {"$ref": "#/$defs/row/properties", "$defs": {"row": {"type": "object", "properties": {"field": {"type": "string"}}, "required": ["field"], "additionalProperties": False}}},
    {"$ref": "#/$defs/alias", "$defs": {"alias": {"anyOf": [{"$ref": "#/$defs/alias"}, {"type": "null"}]}}}])
def test_native_schema_rejects_generic_masks_and_broken_references(mask):
    with pytest.raises((ValueError, jsonschema.SchemaError)):
        validate_native_schema(mask)


def test_malformed_mutation_is_unknown_and_cannot_be_retried():
    session = FakeSession(FakeResponse(payload={"id": "file_x"}))
    with pytest.raises(SubmissionOutcomeUnknown):
        transport(session).request(operation_spec("POST", "/files"), "POST", "/files", json_body={"purpose": "user_data"})
    assert len(session.calls) == 1


def test_json_endpoint_rejects_binary_and_file_download_preserves_bytes():
    response = FakeResponse(headers={"content-type": "application/octet-stream"})
    response.content = b"\xff\x00\r\n"
    with pytest.raises(OpenAIError):
        transport(FakeSession(response)).request(operation_spec("GET", "/models"), "GET", "/models")
    assert transport(FakeSession(response)).request(operation_spec("GET", "/files/file_x/content"), "GET", "/files/file_x/content") == response.content


def test_schema_compilation_error_prevents_dispatch(monkeypatch):
    monkeypatch.setattr("kajovo.core.openai_transport.native_contract", lambda _: (_ for _ in ()).throw(ValueError("broken schema")))
    session = FakeSession()
    with pytest.raises(OpenAIError) as caught:
        transport(session).request(operation_spec("GET", "/models"), "GET", "/models")
    assert caught.value.request_sent is False
    assert session.calls == []


def test_known_attribute_keys_and_identity_are_bound_before_dispatch():
    request = {"attributes": {"category": "text", "version": 2, "ready": True}}
    schema = native_contract("update_vector_store_file", request=request, path="/vector_stores/vs_x/files/file_x")
    attributes = schema["properties"]["attributes"]
    assert attributes["required"] == ["category", "version", "ready"]
    assert attributes["additionalProperties"] is False
    assert attributes["properties"]["version"] == {"type": "integer", "const": 2}
    assert schema["properties"]["id"]["const"] == "file_x"
    assert schema["properties"]["vector_store_id"]["const"] == "vs_x"
    with pytest.raises(ValueError):
        native_contract("update_vector_store_file", request={"attributes": {"x": []}}, path="/vector_stores/vs_x/files/file_x")


def test_known_metadata_keys_are_compiled_and_checked_before_dispatch():
    request = {"name": "Test", "metadata": {"project": "local"}}
    schema = native_contract("create_vector_store", request=request)
    assert schema["properties"]["metadata"] == {
        "type": "object", "properties": {"project": {"type": "string", "const": "local"}},
        "required": ["project"], "additionalProperties": False,
    }
    value = native_fixture("POST", "/vector_stores", {"id": "vs_x", "metadata": {"project": "foreign"}}, request)
    with pytest.raises(jsonschema.ValidationError):
        validate_native_response("create_vector_store", value, request=request)
    with pytest.raises(ValueError, match="textové"):
        native_contract("create_vector_store", request={"name": "Test", "metadata": {"project": 1}})


def test_image_count_is_a_compiled_constraint_and_invalid_base64_is_rejected():
    schema = native_contract("create_image", request={"n": 1})
    assert schema["properties"]["data"]["minItems"] == schema["properties"]["data"]["maxItems"] == 1
    with pytest.raises(ValueError, match="base64"):
        validate_native_response("create_image", {"created": 0, "data": [{"b64_json": "!!!"}]}, request={"n": 1})


@pytest.mark.parametrize("fault", ["duplicate", "empty_more", "first", "last", "counts"])
def test_list_contract_preserves_cardinality_order_and_counts(fault):
    value = native_fixture("GET", "/vector_stores", {"data": [{"id": "vs_x"}], "has_more": False})
    if fault == "duplicate":
        value["data"].append(copy.deepcopy(value["data"][0]))
    elif fault == "empty_more":
        value.update(data=[], has_more=True)
    elif fault in {"first", "last"}:
        value[fault + "_id"] = "vs_foreign"
    else:
        value["data"][0]["file_counts"]["total"] = 1
    with pytest.raises(ValueError):
        validate_native_response("list_vector_stores", value, path="/vector_stores")


def test_page_handoff_rejects_a_resource_repeated_on_another_page():
    from unittest.mock import Mock

    from kajovo.core.openai_client import OpenAIClient
    client = object.__new__(OpenAIClient)
    client._req = Mock(side_effect=[
        native_fixture("GET", "/files", {"data": [{"id": "file_a"}], "has_more": True}),
        native_fixture("GET", "/files", {"data": [{"id": "file_a"}, {"id": "file_b"}], "has_more": False}),
    ])
    with pytest.raises(OpenAIError, match="opakuje"):
        client._list_all("/files")
    assert client._req.call_count == 2
