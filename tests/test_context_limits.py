from __future__ import annotations

from unittest.mock import Mock

import pytest

from kajovo.core.context_limits import (
    checked_measurement,
    measure_request,
    preparation_measurement,
)
from kajovo.core.contracts import ContractError


def test_checked_measurement_uses_exact_provider_count_when_external_input_exists():
    client = Mock()
    payload = {
        "model": "gpt-4.1",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_file", "file_id": "file_1"}],
            }
        ],
        "max_output_tokens": 1000,
    }
    from kajovo.core.context_compiler import content_hash

    client.count_input_tokens.return_value = {
        "request_hash": content_hash(payload),
        "input_tokens": 1234,
    }
    report = checked_measurement(payload, client)
    assert report["input_tokens"] == 1234
    assert report["input_tokens_exact"] is True
    client.count_input_tokens.assert_called_once()


def test_checked_measurement_blocks_when_exact_count_cannot_be_verified():
    client = Mock()
    client.count_input_tokens.side_effect = RuntimeError("offline")
    payload = {
        "model": "gpt-4.1",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_file", "file_id": "file_1"}],
            }
        ],
        "max_output_tokens": 1000,
    }
    with pytest.raises(ContractError, match="kapacitu vstupu"):
        checked_measurement(payload, client)


def test_preparation_uses_model_capability_for_output_limit():
    client = Mock()
    payload = {"model": "gpt-4.1", "input": "small"}
    report = preparation_measurement(payload, client)
    assert payload["max_output_tokens"] > 0
    assert report["status"] == "measured"


def test_measurement_has_no_economic_decision_fields():
    report = measure_request(
        {"model": "gpt-4.1", "input": "small", "max_output_tokens": 1000},
        exact_input_tokens=10,
    )
    forbidden = {
        "projected_cost",
        "actual_cost",
        "pricing_status",
        "long_context_pricing_threshold",
        "price_snapshot_hash",
    }
    assert forbidden.isdisjoint(report)
