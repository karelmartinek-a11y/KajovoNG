"""Continue přebírá původní LIVE práci bez placeného opakování."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from change_v2_fixtures import make_client, run, scenario
from kajovo.core.contracts import ContractError
from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.response_journal import ResponseJournal
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.runlog import RunLogger
from kajovo.core.runs.executor import RunExecutor
from kajovo.core.runs.live_continuation import inherit_pending, read_evidence, response_claim


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY", "QA", "QFILE"])
def test_branch_dry_run_is_effective_only_for_modify(mode):
    from kajovo.studio.history_launcher import HistoryBranchLauncher
    original = {"dry_run": True}
    derived = HistoryBranchLauncher._branch_ui(original, mode, "continue", "input_ready")
    assert derived["dry_run"] is (mode == "MODIFY")
    assert original == {"dry_run": True}


def pending_run(tmp_path, mode="GENERATE", phase="A1_PLAN_V2"):
    worker, client, responder = scenario(tmp_path, mode)
    pending = {}

    def create(payload):
        completed = responder(payload)
        if payload["text"]["format"]["name"] == phase and not pending:
            pending.update(response=completed, payload=copy.deepcopy(payload))
            return {"id": completed["id"], "status": "queued", "model": completed["model"]}
        return completed

    client.create_response.side_effect = create
    client.retrieve_response.side_effect = OpenAIError("offline", status_code=404)
    with patch.object(ResponseJournal, "_wait"):
        results, errors = run(worker, client)
    assert not results and errors and pending
    return worker, pending


def child_of(worker, name="RUN_CHILD"):
    adapter = LegacyRunAdapter(Path(worker.log.paths.run_dir))
    logger = RunLogger(worker.settings.log_dir, name, "test")
    evidence = read_evidence(adapter.root)
    inherit_pending(adapter, logger, evidence)
    return RunExecutor(worker.cfg, worker.settings, "test", logger), evidence


@pytest.mark.parametrize("mode,phase", [
    ("GENERATE", "A0R_REQUIREMENTS_V2"),
    ("GENERATE", "A1_PLAN_V2"),
    ("GENERATE", "FILE_CONTENT_V1"),
    ("MODIFY", "B1_PLAN_V2"),
])
def test_child_gets_frozen_pending_and_completes_without_duplicate_post(tmp_path, mode, phase):
    worker, pending = pending_run(tmp_path, mode, phase)
    root = Path(worker.log.paths.run_dir)
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    child, evidence = child_of(worker)
    client, responder = make_client(mode)
    client.retrieve_response.return_value = pending["response"]
    with patch.object(ResponseJournal, "_wait"):
        results, errors = run(child, client)
    assert errors == []
    assert results[0]["status"] == "files_complete_unverified"
    client.retrieve_response.assert_called_once_with(pending["response"]["id"])
    assert all(payload != pending["payload"] for payload in responder.calls)
    journal = ResponseJournal(child.log)
    key = evidence["pending_hash"]
    assert journal.entries[key]["payload"] == pending["payload"]
    assert journal.inherited_order(pending["payload"]).to_dict() == {
        key: value for key, value in evidence["work_orders"][key].items() if key != "order_hash"
    }
    assert before == {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_claim_survives_launcher_instances_and_blocks_sibling(tmp_path):
    worker, pending = pending_run(tmp_path)
    child, _ = child_of(worker)
    with pytest.raises(ContractError, match="již převzal"):
        child_of(worker, "RUN_SIBLING")
    with response_claim(worker.settings.log_dir, pending["response"]["id"], child.log.run_id):
        with pytest.raises(BlockingIOError):
            with response_claim(worker.settings.log_dir, pending["response"]["id"], child.log.run_id):
                pytest.fail("Souběžný claim nesmí uspět.")


@pytest.mark.parametrize("failure_point", ["archive", "manifest", "state"])
@pytest.mark.parametrize("existing_owner", [False, True])
def test_local_copy_failure_keeps_parent_owner_and_allows_get_retry(tmp_path, monkeypatch, failure_point, existing_owner):
    worker, pending = pending_run(tmp_path)
    if existing_owner:
        worker, _ = child_of(worker)
    adapter = LegacyRunAdapter(Path(worker.log.paths.run_dir))
    evidence = read_evidence(adapter.root)
    failed = RunLogger(worker.settings.log_dir, "RUN_FAILED_COPY", "test")
    target, method = {
        "archive": (failed.bundle, "archive_artifact"),
        "manifest": (failed, "save_json"),
        "state": (failed, "update_state"),
    }[failure_point]
    with monkeypatch.context() as changes:
        changes.setattr(target, method, Mock(side_effect=OSError("disk failure")))
        with pytest.raises(OSError, match="disk failure"):
            inherit_pending(adapter, failed, evidence)
    with response_claim(worker.settings.log_dir, pending["response"]["id"], worker.log.run_id):
        pass
    retry, _ = child_of(worker, "RUN_RETRY_COPY")
    client, _ = make_client("GENERATE")
    client.retrieve_response.return_value = pending["response"]
    journal = ResponseJournal(retry.log)
    with patch.object(ResponseJournal, "_wait"):
        result = journal.execute(client, pending["payload"], stopped=lambda: False,
                                 cancelled=lambda: False, progress=Mock())
    assert result["id"] == pending["response"]["id"]
    client.create_response.assert_not_called()
    client.retrieve_response.assert_called_once_with(result["id"])


def test_copied_foreign_journal_without_lineage_cannot_borrow_provider_operation(tmp_path):
    worker, _ = pending_run(tmp_path)
    evidence = read_evidence(worker.log.paths.run_dir)
    foreign = RunLogger(worker.settings.log_dir, "RUN_FOREIGN", "test")
    foreign.save_json("manifests", "response_journal", evidence["journal"])
    foreign.update_state({"response_pending": evidence["source_state"]["response_pending"]})
    with pytest.raises(ContractError, match="provider operace"):
        read_evidence(foreign.paths.run_dir)


def test_inherited_order_requires_handoff_bound_to_child_identity(tmp_path):
    worker, _ = pending_run(tmp_path)
    child, evidence = child_of(worker)
    child.log.save_json("manifests", "live_continuation", {**evidence, "target_run_id": "RUN_FOREIGN"})
    with pytest.raises(ContractError, match="lineage"):
        read_evidence(child.log.paths.run_dir)


def test_missing_journal_fails_before_client_creation(tmp_path):
    worker, _ = pending_run(tmp_path)
    child, _ = child_of(worker)
    child.log.save_json("manifests", "response_journal", {"version": 1, "entries": {}})
    client = Mock()
    results, errors = run(child, client)
    assert not results and errors
    client.retrieve_response.assert_not_called()
    client.create_response.assert_not_called()


def test_timeout_child_can_be_continued_by_descendant(tmp_path):
    worker, pending = pending_run(tmp_path)
    child, _ = child_of(worker)
    unavailable, _ = make_client("GENERATE")
    unavailable.retrieve_response.side_effect = OpenAIError("offline", status_code=404)
    with patch.object(ResponseJournal, "_wait"):
        results, errors = run(child, unavailable)
    assert not results and errors
    unavailable.create_response.assert_not_called()
    descendant, _ = child_of(child, "RUN_DESCENDANT")
    client, _ = make_client("GENERATE")
    client.retrieve_response.return_value = pending["response"]
    with patch.object(ResponseJournal, "_wait"):
        results, errors = run(descendant, client)
    assert not errors and results[0]["status"] == "files_complete_unverified"


def test_launcher_child_uses_get_before_source_compilation(tmp_path, monkeypatch):
    from kajovo.studio.history_launcher import HistoryBranchLauncher
    worker, pending = pending_run(tmp_path)
    adapter = LegacyRunAdapter(Path(worker.log.paths.run_dir))
    context = SimpleNamespace(settings=worker.settings, models=[worker.cfg.model], api_key="test", operations=Mock())
    launcher = HistoryBranchLauncher(context)
    checkpoint = next(row for row in adapter.checkpoints() if row["safe_to_continue"])
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "continue")
    monkeypatch.setattr("kajovo.studio.history_launcher.RunWorker", RunExecutor)
    _run_id, child, _output, _project = launcher.launch(adapter, preview, _prepare_only=True)
    client, _ = make_client("GENERATE")

    def retrieve(response_id):
        assert response_id == pending["response"]["id"]
        assert client.stopped() is False
        child._stop = True
        try:
            assert client.stopped() is True
        finally:
            child._stop = False
        return copy.deepcopy(pending["response"])

    client.retrieve_response.side_effect = retrieve
    results, errors = [], []
    child.finished_ok.connect(results.append)
    child.finished_err.connect(errors.append)
    with patch.object(ResponseJournal, "_wait"), patch(
        "kajovo.core.runs.executor.freeze_run_sources", side_effect=AssertionError("SourcePack se nesmí znovu kompilovat")
    ), patch("kajovo.core.runs.executor.OpenAIClient", return_value=client) as constructor:
        child.run()
    constructor.assert_called_once_with("test", timeout_s=child.settings.response_timeout_s)
    assert not errors and results[0]["status"] == "files_complete_unverified"
    client.retrieve_response.assert_called_once_with(pending["response"]["id"])


def test_history_continue_requires_evidence_but_explicit_rerun_is_distinct(tmp_path):
    from kajovo.studio.history_launcher import HistoryBranchLauncher
    worker, _ = pending_run(tmp_path)
    adapter = LegacyRunAdapter(Path(worker.log.paths.run_dir))
    launcher = HistoryBranchLauncher(SimpleNamespace(settings=worker.settings))
    checkpoint = next(row for row in adapter.checkpoints() if row["safe_to_continue"])
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "continue")
    assert preview.first_paid_operation.startswith("GET")
    worker.log.save_json("manifests", "response_journal", {"version": 1, "entries": {}})
    with pytest.raises(ContractError, match="journal"):
        launcher.preview(adapter, checkpoint["checkpoint_id"], "continue")
    rerun = launcher.preview(adapter, checkpoint["checkpoint_id"], "rerun")
    assert not rerun.first_paid_operation.startswith("GET")


def test_real_client_get_observer_uses_child_step_and_preserves_original_identity(tmp_path):
    from kajovo.core.runs.response_execution import _create_response, retrieve_inherited_response
    worker, pending = pending_run(tmp_path)
    root = Path(worker.log.paths.run_dir)
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    child, evidence = child_of(worker)
    child._response_journal = ResponseJournal(child.log)
    order = child._response_journal.inherited_order(pending["payload"])
    assert order.step_id not in {step["step_id"] for step in child.log.bundle.steps()}
    client = OpenAIClient("test")
    http = Mock(status_code=200, headers={"content-type": "application/json", "x-request-id": "req_get"},
                text=json.dumps(pending["response"]), content=json.dumps(pending["response"]).encode())
    http.json.return_value = copy.deepcopy(pending["response"])
    try:
        with patch.object(client.session, "request", return_value=http) as transport, patch.object(ResponseJournal, "_wait"):
            response = retrieve_inherited_response(child, client)
            replay = _create_response(child, client, copy.deepcopy(pending["payload"]))
        assert response["id"] == replay["id"] == pending["response"]["id"]
        transport.assert_called_once()
        requests = LegacyRunAdapter(Path(child.log.paths.run_dir)).requests()
        gets = [row for row in requests if row.get("request_role") == "transport"]
        assert len(gets) == 1 and gets[0]["method"] == "GET"
        step = next(row for row in child.log.bundle.steps() if row["step_id"] == gets[0]["step_id"])
        assert step["run_id"] == child.log.run_id
        assert step["step_id"] != order.step_id
        assert step["model"] == order.model
        assert step["reasoning_effort"] == str((pending["payload"].get("reasoning") or {}).get("effort") or "")
        assert child._response_journal.inherited_order(pending["payload"]).order_hash == evidence["work_orders"][evidence["pending_hash"]]["order_hash"]
        assert before == {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    finally:
        client.session.close()
        if client._sdk is not None:
            client._sdk.close()


@pytest.fixture(scope="module")
def continuation_envelope(tmp_path_factory):
    worker, _ = pending_run(tmp_path_factory.mktemp("live_contract"))
    return {**read_evidence(worker.log.paths.run_dir), "target_run_id": "RUN_BOUNDARY_CHILD"}


@pytest.mark.parametrize("mutation", [
    "version", "boolean_version", "unknown", "missing", "type", "hash", "provider_id",
    "operation", "work_order", "runtime", "source_pack", "upload_id",
])
def test_continuation_contract_rejects_invalid_envelope_on_read_and_write(tmp_path, continuation_envelope, mutation):
    from kajovo.core.runs.live_continuation import validate_live_continuation
    value = copy.deepcopy(continuation_envelope)
    key = value["pending_hash"]
    if mutation == "version":
        value["version"] = 2
    elif mutation == "boolean_version":
        value["version"] = True
    elif mutation == "unknown":
        value["extra"] = "nesmí být ignorováno"
    elif mutation == "missing":
        value.pop("runtime")
    elif mutation == "type":
        value["source_upload_ids"] = []
    elif mutation == "hash":
        value["pending_hash"] = "invalid"
    elif mutation == "provider_id":
        value["response_id"] = "../responses/other"
    elif mutation == "operation":
        value["provider_operations"][key]["provider_id"] = "resp_foreign"
    elif mutation == "work_order":
        value["work_orders"][key]["extra"] = True
    elif mutation == "runtime":
        value["runtime"]["attributes"]["_diag_text"] = []
    elif mutation == "source_pack":
        value["source_pack"]["version"] = 2
    else:
        value["source_upload_ids"]["SRC-1"] = 42
    with pytest.raises(ContractError):
        validate_live_continuation(value)
    logger = RunLogger(str(tmp_path), "RUN_BOUNDARY_CHILD", "test")
    with pytest.raises(ContractError):
        inherit_pending(Mock(), logger, {k: v for k, v in value.items() if k != "target_run_id"})
    assert logger.find_json("manifests", "live_continuation") is None
    logger.save_json("manifests", "live_continuation", value)
    with pytest.raises(ContractError):
        ResponseJournal(logger)


def test_sqlite_work_order_json_rejects_duplicate_keys(tmp_path):
    import sqlite3
    from contextlib import closing
    worker, _ = pending_run(tmp_path)
    database = Path(worker.settings.log_dir) / "orchestration.sqlite3"
    with closing(sqlite3.connect(database)) as db:
        raw = db.execute("SELECT work_order_json FROM work_orders LIMIT 1").fetchone()[0]
        db.execute("UPDATE work_orders SET work_order_json=? WHERE work_order_json=?",
                   ('{"version":2,' + raw[1:], raw))
        db.commit()
    with pytest.raises(ContractError, match="[Dd]uplicit"):
        read_evidence(worker.log.paths.run_dir)


def test_history_continue_after_get_and_domain_failure_replays_completed_without_post(tmp_path, monkeypatch):
    from kajovo.core.recoverable_artifacts import load_run_state
    from kajovo.core.runs.live_continuation import pending_live
    from kajovo.studio.history_launcher import HistoryBranchLauncher
    from kajovo.studio.history_policy import ActionAvailabilityPolicy

    worker, pending = pending_run(tmp_path, phase="FILE_CONTENT_V1")
    child, _ = child_of(worker)
    client, _ = make_client("GENERATE")
    client.retrieve_response.return_value = pending["response"]
    with patch.object(ResponseJournal, "_wait"), patch(
        "kajovo.core.runs.file_execution.validate_output", side_effect=ContractError("Lokální validace selhala")
    ):
        results, errors = run(child, client)
    assert not results and errors
    client.create_response.assert_not_called()
    state = load_run_state(child.log.paths.run_dir)
    assert state["status"] == "failed" and pending_live(state)
    assert not state.get("response_pending")
    assert state["live_replay_pending"]["id"] == pending["response"]["id"]
    assert child.log.find_json("manifests", "live_replay_complete") is None
    adapter = LegacyRunAdapter(Path(child.log.paths.run_dir))
    checkpoints = [{**row, "_availability_valid": True} for row in adapter.checkpoints()]
    decisions = ActionAvailabilityPolicy().evaluate(adapter.run_record(), state, checkpoints, legacy=False)
    assert decisions["continue"].enabled and decisions["repair"].enabled
    context = SimpleNamespace(settings=child.settings, models=[child.cfg.model], api_key="test", operations=Mock())
    launcher = HistoryBranchLauncher(context)
    checkpoint = next(row for row in checkpoints if row["safe_to_continue"])
    preview = launcher.preview(adapter, checkpoint["checkpoint_id"], "continue")
    monkeypatch.setattr("kajovo.studio.history_launcher.RunWorker", RunExecutor)
    _identifier, descendant, _output, _project = launcher.launch(adapter, preview, _prepare_only=True)
    second_client, _ = make_client("GENERATE")
    results, errors = run(descendant, second_client)
    assert not errors and results[0]["status"] == "files_complete_unverified"
    second_client.create_response.assert_not_called()
    second_client.retrieve_response.assert_not_called()
    final = load_run_state(descendant.log.paths.run_dir)
    assert final["live_replay_complete"] is True and not pending_live(final)
