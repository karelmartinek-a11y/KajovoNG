"""Pevná matice se nesmí změnit podle cache ani podle názvu neznámého modelu."""
import json
from itertools import product

import pytest

from kajovo.core.model_registry import model_spec, model_ids, selectable
from kajovo.core.model_capabilities import ModelCapabilitiesCache
from kajovo.core.request_rules import validate_response_payload


def test_runtime_cache_cannot_override_matrix(tmp_path):
    path = tmp_path / "caps.json"
    path.write_text(json.dumps({"models": {"gpt-5-nano": {"supports_temperature": True}}}))
    cache = ModelCapabilitiesCache(str(path))
    cache.bind("test")
    assert not cache.get("gpt-5-nano").supports_temperature
    assert cache.get("gpt-4.1").supports_temperature
    assert cache.get("gpt-5.2").supports_file_search
    assert cache.get("gpt-5.1-codex").supports_file_search is False


@pytest.mark.parametrize("model", ["gpt-5.2-future", "gpt-4.1-2099-01-01", "ft:gpt-4.1:test", "gpt-unknown"])
def test_unknown_ids_fail_closed(model):
    with pytest.raises(ValueError, match="pevné matici"):
        validate_response_payload({"model": model})


@pytest.mark.parametrize("model", model_ids())
def test_catalogue_covers_live_and_batch(model):
    spec = model_spec(model)
    for batch in (False, True):
        allowed = selectable(model) and (not batch or spec["batch"])
        if allowed:
            validate_response_payload({"model": model}, batch=batch)
        else:
            with pytest.raises(ValueError):
                validate_response_payload({"model": model}, batch=batch)


@pytest.mark.parametrize("model", [m for m in model_ids() if selectable(m)])
def test_all_supported_models_cross_reasoning_sampling_tools_inputs(model):
    spec = model_spec(model)
    for batch, effort, sampling, source, tool in product(
        (False, True), (None, "none", "minimal", "low", "medium", "high", "xhigh", "max"),
        (None, "temperature", "top_p"), (None, "input_file", "input_image"), (False, True)
    ):
        payload = {"model": model}
        if effort: payload["reasoning"] = {"effort": effort}
        if sampling: payload[sampling] = .5
        if source: payload["input"] = [{"role": "user", "content": [{"type": source, "file_id": "file_test"}]}]
        if tool: payload["tools"] = [{"type": "file_search", "vector_store_ids": ["vs_test"]}]
        allowed = (not batch or spec["batch"]) and (effort is None or effort in spec["reasoning"])
        allowed &= not sampling or spec["sampling"] == "always" or spec["sampling"] == "explicit_none" and effort == "none"
        allowed &= not tool or "file_search" in spec["features"]
        allowed &= source != "input_image" or "image_input" in spec["features"]
        allowed &= source != "input_file" or "file_uploads" in spec["features"]
        if allowed:
            validate_response_payload(payload, batch=batch)
        else:
            with pytest.raises(ValueError): validate_response_payload(payload, batch=batch)


@pytest.mark.parametrize("model,option,value,allowed", [
    ("gpt-5.4-pro", "max_output_tokens", 16, False),
    ("gpt-4o-2024-05-13", "max_output_tokens", 16, False),
    ("gpt-5.6-sol", "reasoning", {"effort": "max", "mode": "pro"}, True),
    ("gpt-6-astra", "reasoning", {"effort": "none"}, False),
    ("gpt-5-pro", "reasoning", {"effort": "low"}, False),
    ("gpt-5.1", "reasoning", {"effort": "xhigh"}, False),
    ("o3-mini", "reasoning", {"effort": "low"}, True),
    ("gpt-5.5", "prompt_cache_retention", "in_memory", False),
    ("gpt-5.5", "prompt_cache_retention", "24h", True),
    ("gpt-6-astra", "prompt_cache_retention", "24h", False),
    ("gpt-6-astra", "prompt_cache_options", {"ttl": "30m"}, True),
    ("gpt-4.1", "prompt_cache_retention", "in-memory", False),
    ("gpt-4.1", "max_output_tokens", 32769, False),
    ("gpt-4.1", "max_tool_calls", True, False),
    ("gpt-6-astra", "include", ["message.output_text.logprobs"], False),
])
def test_documented_exceptions_and_parameter_boundaries(model, option, value, allowed):
    payload = {"model": model, option: value}
    if allowed: validate_response_payload(payload)
    else:
        with pytest.raises(ValueError): validate_response_payload(payload)
