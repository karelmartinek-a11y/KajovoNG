"""Významové a kořenové hranice JSON kontraktů bez provider volání."""
from copy import deepcopy

import jsonschema
import pytest

from change_v2_fixtures import file_spec, plan_data, spine_data, default_files
from test_preparation_semantic_links import inputs
from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.preparation import (
    PreparationBlocked, validate_file_spec_v1, validate_graph,
    validate_requirements_v2, validate_plan_v2,
)
from kajovo.core.orchestration.projection import Binding, FrozenRegistry, project
from kajovo.core.structured_output import restore_optional_fields
from kajovo.core.cascade_contract import runtime_schema_for_step, output_machine_key, validate_cascade_definition
from kajovo.core.cascade_types import CascadeOutput, CascadeStep, CascadeDefinition


@pytest.mark.parametrize("field", ["product_intent", "statement", "extra"])
def test_requirements_wire_contract_is_rechecked_on_restore(field):
    worker, req = inputs()
    if field == "statement":
        req["requirements"][0][field] = " \t"
    else:
        req[field] = " " if field == "product_intent" else 123
    with pytest.raises(ContractError):
        validate_requirements_v2(worker, req)


def test_duplicate_assumptions_and_unknown_package_component():
    worker, req = inputs()
    assumption = {"id": "A1", "statement": "Předpoklad", "reason": "Důvod", "requirement_ids": []}
    req["assumptions"] = [assumption, deepcopy(assumption)]
    with pytest.raises(ContractError, match="assumptions"):
        validate_requirements_v2(worker, req)
    plan = plan_data()
    plan["packages"] = [{"name": "package", "version_constraint": "*", "registry": "pypi", "reason": "Potřeba", "required_by": ["missing"]}]
    with pytest.raises(ContractError, match="required_by"):
        validate_plan_v2(req, plan)


def test_detail_question_is_clarification_and_facets_resolve():
    worker, req = inputs()
    spine = spine_data("GENERATE", default_files("GENERATE"))
    target = spine["files"][0]
    spec = file_spec(target, [])
    spec["facets"] = [{"kind": "errors", "definition": "Chyby", "obligation_ids": ["missing"]}]
    with pytest.raises(ContractError, match="povinnost"):
        validate_file_spec_v1(worker, target, spine, req, spec)
    spec["facets"] = []
    spec["unresolved_questions"] = [{"question": "Který výsledek chcete?", "blocking": True, "source_refs": []}]
    with pytest.raises(PreparationBlocked) as exc:
        validate_file_spec_v1(worker, target, spine, req, spec)
    assert exc.value.stage == "A2_DETAIL"


def test_graph_parents_are_not_just_well_formed_hashes():
    worker, req = inputs()
    graph = {"contract": "IMPLEMENTATION_GRAPH_V3", "mode": "GENERATE", "source_snapshot_hash": "a" * 64,
             "requirements_hash": "0" * 64, "plan_hash": canonical_sha256(plan_data()),
             "spine": spine_data("GENERATE", default_files("GENERATE")), "file_specs": [], "verification_profile_ids": []}
    with pytest.raises(ContractError, match="rodičovských"):
        validate_graph(worker, graph, req, plan_data())


def test_projection_verifies_content_not_declared_hash():
    claimed = canonical_sha256("old")
    registry = FrozenRegistry({"a": {"value": "new", "sha256": claimed}}, {"target": ("a",)})
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        project("A3", "target", registry, (Binding("a", claimed, "/a", "source", "Důvod"),))


def test_optional_fields_through_refs_anyof_arrays_and_legitimate_null():
    schema = {"type": "object", "properties": {"rows": {"type": "array", "items": {"$ref": "#/$defs/row"}}},
              "$defs": {"row": {"anyOf": [
                  {"type": "object", "properties": {"name": {"type": "string"}, "optional": {"type": "string"}, "nullable": {"type": ["string", "null"]}}, "required": ["name"], "additionalProperties": False},
                  {"type": "string"}]}}}
    value = {"rows": [{"name": "x", "optional": None, "nullable": None}]}
    restored = restore_optional_fields(value, schema)
    assert restored == {"rows": [{"name": "x", "nullable": None}]}
    jsonschema.Draft202012Validator(schema).validate(restored)


def test_optional_anyof_does_not_choose_ambiguous_branch():
    schema = {"anyOf": [
        {"type": "object", "properties": {"x": {"type": "string"}}, "additionalProperties": False},
        {"type": "object", "properties": {"x": {"type": "null"}}, "additionalProperties": False},
    ]}
    with pytest.raises(ContractError, match="jednoznačnou"):
        restore_optional_fields({"x": None}, schema)


def test_cascade_refs_are_namespaced_including_recursive_root():
    outputs = []
    for kind in ("string", "integer"):
        mask = {"type": "object", "properties": {"value": {"$ref": "#/$defs/value"}, "next": {"anyOf": [{"$ref": "#"}, {"type": "null"}]}},
                "required": ["value", "next"], "additionalProperties": False, "$defs": {"value": {"type": kind}}}
        outputs.append(CascadeOutput(name=kind, kind="json", json_schema=mask))
    step = CascadeStep(title="Rekurzivní data", model="gpt-5.6-luna", input_text="Vytvoř objekty", deterministic=True, outputs=outputs)
    validate_cascade_definition(CascadeDefinition(name="Reference", steps=[step]), strict=True)
    schema = runtime_schema_for_step(step)
    value = {output_machine_key(outputs[0]): {"value": "x", "next": {"value": "y", "next": None}},
             output_machine_key(outputs[1]): {"value": 1, "next": None}}
    jsonschema.Draft202012Validator(schema).validate(value)
    assert outputs[0].json_schema["properties"]["next"]["anyOf"][0]["$ref"] == "#"
