"""Výpadky a obnova bez skutečných síťových nebo placených požadavků."""

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from delivery_fixtures import delivery_payloads
from PySide6.QtCore import QLockFile
from test_workflows import make_worker, response

from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.recovery import recover_run
from kajovo.core.request_rules import validate_response_payload
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
@pytest.mark.parametrize("pending_index", [0, 2, 3])
def test_worker_recovers_preparation_and_file_without_reposting(tmp_path, mode, pending_index):
    worker = make_worker(tmp_path, mode)
    if mode == "MODIFY":
        source = tmp_path / "in"
        source.mkdir()
        worker.cfg.in_dir = str(source)
    file = {"contract": "A3_FILE" if mode == "GENERATE" else "B3_FILE", "path": "hello.txt", "content": "hello\n",
            "chunking": {"chunk_index": 0, "chunk_count": 1, "has_more": False, "next_chunk_index": None}}
    if mode == "MODIFY":
        file["action"] = "add"
    results = [response(i, item) for i, item in enumerate([*delivery_payloads(mode), file])]
    expected = "hello\n"
    client = Mock()
    from kajovo.core.context_compiler import content_hash
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000, "request_hash": content_hash(payload)}
    client.upload_file.return_value = {"id": "file_test"}
    client.retrieve_file.return_value = {"id": "file_test", "filename": "input.txt", "bytes": 1}
    client.create_response.side_effect = [*results[:pending_index], {"id": results[pending_index]["id"], "status": "queued"}]
    worker.settings.response_poll_timeout_s = 0.001
    # Přerušení ihned po přijetí ID bez čekání v reálném čase.
    def stop_get(*args):
        raise OpenAIError("offline", status_code=404)
    client.retrieve_response.side_effect = stop_get
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client), patch.object(ResponseJournal, "_wait"):
        worker.run()
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "response_pending"
    upload_count = client.upload_file.call_count
    ui, _, _ = recover_run(worker.settings.log_dir, worker.log.run_id)
    assert ui["prompt"] == worker.cfg.prompt
    resumed = RunWorker(worker.cfg, worker.settings, "test", RunLogger(worker.settings.log_dir, worker.log.run_id, "test", resume=True))
    resumed.settings.response_poll_timeout_s = 3600
    client.retrieve_response.side_effect = None
    client.retrieve_response.return_value = results[pending_index]
    client.create_response.side_effect = results[pending_index + 1:]
    errors = []
    resumed.finished_err.connect(errors.append)
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client), patch.object(ResponseJournal, "_wait"):
        resumed.run()
    assert errors == []
    assert (tmp_path / "out" / "hello.txt").read_text() == expected
    assert client.create_response.call_count == len(results)
    assert client.upload_file.call_count == upload_count


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
def test_restart_after_first_output_write_keeps_files_and_response_chain(tmp_path, mode):
    worker = make_worker(tmp_path, mode)
    if mode == "MODIFY":
        source = tmp_path / "out"
        source.mkdir()
        worker.cfg.in_dir = worker.cfg.out_dir
        worker.cfg.in_equals_out = True
    preparation = delivery_payloads(mode)
    structure = preparation[-1]
    key = "files" if mode == "GENERATE" else "touched_files"
    second = copy.deepcopy(structure[key][0])
    second["path"] = "world.txt"
    structure[key].append(second)
    from delivery_fixtures import implementation_fixture
    implementation_fixture(structure, preparation[0], preparation[1])
    files = [{"contract": "A3_FILE" if mode == "GENERATE" else "B3_FILE", "path": path, "content": path + "\n",
              "chunking": {"chunk_index": 0, "chunk_count": 1, "has_more": False, "next_chunk_index": None}}
             for path in ("hello.txt", "world.txt")]
    if mode == "MODIFY":
        for file in files:
            file["action"] = "add"
    client = Mock()
    from kajovo.core.context_compiler import content_hash
    client.count_input_tokens.side_effect = lambda payload: {
        "input_tokens": 1000, "request_hash": content_hash(payload)}
    client.upload_file.return_value = {"id": "file_test"}
    client.retrieve_file.return_value = {"id": "file_test", "filename": "input.txt", "bytes": 1}
    client.create_response.side_effect = [response(i, item) for i, item in enumerate([*preparation, *files])]
    save = worker._save_out_files

    def partial_save(rows):
        save(rows[:1])
        raise OSError("Pád po prvním zápisu")

    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client), patch.object(worker, "_save_out_files", side_effect=partial_save):
        worker.run()
    assert (tmp_path / "out" / "hello.txt").is_file()
    assert not (tmp_path / "out" / "world.txt").exists()
    worker.cfg.skip_paths = ["hello.txt"]
    worker.cfg.completed_hashes = {"hello.txt": hashlib.sha256((tmp_path / "out" / "hello.txt").read_bytes()).hexdigest()}
    resumed = RunWorker(worker.cfg, worker.settings, "test", RunLogger(worker.settings.log_dir, worker.log.run_id, "test", resume=True))
    errors = []
    resumed.finished_err.connect(errors.append)
    with patch("kajovo.core.runs.executor.OpenAIClient", return_value=client):
        resumed.run()
    assert errors == []
    assert client.create_response.call_count == 5
    for path in ("hello.txt", "world.txt"):
        assert (tmp_path / "out" / path).read_text() == path + "\n"


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
def test_background_preparation_resumes_then_submits_only_file_batch(tmp_path, mode):
    from test_delivery_pipeline import _client, _run, _scenario
    worker, preparation, _ = _scenario(tmp_path, mode, True, True)
    client, calls = _client(preparation[1:])
    first = response(999, preparation[0])
    client.create_response.side_effect = [{"id": first["id"], "status": "queued"}]
    client.retrieve_response.side_effect = OpenAIError("offline", status_code=404)
    with patch.object(ResponseJournal, "_wait"):
        results, errors = _run(worker, client)
    assert errors and not results
    client.create_batch.assert_not_called()
    resumed = RunWorker(worker.cfg, worker.settings, "test", RunLogger(worker.settings.log_dir, worker.log.run_id, "test", resume=True))
    next_client, calls = _client(preparation[1:])
    next_client.retrieve_response.return_value = first
    with patch.object(ResponseJournal, "_wait"):
        results, errors = _run(resumed, next_client)
    assert errors == [] and results[0]["batch_id"] == "batch_work"
    assert len(calls) == 3
    assert all(body["background"] and body["store"] for body in calls)
    rows = next_client.create_batch.call_args.kwargs["_prevalidated_rows"]
    assert all(not row["body"].get("background") for row in rows)
    next_client.create_batch.assert_called_once()


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
