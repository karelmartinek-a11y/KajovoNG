from __future__ import annotations

import pytest

from kajovo.core.runs.cancellation import CancellationToken, RunCancelled


def test_cancellation_token_starts_active_and_cancels_idempotently():
    token = CancellationToken()
    assert token.is_cancelled() is False
    token.raise_if_cancelled()
    token.cancel()
    token.cancel()
    assert token.is_cancelled() is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY", "QA", "QFILE"])
def test_executor_stop_before_provider_never_submits(tmp_path, mode, monkeypatch):
    """Kooperativní stop standardních workflow skončí před placeným POSTem."""
    import json
    from pathlib import Path
    from unittest.mock import Mock

    from kajovo.core.context_compiler import content_hash
    from test_workflows import make_worker

    worker = make_worker(tmp_path, mode)
    if mode == "MODIFY":
        in_dir = tmp_path / "in"
        in_dir.mkdir()
        worker.cfg.in_dir = str(in_dir)
    if mode == "QFILE":
        worker.cfg.qfile_output_path = "result.txt"
        worker.cfg.qfile_output_format = "txt"

    client = Mock()
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1,
        "request_hash": content_hash(payload),
    }
    monkeypatch.setattr("kajovo.core.runs.executor.OpenAIClient", lambda *args, **kwargs: client)

    errors = []
    results = []
    worker.finished_err.connect(errors.append)
    worker.finished_ok.connect(results.append)
    worker.request_stop()
    worker.run()

    assert results == []
    assert errors == ["STOPPED"]
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "stopped"
    client.create_response.assert_not_called()
    client.create_batch.assert_not_called()
    client.create_image.assert_not_called()
    client.create_image_batch.assert_not_called()
