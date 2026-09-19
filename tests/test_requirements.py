"""Regrese requirements kontraktů, kvality a úplnosti implementačních vazeb."""

from copy import deepcopy
from pathlib import Path
import re

import pytest

from kajovo.core import requirements as subject
from kajovo.core.contracts import ContractError
from kajovo.core.model_registry import model_ids, model_spec, selectable, validate_model_parameters
from kajovo.core.structured_output import validate_schema


def _sample(schema):
    if "enum" in schema:
        return schema["enum"][0]
    if schema["type"] == "object":
        return {key: _sample(value) for key, value in schema["properties"].items()}
    return {"array": [], "string": "Popis", "integer": 1}[schema["type"]]


def _documents(mode):
    req, plan, struct = [
        _sample(factory(mode)["format"]["schema"]) for factory in (
            subject.requirements_format, subject.enriched_plan_format,
            subject.enriched_structure_format,
        )
    ]
    key = "explicit_requirements" if mode == "GENERATE" else "explicit_change_requirements"
    req[key] = [{"id": "R1", "description": "Načíst uložený stav."}]
    plan["architecture_items"] = [
        {"id": "A1", "requirement_ids": ["R1"], "responsibility": "Načtení stavu"}
    ]
    files_key = "files" if mode == "GENERATE" else "touched_files"
    file_schema = subject.enriched_structure_format(mode)["format"]["schema"]["properties"][files_key]["items"]
    file = _sample(file_schema)
    file.update(path="app.py", requirement_ids=["R1"], architecture_item_ids=["A1"])
    struct[files_key] = [file]
    if mode == "MODIFY":
        file["action"] = "modify"
        plan["change_plan"]["files_to_modify"] = [{"path": "app.py", "intent": "Načíst stav"}]
        struct["interfaces"] = [{"id": "I1", "definition": "load() -> dict"}]
        struct["preserved_files"] = [{
            "path": "storage.py", "provides": ["I1"], "requirement_ids": [],
            "architecture_item_ids": [], "behavior": "Poskytuje uložený stav.",
        }]
        file.update(requires=["I1"], dependencies=["storage.py"])
    return req, plan, struct


def test_exact_instructions():
    source = Path(__file__).resolve().parents[1] / "KajovoNG_zadani_zmen_GENERATE_MODIFY.md"
    blocks = re.findall(r"```text\n(.*?)\n```", source.read_text(encoding="utf-8"), re.S)
    stages = (
        "A0R_REQUIREMENTS", "A1_PLAN", "A2_STRUCTURE", "A2Q_QUALITY_GATE", "A3_FILE",
        "B0R_REQUIREMENTS", "B1_PLAN", "B2_STRUCTURE", "B2Q_QUALITY_GATE", "B3_FILE",
    )
    assert len(blocks) == 11
    assert subject.CORE_INSTRUCTIONS == blocks[0]
    for stage, instruction in zip(stages, blocks[1:], strict=True):
        expected = blocks[0] + "\n\n" + instruction
        if stage in ("A3_FILE", "B3_FILE"):
            actual = subject.stage_instructions(stage)
            assert actual.startswith(expected + "\n\n")
            assert "FILE_CONTENT_V1" in actual
            assert "jediným polem content" in actual
            assert "500 řádků" not in actual
        else:
            assert subject.stage_instructions(stage) == expected
    with pytest.raises(ValueError):
        subject.stage_instructions("QA")


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_strict_schemas_and_independent_copies(mode):
    for factory in (subject.requirements_format, subject.enriched_plan_format,
                    subject.enriched_structure_format):
        original = factory(mode)
        assert original["format"]["strict"] is True
        validate_schema(original["format"]["schema"])
        changed = factory(mode)
        changed["format"]["schema"]["properties"].clear()
        assert factory(mode) == original
    assert subject.enriched_structure_format(mode)["format"]["schema"]["properties"]["contract"]["enum"] == [
        "A2_STRUCTURE" if mode == "GENERATE" else "B2_STRUCTURE"
    ]


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_requirements_exact_keys(mode):
    expected = (
        "contract product_intent explicit_requirements implicit_requirements invariants "
        "assumptions user_flows system_flows states_and_lifecycle validation_and_error_behaviour "
        "quality_attributes security_and_privacy persistence_and_consistency "
        "dependencies_and_integrations acceptance_criteria definition_of_done"
        if mode == "GENERATE" else
        "contract requested_change current_behaviour_to_preserve explicit_change_requirements "
        "implicit_change_requirements impacted_flows impacted_states impacted_contracts "
        "validation_and_error_behaviour migration_or_compatibility_concerns "
        "acceptance_criteria definition_of_done"
    )
    props = subject.requirements_format(mode)["format"]["schema"]["properties"]
    assert set(props) == set(expected.split())
    for key, value in props.items():
        if key.endswith("requirements"):
            assert set(value["items"]["properties"]) == {"id", "description"}
        elif key not in ("contract", "product_intent", "requested_change"):
            assert value == {"type": "array", "items": {"type": "string"}}


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_valid_traceability_does_not_mutate(mode):
    documents = _documents(mode)
    before = deepcopy(documents)
    assert subject.validate_traceability(*documents) is None
    assert documents == before


@pytest.mark.parametrize("mutation", [
    lambda r, p, s: r["implicit_change_requirements"].append(
        {"id": "R1", "description": "Duplicitní"}),
    lambda r, p, s: r["explicit_change_requirements"][0].update(id=" "),
    lambda r, p, s: r["explicit_change_requirements"][0].update(description=" "),
    lambda r, p, s: r["implicit_change_requirements"].append(
        {"id": "R2", "description": "Nepokrytý"}),
    lambda r, p, s: p["architecture_items"][0].update(requirement_ids=["neznámý"]),
    lambda r, p, s: p["architecture_items"].append(deepcopy(p["architecture_items"][0])),
    lambda r, p, s: s["touched_files"][0].update(requirement_ids=[]),
    lambda r, p, s: s["touched_files"][0].update(architecture_item_ids=[]),
    lambda r, p, s: s["touched_files"][0].update(architecture_item_ids=["neznámý"]),
    lambda r, p, s: s["touched_files"][0].update(dependencies=[]),
    lambda r, p, s: s["touched_files"][0].update(dependencies=["../storage.py"]),
    lambda r, p, s: s["touched_files"][0].update(requires=["neznámý"]),
    lambda r, p, s: s["preserved_files"][0].update(provides=[]),
    lambda r, p, s: s["preserved_files"][0].update(provides=["neznámý"]),
    lambda r, p, s: s["preserved_files"][0].update(path="APP.py"),
    lambda r, p, s: s["interfaces"][0].update(id=""),
    lambda r, p, s: s["interfaces"][0].update(definition=" "),
    lambda r, p, s: s["interfaces"].append(deepcopy(s["interfaces"][0])),
    lambda r, p, s: s["touched_files"][0].update(action="add"),
    lambda r, p, s: s.update(contract="B2Q_QUALITY_GATE"),
])
def test_invalid_traceability(mutation):
    documents = _documents("MODIFY")
    mutation(*documents)
    with pytest.raises(ContractError):
        subject.validate_traceability(*documents)


@pytest.mark.parametrize("path", ["../x", "/x", "C:/x", "a\\x", "NUL", "a/../x"])
def test_unsafe_generate_paths(path):
    documents = _documents("GENERATE")
    documents[2]["files"][0]["path"] = path
    with pytest.raises(ContractError):
        subject.validate_traceability(*documents)


def test_requirement_cannot_bypass_architecture():
    req, plan, struct = _documents("GENERATE")
    req["implicit_requirements"] = [{"id": "R2", "description": "Uložení"}]
    plan["architecture_items"].append(
        {"id": "A2", "requirement_ids": ["R2"], "responsibility": "Uložení"}
    )
    struct["files"][0]["requirement_ids"].append("R2")
    with pytest.raises(ContractError):
        subject.validate_traceability(req, plan, struct)


def test_standard_is_unchanged():
    payload = {"model": "neznámý", "reasoning": {"effort": "low"}, "temperature": 0.3}
    before = deepcopy(payload)
    assert subject.apply_quality(payload, False) is None
    assert payload == before


@pytest.mark.parametrize("model", [model for model in model_ids() if selectable(model)])
def test_maximum_quality_respects_entire_matrix(model):
    payload = {"model": model, "temperature": 0.3, "top_p": 0.8}
    subject.apply_quality(payload, True)
    spec = model_spec(model)
    order = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
    if spec["reasoning_supported"] and spec["reasoning"]:
        assert payload["reasoning"]["effort"] == max(spec["reasoning"], key=order.index)
    validate_model_parameters(payload)


def test_effort_order_and_sampling(monkeypatch):
    monkeypatch.setattr(subject, "model_spec", lambda _: {
        "reasoning_supported": True, "reasoning": ["max", "low", "xhigh"],
        "sampling": "explicit_none",
    })
    payload = {"model": "test", "reasoning": {"effort": "low", "summary": "auto"},
               "temperature": 1, "top_p": 1}
    subject.apply_quality(payload, True)
    assert payload == {"model": "test", "reasoning": {"effort": "max", "summary": "auto"}}


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        subject.apply_quality({"model": "neznámý"}, True)


@pytest.mark.parametrize("stage", ["A0R", "A1", "A2", "A2Q", "A3",
                                  "B0R", "B1", "B2", "B2Q", "B3"])
def test_short_stage_names(stage):
    suffix = {"0R": "_REQUIREMENTS", "1": "_PLAN", "2": "_STRUCTURE",
              "2Q": "_QUALITY_GATE", "3": "_FILE"}[stage[1:]]
    assert subject.stage_instructions(stage) == subject.stage_instructions(stage + suffix)


def test_ingestion_is_technical():
    instructions = subject.stage_instructions("A0")
    assert instructions.startswith(subject.CORE_INSTRUCTIONS + "\n\n")
    assert "bez shrnutí" in instructions
    assert "Nevytvářej requirements" in instructions
    assert "Potvrď příjem" in instructions


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_snapshot_mode(mode):
    documents = _documents(mode)
    subject.validate_traceability(*documents, mode=mode)
    with pytest.raises(ContractError):
        subject.validate_traceability(*documents, mode="MODIFY" if mode == "GENERATE" else "GENERATE")


def test_modify_no_changes_preserved_coverage():
    req, plan, struct = _documents("MODIFY")
    plan["change_plan"]["files_to_modify"] = []
    struct["touched_files"] = []
    struct["preserved_files"][0].update(requirement_ids=["R1"], architecture_item_ids=["A1"])
    subject.validate_traceability(req, plan, struct)
    struct["preserved_files"][0]["requirement_ids"] = []
    with pytest.raises(ContractError):
        subject.validate_traceability(req, plan, struct)


@pytest.mark.parametrize("field,value", [
    ("requirement_ids", ["neznámý"]), ("architecture_item_ids", ["neznámý"]),
    ("behavior", " "), ("requirement_ids", ["R1", "R1"]),
])
def test_invalid_preserved_coverage(field, value):
    documents = _documents("MODIFY")
    documents[2]["preserved_files"][0][field] = value
    with pytest.raises(ContractError):
        subject.validate_traceability(*documents)


@pytest.mark.parametrize("stage", ["A3", "B3", "A3_FILE", "B3_FILE"])
def test_live_and_batch_require_single_complete_file_without_line_chunking(stage):
    live = subject.stage_instructions(stage)
    batch = subject.stage_instructions(stage, batch=True)
    for instructions in (live, batch):
        assert "500 řádků" not in instructions
        assert "FILE_CONTENT_V1" in instructions
        assert "jediným polem content" in instructions
        assert "chunk_index" not in instructions
        assert "chunk_count" not in instructions
        assert "has_more" not in instructions
        assert instructions.startswith(subject.CORE_INSTRUCTIONS + "\n\n")


def test_batch_does_not_change_preparation_instructions():
    assert subject.stage_instructions("A1", batch=True) == subject.stage_instructions("A1")


@pytest.mark.parametrize("action", ["add", "modify"])
def test_structure_can_extend_plan_with_traced_file(action):
    documents = _documents("MODIFY")
    extra = deepcopy(documents[2]["touched_files"][0])
    extra.update(path="recovery.py", action=action)
    documents[2]["touched_files"].append(extra)
    before = deepcopy(documents)
    subject.validate_traceability(*documents)
    assert documents == before


@pytest.mark.parametrize("field,value", [
    ("requirement_ids", []),
    ("requirement_ids", ["neznámý"]),
    ("architecture_item_ids", []),
    ("architecture_item_ids", ["neznámý"]),
])
def test_structure_extension_requires_valid_traceability(field, value):
    documents = _documents("MODIFY")
    extra = deepcopy(documents[2]["touched_files"][0])
    extra.update(path="recovery.py", action="add")
    extra[field] = value
    documents[2]["touched_files"].append(extra)
    with pytest.raises(ContractError):
        subject.validate_traceability(*documents)


@pytest.mark.parametrize("change", ["remove", "action", "preserve"])
def test_structure_extension_cannot_replace_planned_file(change):
    documents = _documents("MODIFY")
    files = documents[2]["touched_files"]
    extra = deepcopy(files[0])
    extra.update(path="recovery.py", action="add")
    files.append(extra)
    if change == "remove":
        files.pop(0)
    elif change == "action":
        files[0]["action"] = "add"
    else:
        planned = files.pop(0)
        documents[2]["preserved_files"].append({
            key: planned[key] for key in (
                "path", "provides", "requirement_ids", "architecture_item_ids", "behavior"
            )
        })
    with pytest.raises(ContractError, match="soubory a akce plánu B1"):
        subject.validate_traceability(*documents)


def test_extension_cannot_link_unrelated_architecture():
    req, plan, struct = _documents("MODIFY")
    req["implicit_change_requirements"] = [{"id": "R2", "description": "Obnovit stav."}]
    plan["architecture_items"].append({
        "id": "A2", "requirement_ids": ["R2"], "responsibility": "Obnovení stavu",
    })
    extra = deepcopy(struct["touched_files"][0])
    extra.update(path="recovery.py", action="add", requirement_ids=["R2"])
    struct["touched_files"].append(extra)
    with pytest.raises(ContractError, match="vazbu přes uvedenou architekturu"):
        subject.validate_traceability(req, plan, struct)
    extra["architecture_item_ids"] = ["A2"]
    subject.validate_traceability(req, plan, struct)
