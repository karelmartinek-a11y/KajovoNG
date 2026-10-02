"""Zákaz obecných masek na odesílací i obnovovací hranici."""
from __future__ import annotations

import copy
from unittest.mock import Mock

import jsonschema
import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.structured_output import (
    compile_schema, obj, prepare_payload, resolve_schema, response_format,
    text_format, validate_output, validate_schema,
)
from tools.verify_response_contracts import response_contract_catalog, schema_nodes

CATALOG = response_contract_catalog()


@pytest.mark.parametrize("name", CATALOG)
def test_every_production_mask_rejects_untyped_node_at_every_position(name):
    schema = CATALOG[name]
    validate_schema(schema)
    for path, _node in schema_nodes(schema):
        bad = copy.deepcopy(schema)
        if not path:
            bad = {}
        else:
            parent = bad
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = {}
        with pytest.raises((ValueError, jsonschema.SchemaError), match="."):
            validate_schema(bad)


@pytest.mark.parametrize("bad", [
    {"type": "object", "required": [], "additionalProperties": False},
    {"type": ["string", "integer", "boolean", "number", "null"]},
    {"type": "array", "items": True},
    {"type": "array", "items": {}},
    {"anyOf": [{"type": "string"}, {}]},
])
def test_general_field_masks_are_forbidden(bad):
    with pytest.raises((ValueError, jsonschema.SchemaError)):
        validate_schema(obj({"value": bad}))


@pytest.mark.parametrize("ref,definitions", [
    ("#/$defs/row/properties", {"row": obj({"value": {"type": "string"}})}),
    ("#/$defs/row/type", {"row": obj({"value": {"type": "string"}})}),
    ("#/$defs/a", {"a": {"$ref": "#/$defs/a"}}),
    ("#/$defs/a", {"a": {"$ref": "#/$defs/b"}, "b": {"$ref": "#/$defs/a"}}),
    ("#/$defs/a", {"a": {"anyOf": [{"$ref": "#/$defs/a"}]}}),
    ("#/$defs/a", {"a": {"anyOf": [{"$ref": "#/$defs/a"}, {"type": "null"}]}}),
])
def test_reference_requires_productive_explicit_schema(ref, definitions):
    schema = obj({"value": {"$ref": ref}})
    schema["$defs"] = definitions
    with pytest.raises(ValueError):
        validate_schema(schema)


def test_nullable_and_recursive_typed_fields_remain_valid():
    schema = obj({"name": {"type": ["string", "null"]}, "next": {"anyOf": [{"$ref": "#"}, {"type": "null"}]}})
    validate_schema(schema)
    jsonschema.Draft202012Validator(schema).validate({"name": None, "next": {"name": "x", "next": None}})


@pytest.mark.parametrize("entry", ["create_response", "_send_response"])
def test_missing_mask_cannot_reach_any_transport_or_resource_lookup(entry):
    client = OpenAIClient("offline")
    client._req = Mock(side_effect=AssertionError("Síť nesmí být použita."))
    client.validate_access = Mock(side_effect=AssertionError("Reference se nesmí načítat."))
    with pytest.raises(ValueError, match="explicitní response"):
        getattr(client, entry)({"model": "gpt-5.4", "input": "test"})
    client._req.assert_not_called()
    client.validate_access.assert_not_called()


def test_prepare_payload_never_changes_the_supplied_contract():
    payload = {"text": text_format()}
    original = copy.deepcopy(payload)
    assert prepare_payload(payload) is payload
    assert payload == original
    with pytest.raises(ValueError):
        prepare_payload({})


def test_saved_response_cannot_be_accepted_under_general_mask():
    payload = {"text": {"format": {"type": "json_schema", "name": "X", "strict": True, "schema": obj({"value": {}})}}}
    with pytest.raises(ContractError, match="uložená response maska"):
        validate_output({"status": "completed", "output_text": '{"value":123}'}, payload)


def test_explicit_open_schema_is_not_silently_replaced_by_model():
    schema = obj({"value": {"type": "string"}})
    schema["additionalProperties"] = True
    client = Mock()
    with pytest.raises(ValueError, match="Otevřený objekt"):
        resolve_schema(client, "gpt-5.4", "test", original=schema)
    client.create_response.assert_not_called()


def test_optional_conversion_preserves_original_schema():
    original = {"type": "object", "properties": {"required": {"type": "integer"}, "optional": {"type": "string"}}, "required": ["required"], "additionalProperties": False}
    before = copy.deepcopy(original)
    wire = compile_schema(original)
    assert original == before
    assert wire["required"] == ["required", "optional"]
    assert wire["properties"]["optional"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}
    response_format("OPTIONAL", wire)
