import json
from unittest.mock import Mock, patch

import pytest

from kajovo.core.cascade_contract import (
    CascadeValidationError,
    output_machine_key,
    validate_cascade_definition,
)
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
from kajovo.core.cascade_types import (
    CascadeDecisionOption,
    CascadeDefinition,
    CascadeInput,
    CascadeOutput,
    CascadeStep,
)
from kajovo.core.config import AppSettings
from kajovo.core.run_bundle import LegacyRunAdapter

MODEL = "gpt-5.6-luna"


def _client():
    client = Mock()
    from kajovo.core.context_compiler import content_hash

    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 128,
        "request_hash": content_hash(payload),
    }
    return client


def _text_step(title, *, context="Kontext 1", output_id=None):
    output = CascadeOutput(name=f"{title} výstup", kind="text")
    if output_id:
        output.id = output_id
    return CascadeStep(
        title=title,
        model=MODEL,
        input_text=f"Zpracuj {title}",
        context_id=context,
        deterministic=True,
        outputs=[output],
    )


def _response(response_id, output, value):
    return {
        "id": response_id,
        "status": "completed",
        "model": MODEL,
        "output_text": json.dumps(
            {output_machine_key(output): value},
            ensure_ascii=False,
        ),
        "usage": {"input_tokens": 128, "output_tokens": 32},
    }


def _worker(definition, tmp_path):
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    return CascadeRunExecutor(
        CascadeRunConfig("test", definition, "", str(tmp_path / "OUT")),
        settings,
        "test",
    )


def _run_dir(tmp_path):
    return next(path for path in (tmp_path / "LOG").iterdir() if path.is_dir())


def test_forward_decision_target_is_allowed_in_draft_but_not_at_run():
    decision = CascadeOutput(
        name="Verdikt",
        kind="decision",
        decision_options=[
            CascadeDecisionOption(value="A", target_step_number=4),
            CascadeDecisionOption(value="B", target_step_number=2),
        ],
    )
    definition = CascadeDefinition(
        "test",
        steps=[
            CascadeStep(
                title="Rozhodnutí",
                model=MODEL,
                input_text="Vyber A nebo B",
                deterministic=True,
                outputs=[decision],
            ),
            _text_step("Druhý"),
        ],
    )
    warnings = validate_cascade_definition(definition, strict=False)
    assert warnings
    with pytest.raises(CascadeValidationError):
        validate_cascade_definition(definition, strict=True)


def test_same_context_does_not_imply_conversation_dependency(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    definition = CascadeDefinition("test", steps=[first, second])
    client = _client()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    assert not errors
    assert results
    first_payload = client.create_response.call_args_list[0].args[0]
    second_payload = client.create_response.call_args_list[1].args[0]
    assert "previous_response_id" not in first_payload
    assert "previous_response_id" not in second_payload


def test_explicit_conversation_dependency_uses_previous_response_id(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    second.use_conversation_context = True
    definition = CascadeDefinition("test", steps=[first, second])
    client = _client()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    assert not errors
    assert results
    second_payload = client.create_response.call_args_list[1].args[0]
    assert second_payload["previous_response_id"] == "resp_1"


def test_new_cascade_records_steps_and_explicit_safe_checkpoints(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    definition = CascadeDefinition("evidence", steps=[first, second])
    client = _client()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
    run_dir = _run_dir(tmp_path)
    adapter = LegacyRunAdapter(run_dir)
    assert [(row["stage"], row["status"]) for row in adapter.steps()] == [
        (first.id, "completed"), (second.id, "completed")
    ]
    checkpoints = adapter.checkpoints()
    assert checkpoints[0]["checkpoint_type"] == "cascade_input_ready"
    assert sum(row["checkpoint_type"] == "cascade_step_completed" for row in checkpoints) == 2
    for checkpoint in checkpoints:
        assert adapter.bundle.validate_checkpoint(checkpoint["checkpoint_id"])
    assert all(row.get("step_id") for row in adapter.requests() if row.get("request_record_id"))


def test_cascade_safe_checkpoint_archives_required_local_input(tmp_path):
    local_input = tmp_path / "source.txt"
    local_input.write_text("kanonický vstup", encoding="utf-8")
    step = _text_step("Lokální vstup")
    step.inputs.append(CascadeInput(name="Zdroj", source="local_file", value=str(local_input)))
    definition = CascadeDefinition("evidence", steps=[step])
    client = _client()
    client.upload_file.return_value = {"id": "file_uploaded"}
    client.create_response.return_value = _response("resp_1", step.outputs[0], "A")
    worker = _worker(definition, tmp_path)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
    adapter = LegacyRunAdapter(_run_dir(tmp_path))
    inputs = [row for row in adapter.artifacts() if row.get("kind") == "cascade_input"]
    assert len(inputs) == 1
    checkpoint = adapter.checkpoints()[0]
    assert checkpoint["safe_to_continue"] is True
    assert inputs[0]["artifact_id"] in checkpoint["required_artifact_ids"]
    assert adapter.bundle.validate_checkpoint(checkpoint["checkpoint_id"])


def test_cascade_repair_instruction_is_only_in_new_step_request(tmp_path):
    step = _text_step("Oprava")
    definition = CascadeDefinition("repair", steps=[step])
    client = _client()
    client.create_response.return_value = _response("resp_new", step.outputs[0], "hotovo")
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    worker = CascadeRunExecutor(
        CascadeRunConfig("test", definition, "", str(tmp_path / "OUT"), recovery_instruction="Oprav citaci."),
        settings,
        "test",
    )
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
    payload = client.create_response.call_args.args[0]
    assert "Oprav citaci." in str(payload["input"])


def test_new_context_does_not_inherit_previous_response_id(tmp_path):
    first = _text_step("První", context="A")
    second = _text_step("Druhý", context="B")
    definition = CascadeDefinition("test", steps=[first, second])
    client = _client()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    assert not errors
    second_payload = client.create_response.call_args_list[1].args[0]
    assert "previous_response_id" not in second_payload


def test_decision_routes_to_future_step_and_skips_other_branch(tmp_path):
    decision = CascadeOutput(
        name="Kvalita",
        kind="decision",
        decision_options=[
            CascadeDecisionOption(value="dobrý", target_step_number=3),
            CascadeDecisionOption(value="špatný", target_step_number=2),
        ],
    )
    first = CascadeStep(
        title="Rozhodni",
        model=MODEL,
        input_text="Je program dobrý nebo špatný?",
        deterministic=True,
        outputs=[decision],
    )
    second = _text_step("Větev špatný")
    third = _text_step("Větev dobrý")
    definition = CascadeDefinition("test", steps=[first, second, third])
    validate_cascade_definition(definition, strict=True)

    client = _client()
    client.create_response.side_effect = [
        _response("resp_1", decision, "dobrý"),
        _response("resp_3", third.outputs[0], "hotovo"),
    ]
    worker = _worker(definition, tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    assert not errors
    assert client.create_response.call_count == 2
    assert third.id in results[0]["executed_step_ids"]
    assert second.id not in results[0]["executed_step_ids"]


def test_step_contract_failure_retries_exactly_three_times(tmp_path):
    step = _text_step("Retry")
    definition = CascadeDefinition("test", steps=[step])
    client = _client()
    client.create_response.side_effect = [
        {"id": f"resp_{index}", "status": "completed", "output_text": '{"wrong":"value"}'}
        for index in range(1, 4)
    ]
    worker = _worker(definition, tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    assert errors
    assert client.create_response.call_count == 3
    assert len(errors[0].splitlines()) == 1


def test_output_input_reference_must_exist_and_be_previous():
    first = _text_step("První")
    second = _text_step("Druhý")
    second.inputs = [
        CascadeInput(
            name="Předchozí",
            source="output",
            source_step_id=first.id,
            source_output_id="missing",
        )
    ]
    with pytest.raises(CascadeValidationError):
        validate_cascade_definition(CascadeDefinition("test", steps=[first, second]), strict=True)


def test_resume_from_step_reuses_only_previous_context_lineage(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    second.use_conversation_context = True
    definition = CascadeDefinition("resume-test", steps=[first, second])

    first_client = _client()
    first_client.create_response.side_effect = [
        _response("resp_old_1", first.outputs[0], "A"),
        _response("resp_old_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=first_client):
        worker.execute()
    assert not errors

    resumed = CascadeDefinition.from_dict(definition.to_dict())
    resumed.run_from_step_id = second.id
    second_client = _client()
    second_client.create_response.return_value = _response(
        "resp_new_2", resumed.steps[1].outputs[0], "B2"
    )
    resumed_worker = _worker(resumed, tmp_path)
    resumed_errors = []
    resumed_worker.finished_err.connect(resumed_errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=second_client):
        resumed_worker.execute()

    assert not resumed_errors
    assert second_client.create_response.call_count == 1
    payload = second_client.create_response.call_args.args[0]
    assert payload["previous_response_id"] == "resp_old_1"


def test_resume_is_blocked_when_a_previous_step_changed(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    second.use_conversation_context = True
    definition = CascadeDefinition("resume-signature", steps=[first, second])

    client = _client()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    changed = CascadeDefinition.from_dict(definition.to_dict())
    changed.steps[0].input_text = "Změněné zadání prvního kroku"
    changed.run_from_step_id = changed.steps[1].id
    blocked_client = _client()
    blocked_worker = _worker(changed, tmp_path)
    errors = []
    blocked_worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=blocked_client):
        blocked_worker.execute()

    assert errors
    blocked_client.create_response.assert_not_called()



def test_cascade_provider_operation_records_identity_and_raw_usage(tmp_path):
    step = _text_step("Provider operation")
    definition = CascadeDefinition("provider-operation", steps=[step])
    client = _client()
    usage = {"input_tokens": 10, "output_tokens": 5}
    client.create_response.return_value = {
        **_response("resp_provider_operation", step.outputs[0], "hotovo"),
        "model": MODEL,
        "usage": usage,
    }
    worker = _worker(definition, tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()

    assert errors == []
    assert results
    from kajovo.core.orchestration.repository import OrchestrationRepository

    repo = OrchestrationRepository(tmp_path / "LOG" / "orchestration.sqlite3")
    with repo.connect() as db:
        work = db.execute(
            "SELECT task_id,route,attempt_no FROM work_orders"
        ).fetchall()
        operations = db.execute(
            "SELECT state,provider_id FROM provider_operations"
        ).fetchall()
        recorded_usage = db.execute(
            "SELECT provider,provider_item_id,usage_json FROM usage_records"
        ).fetchall()
    assert work == [(step.id, "responses_live", 1)]
    assert operations == [("completed", "resp_provider_operation")]
    assert recorded_usage == [
        ("openai", "resp_provider_operation", json.dumps(
            usage, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ))
    ]
