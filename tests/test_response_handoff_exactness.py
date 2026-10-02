"""Sémantické předávky včetně všech větví, typů a obnovy uložených návrhů."""
from __future__ import annotations

import copy
import itertools
from unittest.mock import Mock, patch

import pytest

from kajovo.core.cascade_contract import (
    CascadeValidationError, _dominators, _graph, validate_cascade_definition,
    validate_output_binding,
)
from kajovo.core.cascade_types import (
    CascadeDecisionOption, CascadeDefinition, CascadeInput, CascadeOutput, CascadeStep,
)
from kajovo.core.contracts import ContractError
from kajovo.core.generate_batch import encode_requests
from kajovo.core.structured_output import obj
from test_cascade_v2 import _text_step, _worker
from test_generate_batch import manifest
from test_qfile_semantics import _client as qfile_client, _plan
from test_workflows import make_worker
from change_v2_fixtures import run


def test_dominators_match_every_path_of_all_forward_graphs_with_five_steps():
    # 15 × 7 × 3 × 1 = 315 grafů; každý neprázdný výběr následníků.
    choices = []
    for source in range(4):
        targets = list(range(source + 1, 5))
        choices.append([subset for count in range(1, len(targets) + 1) for subset in itertools.combinations(targets, count)])
    for graph in itertools.product(*choices):
        steps = [_text_step(str(index)) for index in range(5)]
        for source, targets in enumerate(graph):
            if targets == (source + 1,):
                continue
            targets = targets if len(targets) > 1 else targets * 2
            steps[source].outputs = [CascadeOutput(kind="decision", name="Volba", decision_options=[
                CascadeDecisionOption(value=str(index), target_step_id=steps[target].id)
                for index, target in enumerate(targets)
            ])]
        definition = CascadeDefinition("Větvení", steps=steps)
        edges = _graph(definition)
        paths = {index: [] for index in range(5)}
        def visit(node, path, paths=paths, edges=edges):
            path = path | {node}
            paths[node].append(path)
            for target in edges[node]:
                visit(target, path)
        visit(0, set())
        expected = {node: set.intersection(*items) if items else set() for node, items in paths.items()}
        assert _dominators(definition) == expected


@pytest.mark.parametrize("source_kind,reference_kind", list(itertools.product(("text", "json"), ("text", "json", "response_id"))))
@pytest.mark.parametrize("field", ("input_text", "instructions", "previous_response_id_expr", "input_content_json", "files_existing_ids", "files_local_paths"))
def test_legacy_reference_matches_source_response_type_in_every_input_field(source_kind, reference_kind, field):
    first = CascadeStep(model="gpt-5.6-luna", input_text="První", output_type=source_kind,
                        output_schema_kind="custom" if source_kind == "json" else None,
                        output_schema_custom=obj({"value": {"type": "string"}}) if source_kind == "json" else None)
    second = CascadeStep(model="gpt-5.6-luna", input_text="Druhý")
    expression = '{{step.1.' + reference_kind + '}}'
    setattr(second, field, [{"type": "input_text", "text": expression}] if field == "input_content_json" else [expression] if field in {"files_existing_ids", "files_local_paths"} else expression)
    definition = CascadeDefinition("Typová předávka", steps=[first, second])
    compatible = reference_kind in {source_kind, "response_id"}
    if field == "previous_response_id_expr":
        compatible = reference_kind == "response_id"
    elif field in {"files_existing_ids", "files_local_paths"}:
        compatible = False
    if compatible:
        validate_cascade_definition(definition)
    else:
        with pytest.raises(CascadeValidationError, match="neposkytuje|vyžaduje"):
            validate_cascade_definition(definition)


def test_legacy_reference_cannot_consume_skipped_branch_output():
    first, middle = _text_step("První"), _text_step("Větev")
    last = CascadeStep(model="gpt-5.6-luna", input_text="{{step.2.text}}")
    first.outputs = [CascadeOutput(kind="decision", name="Volba", decision_options=[
        CascadeDecisionOption(value="A", target_step_id=middle.id),
        CascadeDecisionOption(value="B", target_step_id=last.id),
    ])]
    with pytest.raises(CascadeValidationError, match="všech možných cestách"):
        validate_cascade_definition(CascadeDefinition("Větve", steps=[first, middle, last]))


@pytest.mark.parametrize("reference", ["{{step.2.text}}", "{{step.0.text}}", "{{step.8.json}}", "{{step.1.out_file_id:missing.txt}}", "{{step.1.unknown}}", "{{step.one.text}}"])
def test_legacy_invalid_dependency_fails_before_worker_can_submit(tmp_path, reference):
    first = CascadeStep(model="gpt-5.6-luna", input_text="První")
    second = CascadeStep(model="gpt-5.6-luna", input_text=reference)
    worker = _worker(CascadeDefinition("Chybný odkaz", steps=[first, second]), tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)
    client = Mock()
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", client):
        worker.execute()
    assert errors
    client.assert_not_called()


def test_conversation_must_have_provider_on_every_branch():
    first, second, last = [_text_step(name, context="Společný") for name in ("První", "Druhý", "Třetí")]
    first.context_id = "Jiný"
    first.outputs = [CascadeOutput(kind="decision", name="Volba", decision_options=[
        CascadeDecisionOption(value="A", target_step_id=second.id),
        CascadeDecisionOption(value="B", target_step_id=last.id),
    ])]
    last.use_conversation_context = True
    with pytest.raises(CascadeValidationError, match="konverzační dependency"):
        validate_cascade_definition(CascadeDefinition("Kontext", steps=[first, second, last]))
    first.context_id = "Společný"
    validate_cascade_definition(CascadeDefinition("Kontext", steps=[first, second, last]))


@pytest.mark.parametrize("kind,bad", [
    ("text", {"kind": "text", "value": 123}),
    ("json", {"kind": "json", "value": {"count": "1"}}),
    ("json", {"kind": "file", "path": "x.txt"}),
    ("decision", {"kind": "decision", "value": "missing"}),
    ("file", {"kind": "file", "path": "x.txt", "file_id": "file_1", "file_type": "png"}),
])
def test_restored_typed_value_must_match_original_response_schema(tmp_path, kind, bad):
    output = CascadeOutput(name="Zdroj", kind=kind, file_type="txt", file_name="x.txt",
                           json_schema=obj({"count": {"type": "integer"}}),
                           decision_options=[CascadeDecisionOption(value="A"), CascadeDecisionOption(value="B")])
    with pytest.raises(CascadeValidationError):
        validate_output_binding(output, bad)
    first, second = _text_step("První"), _text_step("Druhý")
    first.outputs = [output]
    second.inputs = [CascadeInput(name="Návaznost", source="output", source_step_id=first.id, source_output_id=output.id)]
    worker = _worker(CascadeDefinition("Obnova", steps=[first, second]), tmp_path)
    with pytest.raises(CascadeValidationError):
        worker._resolve_deterministic_inputs(step=second, idx=2, values={first.id + "|" + output.id: bad}, client=Mock())


@pytest.mark.parametrize("archived", (False, True))
def test_batch_content_type_cannot_be_changed_even_with_consistent_order_hashes(archived):
    from dataclasses import replace
    from kajovo.core.generate_batch import digest
    from kajovo.core.orchestration.work_order import response_payload_hash, work_order_from_mapping
    value = manifest()
    row = value["requests"][0]
    wire = row["body"]["text"]["format"]["schema"]
    wire["properties"]["content"] = {"type": "integer"}
    old = work_order_from_mapping(value["work_orders"][row["custom_id"]])
    changed = replace(old, schema_hash=digest(wire), request_payload_hash=response_payload_hash(row["body"]))
    value["work_orders"][row["custom_id"]] = {**changed.to_dict(), "order_hash": changed.order_hash}
    with pytest.raises(ContractError, match="FILE_CONTENT_V1"):
        encode_requests(value, archived=archived)


@pytest.mark.parametrize("field,bad", [("allow_empty", "false"), ("acceptance", ["x"]), ("purpose", 1), ("extra", "x")])
def test_restored_qfile_plan_cannot_bypass_original_response_types(tmp_path, field, bad):
    worker = make_worker(tmp_path, "QFILE")
    worker.cfg.qfile_output_path = "navrh.md"
    worker.cfg.qfile_output_format = "md"
    worker.cfg.qfile_plan = copy.deepcopy(_plan()["result"]["data"])
    worker.cfg.qfile_plan[field] = bad
    client = qfile_client()
    results, errors = run(worker, client)
    assert not results and errors
    client.create_response.assert_not_called()


@pytest.mark.parametrize("kind", ["text", "custom", "automatic", "manifest", "file"])
def test_legacy_response_is_actually_available_to_typed_next_step(tmp_path, kind):
    import json
    from test_cascade_v2 import _client, _response
    original = obj({"count": {"type": "integer"}})
    first = CascadeStep(model="gpt-5.6-luna", input_text="Podklad", output_type="text" if kind == "text" else "json",
                        output_schema_kind="custom" if kind == "custom" else "manifest" if kind in {"manifest", "file"} else None,
                        output_schema_custom=original if kind == "custom" else None,
                        expected_out_files=["input.txt"] if kind == "file" else [])
    first.ensure_outputs()
    second = _text_step("Navazující krok")
    second.inputs = [CascadeInput(name="Podklad", source="output", source_step_id=first.id, source_output_id=first.outputs[0].id)]
    value = {"text": "Zdrojový text"} if kind == "text" else {"files": [{"path": "input.txt", "content": "Obsah", "encoding": "utf-8"}]} if kind in {"file", "manifest"} else {"count": 2}
    response = {"id": "resp_legacy", "status": "completed", "output_text": json.dumps(value)}
    responses = [response, _response("resp_next", second.outputs[0], "Hotovo")]
    if kind == "automatic":
        responses.insert(0, {"id": "resp_schema", "status": "completed", "output_text": json.dumps({"schema": {"kind": "object", "nullable": False, "fields": [{"name": "count", "schema": {"kind": "integer", "nullable": False}}]}})})
    client = _client()
    client.create_response.side_effect = responses
    client.upload_file.return_value = {"id": "file_legacy"}
    worker = _worker(CascadeDefinition("Smíšená kaskáda", steps=[first, second]), tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
    assert not errors
    assert results
    payload = client.create_response.call_args_list[-1].args[0]
    content = payload["input"][0]["content"]
    if kind == "file":
        assert {"type": "input_file", "file_id": "file_legacy"} in content
    else:
        expected = "Zdrojový text" if kind == "text" else '"count": 2' if kind in {"custom", "automatic"} else '"files"'
        assert expected in content[0]["text"]


@pytest.mark.parametrize("legacy", [False, True])
def test_cached_primary_cannot_substitute_another_valid_but_incompatible_mask(tmp_path, legacy):
    from kajovo.core.structured_output import response_format, text_format
    first = CascadeStep(model="gpt-5.6-luna", input_text="Podklad") if legacy else _text_step("První")
    worker = _worker(CascadeDefinition("Obnova", steps=[first]), tmp_path)
    payload = {"text": response_format("DIFFERENT_VALID_MASK", obj({"count": {"type": "integer"}}))}
    with pytest.raises(CascadeValidationError, match="mask"):
        worker._validate_primary_contract(first, 1, payload, {} if legacy else worker._schema_for_step(first))
    if legacy:
        worker._validate_primary_contract(first, 1, {"text": text_format()}, text_format()["format"]["schema"])


def test_legacy_numbered_reference_cannot_select_ambiguous_typed_response():
    first = _text_step("První")
    first.outputs.append(CascadeOutput(kind="text", name="Další text"))
    second = CascadeStep(model="gpt-5.6-luna", input_text="{{step.1.text}}")
    with pytest.raises(CascadeValidationError, match="neposkytuje"):
        validate_cascade_definition(CascadeDefinition("Nejednoznačná návaznost", steps=[first, second]))


def test_restored_numbered_json_is_checked_against_source_mask(tmp_path):
    first = CascadeStep(model="gpt-5.6-luna", input_text="Podklad", output_type="json", output_schema_kind="custom",
                        output_schema_custom=obj({"count": {"type": "integer"}}))
    first.ensure_outputs()
    worker = _worker(CascadeDefinition("Obnova", steps=[first]), tmp_path)
    with pytest.raises(CascadeValidationError, match="JSON masku"):
        worker._resolve_text("{{step.1.json}}", {"step.1.json": {"count": "two"}})


@pytest.mark.parametrize("reference", ["{{step.1.out_file_path:x.txt}}", "{{step.1.out_file_id:x.txt}}"])
def test_restored_legacy_file_binding_cannot_coerce_another_type(tmp_path, reference):
    worker = _worker(CascadeDefinition("Obnova", steps=[]), tmp_path)
    with pytest.raises(CascadeValidationError, match="musí být text"):
        worker._resolve_text(reference, {"step.1.out_file_path:x.txt": 1, "step.1.out_file_id:x.txt": 1})


@pytest.mark.parametrize("schema", [
    obj({"count": {"type": "integer"}}),
    obj({"files": {"type": "string"}}),
    obj({"files": {"type": "array", "items": {"type": "string"}}}),
    obj({"files": {"type": "array", "items": obj({"path": {"type": "string"}, "content": {"type": "integer"}})}}),
    obj({"files": {"type": "array", "items": obj({"path": {"type": "string", "enum": ["other.txt"]}, "content": {"type": "string"}})}}),
    obj({"files": {"type": "array", "items": obj({"path": {"type": "string"}, "content": {"type": "string"}, "encoding": {"type": "string"}})}}),
])
def test_legacy_manifest_producer_must_be_compatible_before_any_provider_call(tmp_path, schema):
    step = CascadeStep(model="gpt-5.6-luna", input_text="Soubor", output_type="json", output_schema_kind="custom",
                       output_schema_custom=schema, expected_out_files=["x.txt"])
    worker = _worker(CascadeDefinition("Nekompatibilní manifest", steps=[step]), tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)
    client = Mock()
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", client):
        worker.execute()
    assert errors
    client.assert_not_called()


def test_legacy_text_cannot_promise_file_response():
    step = CascadeStep(model="gpt-5.6-luna", input_text="Soubor", expected_out_files=["x.txt"])
    with pytest.raises(CascadeValidationError, match="JSON manifest"):
        validate_cascade_definition(CascadeDefinition("Text není manifest", steps=[step]))


def test_legacy_automatic_file_response_uses_exact_manifest_schema():
    from kajovo.core.cascade_schemas import legacy_schema_for_step
    original, wire = legacy_schema_for_step(CascadeStep(output_type="json", expected_out_files=["x.txt"]))
    assert original["properties"]["files"]["items"]["properties"]["content"] == {"type": "string"}
    assert wire["properties"]["files"]["items"]["properties"]["encoding"]["enum"] == ["utf-8", "base64"]


def test_legacy_manifest_local_references_and_optional_encoding_are_compatible():
    from kajovo.core.cascade_schemas import legacy_schema_for_step
    schema = obj({"files": {"type": "array", "items": {"$ref": "#/$defs/file"}}})
    schema["$defs"] = {"file": {
        "type": "object", "additionalProperties": False, "required": ["path", "content"],
        "properties": {"path": {"$ref": "#/$defs/path"}, "content": {"type": "string"},
                       "encoding": {"type": "string", "enum": ["utf-8", "base64"]}},
    }, "path": {"type": "string", "enum": ["x.txt"]}}
    legacy_schema_for_step(CascadeStep(output_type="json", output_schema_kind="custom", output_schema_custom=schema, expected_out_files=["x.txt"]))


@pytest.mark.parametrize("encoding", [None, "", False, 0, [], {}, "latin-1"])
def test_manifest_encoding_is_never_silently_defaulted(encoding):
    from kajovo.core.cascade_pipeline import CascadeRunExecutor
    with pytest.raises(ContractError, match="kódování"):
        CascadeRunExecutor._decode_file_content({"content": "Obsah", "encoding": encoding})
    assert CascadeRunExecutor._decode_file_content({"content": "Obsah"}) == "Obsah".encode("utf-8")


def test_manifest_original_nullable_encoding_cannot_be_handed_to_file_writer():
    from kajovo.core.cascade_schemas import legacy_schema_for_step
    schema = obj({"files": {"type": "array", "items": obj({
        "path": {"type": "string"}, "content": {"type": "string"},
        "encoding": {"anyOf": [{"type": "string", "enum": ["utf-8", "base64"]}, {"type": "null"}]},
    })}})
    with pytest.raises(ValueError, match="kódování"):
        legacy_schema_for_step(CascadeStep(output_type="json", output_schema_kind="custom", output_schema_custom=schema, expected_out_files=["x.txt"]))


def test_restored_legacy_text_cannot_use_an_empty_schema_placeholder(tmp_path):
    from kajovo.core.structured_output import text_format
    step = CascadeStep(model="gpt-5.6-luna", input_text="Text", output_type="text")
    step.ensure_outputs()
    worker = _worker(CascadeDefinition("Přesná textová obnova", steps=[step]), tmp_path)
    payload = {"model": step.model, "input": step.input_text, "text": text_format()}
    with pytest.raises(CascadeValidationError, match="textový krok"):
        worker._validate_primary_contract(step, 1, payload, {})
    worker._validate_primary_contract(step, 1, payload, text_format()["format"]["schema"])


@pytest.mark.parametrize("field,reference", list(itertools.product(
    ["files_existing_ids", "files_local_paths", "previous_response_id_expr", "input_content_json"],
    ["response_id", "out_file_id:x.txt", "out_file_path:x.txt"],
)))
def test_legacy_provider_reference_roles_cannot_be_interchanged(field, reference):
    first = CascadeStep(model="gpt-5.6-luna", input_text="Soubor", output_type="json", output_schema_kind="manifest", expected_out_files=["x.txt"])
    second = CascadeStep(model="gpt-5.6-luna", input_text="Navazující")
    value = "{{step.1." + reference + "}}"
    setattr(second, field, [{"type": "input_file", "file_id": value}] if field == "input_content_json" else [value] if field in {"files_existing_ids", "files_local_paths"} else value)
    expected = {"files_existing_ids": "out_file_id:x.txt", "input_content_json": "out_file_id:x.txt", "files_local_paths": "out_file_path:x.txt", "previous_response_id_expr": "response_id"}[field]
    definition = CascadeDefinition("Role předávek", steps=[first, second])
    if reference == expected:
        validate_cascade_definition(definition)
    else:
        with pytest.raises(CascadeValidationError, match="vyžaduje"):
            validate_cascade_definition(definition)
