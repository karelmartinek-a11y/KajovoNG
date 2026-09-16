from __future__ import annotations

from unittest.mock import Mock

import pytest

from kajovo.core.runs.polling import VectorStorePollingContext, wait_vector_store_files


def test_vector_store_polling_completes_and_reports_progress(monkeypatch):
    monkeypatch.setattr("kajovo.core.runs.polling.time.sleep", lambda _: None)
    responses = iter(({"status": "in_progress"}, {"status": "completed"}))
    retrieve = Mock(side_effect=lambda _vs, _file: next(responses))
    progress = Mock()
    evidence = Mock()

    wait_vector_store_files(
        VectorStorePollingContext(
            retrieve=retrieve,
            check_stop=lambda: None,
            progress_emit=progress,
            evidence_emit=evidence,
            timeout_s=30,
            poll_interval_s=0,
        ),
        "vs_test",
        ["vsf_test"],
    )

    assert retrieve.call_count == 2
    assert evidence.call_count == 0
    assert any(call.args[0].completed == 1 for call in progress.call_args_list)


def test_vector_store_polling_records_transient_failure_and_recovers(monkeypatch):
    monkeypatch.setattr("kajovo.core.runs.polling.time.sleep", lambda _: None)
    responses = iter((ConnectionError("temporary"), {"status": "completed"}))

    def retrieve(_vs, _file):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value

    evidence = Mock()
    wait_vector_store_files(
        VectorStorePollingContext(
            retrieve=retrieve,
            check_stop=lambda: None,
            progress_emit=Mock(),
            evidence_emit=evidence,
            timeout_s=30,
            poll_interval_s=0,
        ),
        "vs_test",
        ["vsf_test"],
    )

    evidence.assert_called_once()
    event, payload = evidence.call_args.args
    assert event == "vector_store.poll_error"
    assert payload["attempt"] == 1
    assert payload["error_class"] == "ConnectionError"
    assert "temporary" not in repr(payload)


def test_vector_store_polling_fails_after_repeated_provider_errors(monkeypatch):
    monkeypatch.setattr("kajovo.core.runs.polling.time.sleep", lambda _: None)
    evidence = Mock()

    with pytest.raises(RuntimeError, match="opakovaně ověřit"):
        wait_vector_store_files(
            VectorStorePollingContext(
                retrieve=Mock(side_effect=ConnectionError("secret provider detail")),
                check_stop=lambda: None,
                progress_emit=Mock(),
                evidence_emit=evidence,
                timeout_s=30,
                poll_interval_s=0,
                max_consecutive_failures=3,
            ),
            "vs_test",
            ["vsf_test"],
        )

    assert evidence.call_count == 3
    assert all("secret provider detail" not in repr(call.args) for call in evidence.call_args_list)


def test_vector_store_polling_surfaces_provider_failed_status(monkeypatch):
    monkeypatch.setattr("kajovo.core.runs.polling.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="index rejected"):
        wait_vector_store_files(
            VectorStorePollingContext(
                retrieve=lambda _vs, _file: {
                    "status": "failed",
                    "last_error": {"message": "index rejected"},
                },
                check_stop=lambda: None,
                progress_emit=Mock(),
                timeout_s=30,
                poll_interval_s=0,
            ),
            "vs_test",
            ["vsf_test"],
        )


def test_vector_store_polling_honors_cancellation_before_retrieve():
    retrieve = Mock()

    def stopped():
        raise RuntimeError("STOP_REQUESTED")

    with pytest.raises(RuntimeError, match="STOP_REQUESTED"):
        wait_vector_store_files(
            VectorStorePollingContext(
                retrieve=retrieve,
                check_stop=stopped,
                progress_emit=Mock(),
            ),
            "vs_test",
            ["vsf_test"],
        )

    retrieve.assert_not_called()
