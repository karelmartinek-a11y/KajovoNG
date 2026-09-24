"""Nejasný vzdálený výsledek nesmí vytvořit druhou placenou operaci."""

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
from kajovo.core.config import AppSettings
from kajovo.core.openai_transport import (
    CREATE_RESPONSE,
    OpenAIError,
    OpenAITransport,
    SubmissionOutcomeUnknown,
)


def make_executor(tmp_path, count=1):
    definition = CascadeDefinition("unknown", steps=[
        CascadeStep(model="gpt-5.6-luna", input_text=f"Krok {index}")
        for index in range(count)
    ])
    return CascadeRunExecutor(
        CascadeRunConfig("test", definition, "", str(tmp_path / "out")),
        AppSettings(log_dir=str(tmp_path / "LOG")), "test",
    )


@pytest.mark.parametrize("before", [0, 1], ids=["CAS-UNKNOWN-002", "CAS-UNKNOWN-003"])
def test_unknown_preserves_checkpoint_and_prevents_restart(tmp_path, before):
    executor = make_executor(tmp_path, before + 2)
    client = Mock()
    client.create_response.side_effect = [
        {"id": "resp_done", "status": "completed", "output_text": '{"text":"hotovo"}'}
    ] * before + [SubmissionOutcomeUnknown("create_response", "POST", "/responses")]
    results, events = [], []
    executor.finished_ok.connect(results.append)
    executor.progress_event.connect(events.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        executor.execute()
        assert client.create_response.call_count == before + 1
        state = json.loads(Path(executor.logger.state_path).read_text(encoding="utf-8"))
        assert state["status"] == "submission_unknown"
        assert len(state["cascade_runtime"]["executed_step_ids"]) == before
        assert state["unknown_submission"]["operation"] == "create_response"
        assert not results
        assert events[-1].state == "submission_unknown"
        restarted = make_executor(tmp_path, before + 2)
        restarted.cfg.cascade = executor.cfg.cascade
        restarted.execute()
        assert client.create_response.call_count == before + 1


@pytest.mark.parametrize("error", [
    SubmissionOutcomeUnknown("create_response", "POST", "/responses"),
    OpenAIError("rate limited", status_code=429),
    OpenAIError("server error", status_code=503),
], ids=["CAS-UNKNOWN-001", "CAS-UNKNOWN-006-429", "CAS-UNKNOWN-006-503"])
def test_cascade_never_retries_submit(tmp_path, error):
    executor = make_executor(tmp_path)
    client = Mock()
    client.create_response.side_effect = error
    results = []
    executor.finished_ok.connect(results.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        executor.execute()
    assert client.create_response.call_count == 1
    assert results == []


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("reset")],
                         ids=["CAS-UNKNOWN-004", "CAS-UNKNOWN-005"])
def test_non_idempotent_transport_sends_once(error):
    session = Mock()
    session.request.side_effect = error
    transport = OpenAITransport(base_url="https://invalid.test", api_key="test",
                                timeout_s=1, session=session)
    with pytest.raises(SubmissionOutcomeUnknown):
        transport.request(CREATE_RESPONSE, "POST", "/responses", max_attempts=20)
    assert session.request.call_count == 1
