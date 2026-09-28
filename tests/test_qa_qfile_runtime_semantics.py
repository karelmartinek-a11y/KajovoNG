"""Runtime sémantika QA/QFILE clarifikace a QA konverzační návaznosti."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from change_v2_fixtures import run
from kajovo.core.context_compiler import content_hash
from test_workflows import make_worker, response


def _client():
    client = Mock()
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000,
        "request_hash": content_hash(payload),
    }
    client.retrieve_file.return_value = {
        "id": "file_test",
        "filename": "input.txt",
        "bytes": 100,
    }
    return client


def _blocked():
    return {
        "result": {
            "status": "blocked",
            "questions": [
                {
                    "code": "missing_input",
                    "question": "Který podklad mám použít?",
                    "blocking": True,
                    "source_refs": [],
                }
            ],
        }
    }


@pytest.mark.parametrize("mode", ["QA", "QFILE"])
def test_blocked_runtime_becomes_needs_clarification_without_followup_submit(
    tmp_path, mode
):
    worker = make_worker(tmp_path, mode)
    if mode == "QFILE":
        worker.cfg.qfile_suggest_path = True
        worker.cfg.qfile_output_path = ""
    client = _client()
    client.create_response.return_value = response(0, _blocked())

    results, errors = run(worker, client)

    assert errors == []
    assert results == [
        {
            "mode": mode,
            "status": "needs_clarification",
            "stage": mode,
            "questions": _blocked()["result"]["questions"],
        }
    ]
    assert client.create_response.call_count == 1
    client.create_batch.assert_not_called()


@pytest.mark.parametrize("continue_conversation", [False, True])
def test_qa_previous_response_id_only_when_explicitly_enabled(
    tmp_path, continue_conversation
):
    worker = make_worker(tmp_path, "QA")
    worker.cfg.qa_continue_conversation = continue_conversation
    worker.cfg.response_id = "resp_parent"
    client = _client()
    client.create_response.return_value = response(
        0,
        {
            "result": {
                "status": "ready",
                "data": {
                    "answer": "Odpověď.",
                    "claims": [],
                    "limitations": [],
                },
            }
        },
    )

    results, errors = run(worker, client)

    assert errors == []
    assert results[0]["status"] == "completed"
    payload = client.create_response.call_args.args[0]
    if continue_conversation:
        assert payload["previous_response_id"] == "resp_parent"
        assert results[0]["conversation_continuity"] is True
    else:
        assert "previous_response_id" not in payload
        assert results[0]["conversation_continuity"] is False
