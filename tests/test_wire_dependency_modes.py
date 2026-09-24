"""Vazba režimu závislostí je součástí odesílané masky, nejen validátoru."""
from copy import deepcopy

import jsonschema
import pytest

from change_v2_fixtures import default_files, spine_data
from kajovo.core.orchestration.preparation import FORMATS, GRAPH_SCHEMA
from kajovo.core.structured_output import obj, validate_schema


@pytest.mark.parametrize("stage", ["A2_SPINE", "B2_SPINE", "A2Q", "B2Q"])
@pytest.mark.parametrize(
    "dependencies,mode,reason,valid",
    [
        ([], "contract", "", True),
        (["provider.py"], "verified_content", "Potřebuji přesný obsah.", True),
        (["provider.py"], "contract", "", False),
        ([], "verified_content", "Potřebuji přesný obsah.", False),
        (["provider.py"], "verified_content", "", False),
        (["provider.py"], "verified_content", " \t\n", False),
    ],
)
def test_wire_mask_enforces_dependency_mode(stage, dependencies, mode, reason, valid):
    spine = spine_data("GENERATE", default_files("GENERATE"))
    spine["files"][0].update(
        dependencies=dependencies,
        content_dependencies=dependencies,
        dependency_content_mode=mode,
        dependency_content_reason=reason,
    )
    data = (
        {"corrected_spine": spine, "corrected_file_specs": [], "findings": []}
        if stage.endswith("Q") else spine
    )
    schema = FORMATS[stage]["format"]["schema"]
    validate_schema(schema)
    value = {"result": {"status": "ready", "data": data}}
    assert jsonschema.Draft202012Validator(schema).is_valid(value) is valid


def test_legacy_graph_mask_does_not_rewrite_stored_contracts():
    schema = GRAPH_SCHEMA["properties"]["spine"]
    spine = spine_data("GENERATE", default_files("GENERATE"))
    spine["files"][0]["dependency_content_reason"] = "Historický popis kontraktu."
    before = deepcopy(spine)
    jsonschema.Draft202012Validator(schema).validate(spine)
    assert spine == before


@pytest.mark.parametrize("count,valid", [(500, True), (501, False)])
def test_enum_limit_is_shared_by_entire_schema(count, valid):
    schema = obj({
        key: {"type": "string", "enum": [str(index) for index in range(count)]}
        for key in ("first", "second")
    })
    if valid:
        validate_schema(schema)
    else:
        with pytest.raises(ValueError, match="výčet"):
            validate_schema(schema)


@pytest.mark.parametrize("count,valid", [(250, True), (251, False)])
def test_large_enum_string_limit(count, valid):
    schema = obj({"value": {"type": "string", "enum": [
        f"{index:03d}" + "x" * 58 for index in range(count)
    ]}})
    if valid:
        validate_schema(schema)
    else:
        with pytest.raises(ValueError, match="15000"):
            validate_schema(schema)
