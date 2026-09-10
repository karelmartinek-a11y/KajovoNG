from unittest.mock import Mock

import pytest

from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
from kajovo.core.config import AppSettings


def test_schema_cannot_retrieve_external_urls():
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    with pytest.raises(ValueError):
        worker._validate_schema_minimal({"type": "object", "properties": {"x": {"$ref": "https://example.com/schema"}}})


def test_schema_validates_nested_content():
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        worker._validate_json_output({"x": [3]}, {"type": "object", "properties": {"x": {"type": "array", "items": {"type": "string"}}}})


def test_missing_expected_file_does_not_overwrite_output(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("original", encoding="utf-8")
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", str(tmp_path)), AppSettings(), "test")
    worker.logger = Mock()
    with pytest.raises(RuntimeError):
        worker._process_expected_out_files(step=CascadeStep(expected_out_files=["missing.txt"]), idx=1,
            json_output={"files": [{"path": "keep.txt", "content": "replacement"}]}, context={}, client=Mock())
    assert target.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize("path", ["/absolute.txt", "../escape.txt", "C:/escape.txt"])
def test_cascade_rejects_invalid_paths(path):
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    with pytest.raises(ValueError):
        worker._normalize_expected_rel_path(path)


def test_cascade_keeps_independent_definition_and_settings():
    definition = CascadeDefinition("test", steps=[CascadeStep(model="test", input_text="zadání")])
    cfg = CascadeRunConfig("test", definition, "", "out")
    settings = AppSettings()
    worker = CascadeRunWorker(cfg, settings, "test")
    cfg.out_dir = "jiný-výstup"
    definition.steps[0].input_text = "změněno"
    definition.steps.append(CascadeStep(model="other"))
    settings.retry.max_attempts = 1
    assert worker.cfg.out_dir == "out"
    assert len(worker.cfg.cascade.steps) == 1
    assert worker.cfg.cascade.steps[0].input_text == "zadání"
    assert worker.settings.retry.max_attempts == 6
@pytest.mark.parametrize("raw", [
    {"files_existing_ids": "file-abc"}, {"files_local_paths": [None]},
    {"output_type": "unknown"}, {"output_schema_kind": "unknown"},
    {"input_content_json": "bad"}, {"temperature": "bad"},
    {"temperature": float("nan")}, {"temperature": True},
])
def test_invalid_cascade_step_is_not_silently_rewritten(raw):
    from kajovo.core.cascade_types import CascadeStep
    with pytest.raises(ValueError):
        CascadeStep.from_dict(raw)


def test_unresolved_cascade_reference_fails():
    worker = CascadeRunWorker(CascadeRunConfig("test", CascadeDefinition("test"), "", "out"), AppSettings(), "test")
    from kajovo.core.contracts import ContractError
    for expression in ("{{step.1.response_id}}", "{{step.1.json}}", "{{step.1.out_file_id:x.txt}}"):
        with pytest.raises(ContractError):
            worker._resolve_text(expression, {})


def test_cascade_exposes_effective_output_directory():
    definition = CascadeDefinition("test", default_out_dir="default-output")
    worker = CascadeRunWorker(CascadeRunConfig("test", definition, "", ""), AppSettings(), "test")
    assert worker.cfg.out_dir == "default-output"


def test_text_cascade_cannot_complete_with_partial_response(tmp_path):
    from unittest.mock import patch
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-4.1-nano", input_text="test")])
    worker = CascadeRunWorker(CascadeRunConfig("test", definition, "", str(tmp_path / "out")),
                              AppSettings(log_dir=str(tmp_path / "LOG")), "test")
    client = Mock()
    client.create_response.return_value = {"id": "resp_test", "status": "incomplete", "output_text": "partial"}
    errors, results = [], []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert errors and not results


@pytest.mark.parametrize("raw", [{"name": []}, {"default_out_dir": {}}, {"version": True},
                                {"version": 1.5}, {"version": -1}, {"created_at": float("nan")},
                                {"updated_at": float("inf")}, {"created_at": "invalid"},
                                {"steps": [{"model": {"unexpected": "value"}}]},
                                {"steps": [{"previous_response_id_expr": 123}]}])
def test_cascade_metadata_is_not_silently_coerced(raw):
    with pytest.raises(ValueError):
        CascadeDefinition.from_dict(raw)


def test_cascade_timestamp_zero_survives_round_trip():
    definition = CascadeDefinition.from_dict({"name": "test", "created_at": 0, "updated_at": 0})
    assert definition.to_dict()["created_at"] == 0
    assert definition.to_dict()["updated_at"] == 0


def test_text_cascade_unwraps_native_schema(tmp_path):
    from unittest.mock import patch
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-5.2", input_text="Ahoj")])
    worker = CascadeRunWorker(CascadeRunConfig("test", definition, "", str(tmp_path / "out")),
                              AppSettings(log_dir=str(tmp_path / "LOG")), "test")
    client = Mock()
    client.create_response.return_value = {"id": "resp_test", "status": "completed", "output_text": '{"text":"Ahoj"}'}
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors and results[0]["text"] == "Ahoj"
    assert client.create_response.call_args.args[0]["text"]["format"]["strict"] is True


def test_missing_custom_schema_is_prepared_automatically(tmp_path):
    import json
    from unittest.mock import patch
    from kajovo.core.structured_output import obj
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-5.2", input_text="Vrať answer",
        output_type="json", output_schema_kind="custom")])
    worker = CascadeRunWorker(CascadeRunConfig("test", definition, "", str(tmp_path / "out")),
                              AppSettings(log_dir=str(tmp_path / "LOG")), "test")
    client = Mock()
    client.create_response.side_effect = [
        {"id": "resp_schema", "status": "completed", "output_text": json.dumps({"schema_json": json.dumps(obj({"answer": {"type": "string"}}))})},
        {"id": "resp_result", "status": "completed", "output_text": '{"answer":"hotovo"}'}]
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert not errors and results[0]["step_json_outputs"]["1"] == {"answer": "hotovo"}
    assert client.create_response.call_count == 2


def test_invalid_cascade_content_is_rejected_before_local_upload(tmp_path):
    from unittest.mock import patch
    local = tmp_path / "input.txt"
    local.write_text("data", encoding="utf-8")
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-5.2", input_text="test",
        files_local_paths=[str(local)], input_content_json=[{"type": "input_text", "text": 123}])])
    worker = CascadeRunWorker(CascadeRunConfig("test", definition, "", str(tmp_path / "out")),
                              AppSettings(log_dir=str(tmp_path / "LOG")), "test")
    client = Mock()
    errors = []
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
    assert errors
    client.upload_file.assert_not_called()
    client.create_response.assert_not_called()
