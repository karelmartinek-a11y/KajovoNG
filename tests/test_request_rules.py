import itertools
from unittest.mock import Mock

import pytest

from kajovo.core.openai_client import OpenAIClient
from kajovo.core.request_rules import validate_response_payload, validate_vector_attributes


def test_complete_workflow_flag_matrix():
    from scripts.verify_request_matrix import matrix_rows
    rows = list(matrix_rows())
    assert len(rows) == 1024
    assert any(row["allowed"] for row in rows)
    assert any(not row["allowed"] for row in rows)


@pytest.mark.parametrize("tokens", [True, 0, 1, 15, 16.0, "16"])
def test_output_token_minimum_rejected(tokens):
    with pytest.raises(ValueError):
        validate_response_payload({"model": "gpt-4.1-nano", "max_output_tokens": tokens})


def test_output_token_minimum_accepted():
    validate_response_payload({"model": "gpt-4.1-nano", "max_output_tokens": 16})


@pytest.mark.parametrize("field,value", [("reasoning", []), ("reasoning", False), ("reasoning", ""),
                                         ("input", {}), ("input", 0), ("input", False)])
def test_invalid_optional_types(field, value):
    with pytest.raises(ValueError):
        validate_response_payload({"model": "gpt-4.1-nano", field: value})


@pytest.mark.parametrize("model,effort,temperature", list(itertools.product(
    ["gpt-4.1-nano", "gpt-5-nano", "gpt-5.2", "gpt-6-astra"],
    [None, "none", "high"], [None, 0, 0.5, 2, -1, 3, float("nan")]
)))
def test_sampling_matrix(model, effort, temperature):
    payload = {"model": model, "input": "test"}
    if effort is not None:
        payload["reasoning"] = {"effort": effort}
    if temperature is not None:
        payload["temperature"] = temperature
    valid = not (model == "gpt-6-astra" and effort == "none")
    valid = valid and not (model == "gpt-4.1-nano" and effort is not None)
    if temperature is not None:
        valid = valid and temperature in (0, 0.5, 2) and (
            model == "gpt-4.1-nano" or (model == "gpt-5.2" and effort == "none")
        )
    if valid:
        validate_response_payload(payload)
    else:
        with pytest.raises(ValueError):
            validate_response_payload(payload)


@pytest.mark.parametrize("attributes", [{"x": None}, {"x": []}, {"x": float("inf")},
                                       {"x": "a" * 513}, {"a" * 65: 0},
                                       {str(i): i for i in range(17)}])
def test_invalid_attributes_do_not_reach_network(attributes):
    client = OpenAIClient("test")
    client._req = Mock()
    with pytest.raises(ValueError):
        client.update_vector_store_file_attributes("vs_test", "file-test", attributes)
    client._req.assert_not_called()


def test_attribute_boundary():
    validate_vector_attributes({"a" * 64: "b" * 512, "bool": True, "number": 1.5})


def test_invalid_sampling_does_not_reach_sdk():
    client = OpenAIClient("test")
    client._sdk = Mock()
    with pytest.raises(ValueError):
        client.create_response({"model": "gpt-5-nano", "temperature": 0.2, "input": "test"})
    client._sdk.responses.create.assert_not_called()


def test_run_rejects_model_missing_from_current_verified_catalog():
    from types import SimpleNamespace
    from kajovo.core.request_rules import validate_run_options

    cfg = SimpleNamespace(
        mode="QA", model="gpt-5.1-codex", prompt="test", send_as_c=False,
        model_a1="", model_a2="", model_a3="", response_id="",
        attached_vector_store_ids=[], diag_windows_in=False, diag_ssh_in=False,
        diag_windows_out=False, diag_ssh_out=False, model_caps={
            "model": "gpt-5.1-codex", "ok_basic": False,
        }, available_models=["gpt-5.1"],
    )
    with pytest.raises(ValueError, match="aktuálním katalogu"):
        validate_run_options(cfg)


def test_generate_rejects_unprobed_step_model_before_network():
    from types import SimpleNamespace
    from kajovo.core.request_rules import validate_run_options

    cfg = SimpleNamespace(
        mode="GENERATE", model="gpt-5.1", model_a1="gpt-5.1",
        model_a2="gpt-5.1-codex", model_a3="gpt-5.1", prompt="test",
        send_as_c=False, response_id="", attached_vector_store_ids=[],
        diag_windows_in=False, diag_ssh_in=False, diag_windows_out=False,
        diag_ssh_out=False, model_caps={"ok_basic": True},
        caps_by_model={"gpt-5.1": {"ok_basic": True}},
        available_models=["gpt-5.1", "gpt-5.1-codex"],
    )
    with pytest.raises(ValueError, match="úspěšný aktuální probe"):
        validate_run_options(cfg)


@pytest.mark.parametrize("mode,batch,previous,vector,diagnostics", list(itertools.product(
    ["GENERATE", "MODIFY", "QA", "QFILE"], [False, True], [False, True], [False, True], [False, True]
)))
def test_workflow_combination_matrix(mode, batch, previous, vector, diagnostics):
    from types import SimpleNamespace
    from kajovo.core.request_rules import validate_run_options
    cfg = SimpleNamespace(mode=mode, model="gpt-4.1-nano", prompt="test", send_as_c=batch,
                          response_id="resp_test" if previous else "", attached_vector_store_ids=["vs_test"] if vector else [],
                          diag_windows_in=diagnostics, diag_ssh_in=False, diag_windows_out=False, diag_ssh_out=False,
                          model_caps={"ok_basic": True, "supports_file_search": True}, available_models=["gpt-4.1-nano"])
    valid = not batch or mode == "GENERATE" or mode == "MODIFY" and not (previous or vector or diagnostics)
    if valid:
        validate_run_options(cfg)
    else:
        with pytest.raises(ValueError):
            validate_run_options(cfg)


@pytest.mark.parametrize("part", [{"type": "input_text", "text": 4},
                                {"type": "input_file"}, {"type": "input_image", "file_id": "a", "image_url": "b"},
                                {"type": "input_file", "file_data": "data"}, {"type": "unknown"}])
def test_invalid_input_parts_are_rejected(part):
    with pytest.raises(ValueError):
        validate_response_payload({"model": "gpt-4.1-nano", "input": [{"role": "user", "content": [part]}]})
