"""Výpadky a obnova bez skutečných síťových nebo placených požadavků."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QLockFile
from change_v2_fixtures import make_client, run, scenario, staged_path
from test_workflows import make_worker

from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.request_rules import validate_response_payload
from kajovo.core.contracts import ContractError
from kajovo.core.response_journal import ResponseJournal, ResponsePending, SubmissionUnknown
from kajovo.core.runlog import RunLogger
from kajovo.core.runs.executor import RunExecutor as RunWorker


class Clock:
    value = 0.0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


@pytest.fixture
def journal(tmp_path):
    clock = Clock()
    logger = RunLogger(str(tmp_path), "RUN_130920260100_TEST")
    return ResponseJournal(logger, clock=clock.now, sleep=clock.sleep)


def execute(journal, client, **kwargs):
    return journal.execute(client, {"model": "gpt-5.4", "input": "test"},
                           stopped=kwargs.get("stopped", lambda: False),
                           cancelled=kwargs.get("cancelled", lambda: False), progress=Mock())


def test_long_generation_and_restart_only_retrieve_same_id(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_long", "status": "queued"}
    client.retrieve_response.side_effect = [{"id": "resp_long", "status": "in_progress"}] * 160 + [
        {"id": "resp_long", "status": "completed", "output": []}]
    result = execute(journal, client)
    assert journal.clock() > 300
    assert result["status"] == "completed"
    body = client.create_response.call_args.args[0]
    assert body["background"] is True and body["store"] is True
    replay = ResponseJournal(journal.log)
    assert execute(replay, client) == result
    assert client.create_response.call_count == 1
    assert all(call.args == ("resp_long",) for call in client.retrieve_response.call_args_list)


def test_get_failure_preserves_id_and_resume(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_wait", "status": "queued"}
    client.retrieve_response.side_effect = OpenAIError("Read timeout")
    with pytest.raises(ResponsePending):
        execute(journal, client)
    assert client.retrieve_response.call_count == 4
    replay = ResponseJournal(journal.log, clock=journal.clock, sleep=journal.sleep)
    client.retrieve_response.side_effect = None
    client.retrieve_response.return_value = {"id": "resp_wait", "status": "completed"}
    assert execute(replay, client)["id"] == "resp_wait"
    assert client.create_response.call_count == 1


def test_poll_503_is_followed_by_explicit_recovery(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_wait", "status": "queued"}
    error = OpenAIError("Dočasná nedostupnost")
    error.status_code = 503
    error.code = "server_error"
    client.retrieve_response.side_effect = [error, {"id": "resp_wait", "status": "completed"}]
    execute(journal, client)
    events = [json.loads(line) for line in Path(journal.log.events_path).read_text("utf-8").splitlines()]
    failed = [e for e in events if e["type"] == "response.poll_error"]
    assert len(failed) == 1
    assert failed[0]["data"]["message"] == str(error)
    assert failed[0]["data"]["code"] == "server_error"
    recovered = [e for e in events if e["type"] == "response.poll_recovered"]
    assert len(recovered) == 1 and recovered[0]["data"]["response_id"] == "resp_wait"
    client.create_response.assert_called_once()


def test_failed_response_keeps_provider_id_after_restart(journal):
    from kajovo.core.contracts import RemoteResponseError
    client = Mock()
    client.create_response.return_value = {"id": "resp_bad", "status": "failed",
        "error": {"code": "credit_balance_exhausted"}, "_request_id": "req_provider"}
    with pytest.raises(RemoteResponseError):
        execute(journal, client)
    with pytest.raises(RemoteResponseError) as caught:
        execute(ResponseJournal(journal.log), client)
    assert caught.value.request_id == "req_provider"
    assert caught.value.code == "credit_balance_exhausted"
    client.create_response.assert_called_once()


@pytest.mark.parametrize("error", [OpenAIError("timeout"), OSError("connection lost")])
def test_unknown_submission_never_reposts(journal, error):
    client = Mock()
    client.create_response.side_effect = error
    with pytest.raises(SubmissionUnknown):
        execute(journal, client)
    with pytest.raises(SubmissionUnknown):
        execute(ResponseJournal(journal.log), client)
    assert client.create_response.call_count == 1


@pytest.mark.parametrize("status", ["failed", "cancelled", "incomplete", "unexpected"])
def test_terminal_failure_cannot_be_consumed(journal, status):
    client = Mock()
    client.create_response.return_value = {"id": "resp_bad", "status": status}
    with pytest.raises(RuntimeError, match=status):
        execute(journal, client)
    client.retrieve_response.assert_not_called()


def test_timeout_stop_and_cancel_have_distinct_effects(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_wait", "status": "queued"}
    client.retrieve_response.return_value = {"id": "resp_wait", "status": "in_progress"}
    journal.timeout_s = 3
    with pytest.raises(ResponsePending, match="limit"):
        execute(journal, client)
    with pytest.raises(ResponsePending, match="zastaveno"):
        execute(journal, client, stopped=lambda: True)
    client.cancel_response.assert_not_called()
    client.cancel_response.return_value = {"id": "resp_wait", "status": "cancelled"}
    with pytest.raises(RuntimeError, match="cancelled"):
        execute(journal, client, cancelled=lambda: True)
    client.cancel_response.assert_called_once_with("resp_wait")


def test_async_cancellation_is_sent_once_then_polled(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_wait", "status": "queued"}
    client.cancel_response.return_value = {"id": "resp_wait", "status": "in_progress"}
    client.retrieve_response.side_effect = [
        {"id": "resp_wait", "status": "in_progress"}, {"id": "resp_wait", "status": "cancelled"}]
    # Volba zrušení vznikne až po skutečném odeslání pracovní úlohy.
    with pytest.raises(RuntimeError, match="cancelled"):
        execute(journal, client, cancelled=lambda: bool(client.create_response.call_count))
    client.cancel_response.assert_called_once_with("resp_wait")
    assert client.retrieve_response.call_count == 2


@pytest.mark.parametrize("sdk", [False, True])
def test_client_background_does_not_validate_unfinished_output(sdk):
    client = OpenAIClient("test")
    client.validate_access = Mock()
    client._policy = Mock()
    result = {"id": "resp_test", "status": "queued", "output": []}
    client._sdk = Mock() if sdk else None
    client._req = Mock(return_value=result)
    assert client.create_response({"model": "gpt-5.4", "input": "x", "background": True, "store": True})["status"] == "queued"
    assert client.retrieve_response("resp_test")["id"] == "resp_test"
    assert client.cancel_response("resp_test")["id"] == "resp_test"
    assert client._req.call_count == 3
    if sdk:
        client._sdk.responses.create.assert_not_called()
        client._sdk.responses.retrieve.assert_not_called()
        client._sdk.responses.cancel.assert_not_called()


def test_background_not_allowed_inside_batch():
    with pytest.raises(ValueError):
        validate_response_payload({"model": "gpt-5.4", "background": True, "store": True}, batch=True)


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize(
    "pending_stage",
    ["requirements", "spine", "file"],
)
def test_worker_recovers_v2_stage_without_reposting(
    tmp_path, mode, pending_stage
):
    pending_contract = {
        "requirements": (
            "A0R_REQUIREMENTS_V2"
            if mode == "GENERATE"
            else "B0R_REQUIREMENTS_V2"
        ),
        "spine": (
            "A2_SPINE_V1" if mode == "GENERATE" else "B2_SPINE_V1"
        ),
        "file": "FILE_CONTENT_V1",
    }[pending_stage]
    worker, client, responder = scenario(tmp_path, mode)
    original_create = responder
    pending = {}

    def create(payload):
        name = ((payload.get("text") or {}).get("format") or {}).get("name")
        completed = original_create(payload)
        if name == pending_contract and "response" not in pending:
            pending["response"] = completed
            pending["id"] = completed["id"]
            return {
                "id": completed["id"],
                "object": "response",
                "model": completed["model"],
                "status": "queued",
            }
        return completed

    client.create_response.side_effect = create
    client.retrieve_response.side_effect = OpenAIError(
        "offline", status_code=404
    )
    worker.settings.response_poll_timeout_s = 0.001
    with patch.object(ResponseJournal, "_wait"):
        results, errors = run(worker, client)
    assert results == []
    assert errors
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "response_pending"
    create_count_before_resume = client.create_response.call_count

    resumed = RunWorker(
        worker.cfg,
        worker.settings,
        "test",
        RunLogger(
            worker.settings.log_dir,
            worker.log.run_id,
            "test",
            resume=True,
        ),
    )
    resumed.settings.response_poll_timeout_s = 3600
    resumed_client, _responder2 = make_client(mode)
    resumed_client.retrieve_response.return_value = pending["response"]
    resumed_results, resumed_errors = [], []
    resumed.finished_ok.connect(resumed_results.append)
    resumed.finished_err.connect(resumed_errors.append)
    with patch(
        "kajovo.core.runs.executor.OpenAIClient",
        return_value=resumed_client,
    ), patch.object(ResponseJournal, "_wait"):
        resumed.run()

    assert resumed_errors == []
    assert resumed_results
    resumed_client.retrieve_response.assert_called_with(pending["id"])
    # The pending paid mutation is retrieved, never submitted a second time.
    assert all(
        call.args[0].get("metadata", {}).get("kajovo_repair_attempt") != "duplicate"
        for call in resumed_client.create_response.call_args_list
    )
    if pending_contract == "FILE_CONTENT_V1":
        assert resumed_results[0]["status"] == "files_complete_unverified"
        assert staged_path(resumed, "hello.txt").read_text(encoding="utf-8") == (
            "content:hello.txt\n"
        )
    assert create_count_before_resume >= 1


def test_second_instance_does_not_change_run_state(tmp_path):
    worker = make_worker(tmp_path, "GENERATE")
    before = Path(worker.log.state_path).read_bytes()
    lock = QLockFile(str(Path(worker.log.paths.run_dir) / "execution.lock"))
    assert lock.tryLock(0)
    try:
        with patch("kajovo.core.runs.executor.OpenAIClient") as client:
            worker.run()
        client.assert_not_called()
        assert Path(worker.log.state_path).read_bytes() == before
    finally:
        lock.unlock()


def test_response_journal_rejects_duplicate_json_keys(tmp_path):
    path = tmp_path / "duplicate_journal.json"
    path.write_text('{"version":1,"version":1,"entries":{}}', encoding="utf-8")
    logger = Mock()
    logger.find_json.return_value = str(path)
    with pytest.raises(ContractError, match="Duplicit"):
        ResponseJournal(logger)


def test_corrupted_journal_cannot_submit_new_request(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_ok", "status": "completed"}
    execute(journal, client)
    path = Path(journal.log.find_json("manifests", "response_journal"))
    data = json.loads(path.read_text(encoding="utf-8"))
    next(iter(data["entries"].values()))["payload"]["input"] = "změna"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        ResponseJournal(journal.log)
    assert client.create_response.call_count == 1


def test_missing_remote_id_is_not_recreated(journal):
    client = Mock()
    client.create_response.return_value = {"id": "resp_missing", "status": "queued"}
    client.retrieve_response.side_effect = OpenAIError("Not found", status_code=404)
    with pytest.raises(ResponsePending):
        execute(journal, client)
    client.retrieve_response.assert_called_once()
    assert client.create_response.call_count == 1


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_plan_ready_checkpoint_resumes_without_repaying_preparation(tmp_path, mode):
    worker, client, responder = scenario(
        tmp_path,
        mode,
        stop_after_plan=True,
        maximum_quality=True,
    )
    results, errors = run(worker, client)
    assert errors == [] and results[0]["status"] == "plan_ready"
    paid_preparation_calls = client.create_response.call_count
    snapshot = worker.cfg.preparation_snapshot
    assert snapshot["canonical_stage"] in {"A2Q", "B2Q"}

    # Continue is a new run over the immutable preparation checkpoint.
    # The source run remains plan_ready and its authorization scope is unchanged.
    next_cfg = copy.deepcopy(worker.cfg)
    next_cfg.stop_after_plan = False
    next_cfg.preparation_snapshot = copy.deepcopy(snapshot)
    next_cfg.execution_approval_id = ""
    resumed_client, resumed_responder = make_client(mode)
    resumed = RunWorker(
        next_cfg,
        worker.settings,
        "test",
        RunLogger(
            worker.settings.log_dir,
            "RUN_130920260200_CONT",
            "test",
        ),
    )
    resumed_results, resumed_errors = [], []
    resumed.finished_ok.connect(resumed_results.append)
    resumed.finished_err.connect(resumed_errors.append)
    with patch(
        "kajovo.core.runs.executor.OpenAIClient",
        return_value=resumed_client,
    ):
        resumed.run()

    assert resumed_errors == []
    assert resumed_results[0]["status"] == "files_complete_unverified"
    names = [
        ((payload.get("text") or {}).get("format") or {}).get("name")
        for payload in resumed_responder.calls
    ]
    assert names == ["FILE_CONTENT_V1"]
    assert client.create_response.call_count == paid_preparation_calls


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_background_preparation_resume_does_not_duplicate_batch_submit(tmp_path, mode):
    worker, client, responder = scenario(
        tmp_path, mode, batch=True, maximum_quality=False
    )
    pending = {}
    original = responder

    def create(payload):
        completed = original(payload)
        name = ((payload.get("text") or {}).get("format") or {}).get("name")
        if name == ("A1_PLAN_V2" if mode == "GENERATE" else "B1_PLAN_V2") and not pending:
            pending["response"] = completed
            pending["id"] = completed["id"]
            return {
                "id": completed["id"],
                "object": "response",
                "model": completed["model"],
                "status": "queued",
            }
        return completed

    client.create_response.side_effect = create
    client.retrieve_response.side_effect = OpenAIError("offline", status_code=404)
    worker.settings.response_poll_timeout_s = 0.001
    with patch.object(ResponseJournal, "_wait"):
        results, errors = run(worker, client)
    assert results == [] and errors
    client.create_batch.assert_not_called()

    resumed = RunWorker(
        worker.cfg,
        worker.settings,
        "test",
        RunLogger(worker.settings.log_dir, worker.log.run_id, "test", resume=True),
    )
    resumed_client, _ = make_client(mode)
    resumed_client.retrieve_response.return_value = pending["response"]
    resumed_results, resumed_errors = [], []
    resumed.finished_ok.connect(resumed_results.append)
    resumed.finished_err.connect(resumed_errors.append)
    with patch(
        "kajovo.core.runs.executor.OpenAIClient",
        return_value=resumed_client,
    ), patch.object(ResponseJournal, "_wait"):
        resumed.run()

    assert resumed_errors == []
    assert resumed_results[0]["status"] == "batch_pending"
    resumed_client.create_batch.assert_called_once()
    resumed_client.retrieve_response.assert_called_with(pending["id"])



@pytest.mark.parametrize("state", ["response_pending", "submission_unknown", "cancelled"])
def test_operation_dialog_finishes_with_recoverable_status(qtbot, state):
    from kajovo.core.progress import ProgressEvent
    from kajovo.studio.operations import OperationDialog

    dialog = OperationDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("RUN", state, detail="Uložené ID"))
    dialog.finish(state)
    assert dialog.clock.finished is not None
    assert not dialog.timer.isActive()
    assert dialog.progress.maximum() == 0 or dialog.progress.value() != 100


def test_settings_expose_separate_generation_limit(qtbot, monkeypatch):
    from kajovo.core.config import AppSettings
    from kajovo.studio.context import StudioContext
    from kajovo.studio.operations import Operations
    from kajovo.studio.settings import SettingsPage

    settings = AppSettings()
    context = StudioContext(settings, Operations(None), api_key="")
    page = SettingsPage(context)
    qtbot.addWidget(page)
    assert page.editors["response_timeout_s"].value() == 300
    assert page.editors["response_poll_timeout_s"].value() == 3600
    page.editors["response_poll_timeout_s"].setValue(7200)
    assert page.snapshot().response_poll_timeout_s == 7200
    save = Mock()
    monkeypatch.setattr("kajovo.studio.settings.save_settings", save)
    page.save()
    assert save.call_args.args[0].response_poll_timeout_s == 7200
