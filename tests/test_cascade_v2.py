import json
from unittest.mock import Mock, patch

import pytest

from kajovo.core.cascade_contract import (
    CascadeValidationError,
    output_machine_key,
    validate_cascade_definition,
)
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from kajovo.core.cascade_types import (
    CascadeDecisionOption,
    CascadeDefinition,
    CascadeInput,
    CascadeOutput,
    CascadeStep,
)
from kajovo.core.config import AppSettings


MODEL = "gpt-5.2"


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
        "output_text": json.dumps(
            {output_machine_key(output): value},
            ensure_ascii=False,
        ),
    }


def _worker(definition, tmp_path):
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    return CascadeRunWorker(
        CascadeRunConfig("test", definition, "", str(tmp_path / "OUT")),
        settings,
        "test",
    )


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


def test_same_context_uses_previous_response_id_automatically(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    definition = CascadeDefinition("test", steps=[first, second])
    client = Mock()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()

    assert not errors
    assert results
    first_payload = client.create_response.call_args_list[0].args[0]
    second_payload = client.create_response.call_args_list[1].args[0]
    assert "previous_response_id" not in first_payload
    assert second_payload["previous_response_id"] == "resp_1"


def test_new_context_does_not_inherit_previous_response_id(tmp_path):
    first = _text_step("První", context="A")
    second = _text_step("Druhý", context="B")
    definition = CascadeDefinition("test", steps=[first, second])
    client = Mock()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()

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

    client = Mock()
    client.create_response.side_effect = [
        _response("resp_1", decision, "dobrý"),
        _response("resp_3", third.outputs[0], "hotovo"),
    ]
    worker = _worker(definition, tmp_path)
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()

    assert not errors
    assert client.create_response.call_count == 2
    assert third.id in results[0]["executed_step_ids"]
    assert second.id not in results[0]["executed_step_ids"]


def test_step_contract_failure_retries_exactly_three_times(tmp_path):
    step = _text_step("Retry")
    definition = CascadeDefinition("test", steps=[step])
    client = Mock()
    client.create_response.side_effect = [
        {"id": f"resp_{index}", "status": "completed", "output_text": '{"wrong":"value"}'}
        for index in range(1, 4)
    ]
    worker = _worker(definition, tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)

    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()

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
    definition = CascadeDefinition("resume-test", steps=[first, second])

    first_client = Mock()
    first_client.create_response.side_effect = [
        _response("resp_old_1", first.outputs[0], "A"),
        _response("resp_old_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    errors = []
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=first_client):
        worker.run()
    assert not errors

    resumed = CascadeDefinition.from_dict(definition.to_dict())
    resumed.run_from_step_id = second.id
    second_client = Mock()
    second_client.create_response.return_value = _response(
        "resp_new_2", resumed.steps[1].outputs[0], "B2"
    )
    resumed_worker = _worker(resumed, tmp_path)
    resumed_errors = []
    resumed_worker.finished_err.connect(resumed_errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=second_client):
        resumed_worker.run()

    assert not resumed_errors
    assert second_client.create_response.call_count == 1
    payload = second_client.create_response.call_args.args[0]
    assert payload["previous_response_id"] == "resp_old_1"


def test_resume_is_blocked_when_a_previous_step_changed(tmp_path):
    first = _text_step("První", context="Společný")
    second = _text_step("Druhý", context="Společný")
    definition = CascadeDefinition("resume-signature", steps=[first, second])

    client = Mock()
    client.create_response.side_effect = [
        _response("resp_1", first.outputs[0], "A"),
        _response("resp_2", second.outputs[0], "B"),
    ]
    worker = _worker(definition, tmp_path)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()

    changed = CascadeDefinition.from_dict(definition.to_dict())
    changed.steps[0].input_text = "Změněné zadání prvního kroku"
    changed.run_from_step_id = changed.steps[1].id
    blocked_client = Mock()
    blocked_worker = _worker(changed, tmp_path)
    errors = []
    blocked_worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=blocked_client):
        blocked_worker.run()

    assert errors
    blocked_client.create_response.assert_not_called()
