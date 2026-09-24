"""Regrese A2-012 až A2-016 bez vzdálených volání."""

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
from kajovo.core.runs.locking import ExecutionLock
from kajovo.studio.workers.cascade_worker import CascadeRunWorker
from test_cascade_v2 import MODEL, _client, _response, _text_step, _worker


class ProcessCrash(BaseException):
    """Simuluje ukončení mimo obsluhu běžných výjimek."""


def observe(worker):
    errors, details, events, results = [], [], [], []
    worker.finished_err.connect(errors.append)
    worker.failure_detail.connect(details.append)
    worker.progress_event.connect(events.append)
    worker.finished_ok.connect(results.append)
    return errors, details, events, results


@pytest.mark.parametrize("change", ["project", "in_dir", "out_dir", "name", "created_at"])
def test_runtime_scope_isolated(tmp_path, change):
    definition = CascadeDefinition("same/name", steps=[_text_step("První")])
    first = _worker(definition, tmp_path)
    other = _worker(definition, tmp_path)
    if change == "name":
        other.cfg.cascade.name = "same?name"
    elif change == "created_at":
        other.cfg.cascade.created_at += 1
    else:
        setattr(other.cfg, change, str(tmp_path / change))
    first._write_runtime_state({"status": "submission_unknown"})
    assert first._runtime_path() != other._runtime_path()
    assert other._read_runtime_state() == {}


def test_runtime_identity_survives_edit_and_selected_step(tmp_path):
    definition = CascadeDefinition("stable", steps=[_text_step("První")])
    first = _worker(definition, tmp_path)
    other = _worker(definition, tmp_path)
    other.cfg.cascade.steps[0].input_text = "Upravené zadání"
    other.cfg.cascade.updated_at += 1
    other.cfg.cascade.run_from_step_id = definition.steps[0].id
    assert first._runtime_path() == other._runtime_path()


def test_same_runtime_lock_blocks_before_client(tmp_path, monkeypatch):
    worker = _worker(CascadeDefinition("lock", steps=[_text_step("První")]), tmp_path)
    errors, details, events, results = observe(worker)
    client = Mock()
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", client)
    with ExecutionLock(worker._runtime_path() + ".lock"):
        worker.execute()
    assert len(errors) == len(details) == 1
    assert events[-1].state == "failed"
    assert not results
    client.assert_not_called()


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"x":1,"x":2}', b"\xff", b"{}"])
def test_corrupt_runtime_has_one_terminal_error_and_stays_untouched(tmp_path, monkeypatch, raw):
    worker = _worker(CascadeDefinition("broken", steps=[_text_step("První")]), tmp_path)
    runtime = Path(worker._runtime_path())
    runtime.write_bytes(raw)
    errors, details, events, results = observe(worker)
    client = Mock()
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", client)
    worker.execute()
    assert len(errors) == len(details) == len(events) == 1
    assert events[0].state == "failed"
    assert not results
    assert runtime.read_bytes() == raw
    client.assert_not_called()


def test_legacy_name_only_runtime_is_not_adopted(tmp_path):
    worker = _worker(CascadeDefinition("legacy", steps=[_text_step("První")]), tmp_path)
    legacy = Path(worker._runtime_path()).parent / worker._cascade_filename("legacy")
    legacy.write_text('{"status":"submission_unknown"}', encoding="utf-8")
    errors, details, events, results = observe(worker)
    worker.execute()
    assert errors and details and events[-1].state == "failed" and not results
    assert "jednoznačnou identitu" in details[0].detail


def legacy_evidence(worker):
    root = Path(worker.settings.log_dir) / "RUN_LEGACY"
    root.mkdir(parents=True)
    evidence = {
        "run_id": root.name, "status": "submission_unknown",
        "project": worker.cfg.project, "in_dir": worker.cfg.in_dir, "out_dir": worker.cfg.out_dir,
        "cascade_definition": worker.cfg.cascade.to_dict(),
        "cascade_runtime": worker._cache_snapshot(
            context={}, context_response_ids={}, values={}, executed_step_ids=set(),
        ),
    }
    state_path = root / "run_state.json"
    state_path.write_text(json.dumps(evidence), encoding="utf-8")
    legacy = Path(worker._runtime_path()).parent / worker._cascade_filename(worker.cfg.cascade.name)
    legacy.write_text(json.dumps({
        "run_id": root.name, "status": "submission_unknown", "cache": {"untrusted": True},
    }), encoding="utf-8")
    return legacy, state_path, evidence


def test_legacy_foreign_owner_does_not_block_same_name(tmp_path, monkeypatch):
    step = _text_step("První")
    definition = CascadeDefinition("shared", steps=[step])
    owner = _worker(definition, tmp_path)
    legacy, state_path, _ = legacy_evidence(owner)
    original = legacy.read_bytes(), state_path.read_bytes()
    worker = _worker(definition, tmp_path)
    worker.cfg.project = "jiný projekt"
    assert worker._read_runtime_state() == {}
    client = _client()
    client.create_response.return_value = _response("resp_new", step.outputs[0], "hotovo")
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    errors, _, _, results = observe(worker)
    worker.execute()
    assert not errors and results
    client.create_response.assert_called_once()
    assert (legacy.read_bytes(), state_path.read_bytes()) == original


def test_legacy_matching_owner_promotes_canonical_unknown_without_rewriting_source(tmp_path, monkeypatch):
    worker = _worker(CascadeDefinition("matching", steps=[_text_step("První")]), tmp_path)
    legacy, state_path, evidence = legacy_evidence(worker)
    original = legacy.read_bytes(), state_path.read_bytes()
    assert worker._read_runtime_state()["cache"] == evidence["cascade_runtime"]
    assert not Path(worker._runtime_path()).exists()
    client = Mock()
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", client)
    errors, _, events, results = observe(worker)
    worker.execute()
    assert errors and not results and events[-1].state == "submission_unknown"
    pointer = json.loads(Path(worker._runtime_path()).read_text(encoding="utf-8"))
    assert pointer["run_id"] == "RUN_LEGACY"
    assert pointer["cache"] == evidence["cascade_runtime"]
    assert pointer["runtime_identity"] == worker._runtime_identity()
    assert (legacy.read_bytes(), state_path.read_bytes()) == original
    client.assert_not_called()


@pytest.mark.parametrize("broken", ["owner", "cache"])
def test_legacy_ambiguous_or_missing_canonical_cache_blocks_without_mutation(tmp_path, monkeypatch, broken):
    worker = _worker(CascadeDefinition("ambiguous", steps=[_text_step("První")]), tmp_path)
    legacy, state_path, evidence = legacy_evidence(worker)
    if broken == "owner":
        evidence.pop("project")
    else:
        evidence["cascade_runtime"] = None
    state_path.write_text(json.dumps(evidence), encoding="utf-8")
    original = legacy.read_bytes(), state_path.read_bytes()
    client = Mock()
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", client)
    errors, details, events, results = observe(worker)
    worker.execute()
    assert len(errors) == len(details) == len(events) == 1
    assert events[-1].state == "failed" and not results
    assert not Path(worker._runtime_path()).exists()
    assert (legacy.read_bytes(), state_path.read_bytes()) == original
    client.assert_not_called()


def test_qt_boundary_reports_unexpected_executor_error(tmp_path, monkeypatch):
    executor = _worker(CascadeDefinition("qt", steps=[_text_step("První")]), tmp_path)
    worker = CascadeRunWorker(executor.cfg, executor.settings, "test")
    errors, details, events, results = observe(worker)
    monkeypatch.setattr(worker._executor, "execute", Mock(side_effect=OSError("hranice workeru")))
    worker.run()
    assert len(errors) == len(details) == len(events) == 1
    assert events[0].state == "failed" and not results


def legacy_definition():
    first = CascadeStep(
        title="Zdroj", model=MODEL, input_text="Vytvoř zdroj",
        output_type="json", output_schema_kind="manifest", expected_out_files=["source.txt"],
    )
    second = CascadeStep(
        title="Spotřebitel", model=MODEL, input_text="Zpracuj zdroj",
        output_type="json", output_schema_kind="manifest", expected_out_files=["result.txt"],
        files_local_paths=["{{step.1.out_file_path:source.txt}}"],
    )
    return CascadeDefinition.from_dict(CascadeDefinition("dynamic", steps=[first, second]).to_dict())


def manifest_response(name):
    return {"id": "resp_" + name, "status": "completed", "output_text": json.dumps({
        "files": [{"path": name, "content": "obsah " + name, "encoding": "utf-8"}],
    })}


@pytest.mark.parametrize("failure", [ProcessCrash, OSError])
def test_dynamic_input_and_primary_recovery_after_staging_relocation(tmp_path, monkeypatch, failure):
    definition = legacy_definition()
    first = _worker(definition, tmp_path)
    client = _client()
    client.create_response.side_effect = [manifest_response("source.txt"), manifest_response("result.txt")]
    uploads = []

    def upload(path, **kwargs):
        uploads.append((str(path), Path(path).read_bytes()))
        return {"id": "file_" + str(len(uploads))}

    client.upload_file.side_effect = upload
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    process = first._process_expected_out_files

    def interrupted(**kwargs):
        if kwargs["idx"] == 2:
            raise failure("pád při delivery")
        return process(**kwargs)

    monkeypatch.setattr(first, "_process_expected_out_files", interrupted)
    if failure is ProcessCrash:
        with pytest.raises(ProcessCrash):
            first.execute()
    else:
        first.execute()
    assert client.create_response.call_count == 2
    assert len(uploads) == 2
    assert uploads[1][1] == b"obsah source.txt"
    assert uploads[1][0].startswith(first.logger.paths.run_dir)
    state = json.loads(Path(first.logger.state_path).read_text(encoding="utf-8"))
    assert len(state["cascade_runtime"]["primary_responses"]) == 1
    if failure is ProcessCrash:
        assert not first._read_runtime_state()["cache"]["primary_responses"]

    resumed = copy.deepcopy(definition)
    resumed.run_from_step_id = resumed.steps[1].id
    child = _worker(resumed, tmp_path)
    errors, _, _, results = observe(child)
    recovered_client = _client()
    recovered_client.upload_file.return_value = {"id": "file_result"}
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: recovered_client)
    child.execute()
    assert not errors and results
    recovered_client.create_response.assert_not_called()
    recovered_client.upload_file.assert_called_once()
    assert (tmp_path / "OUT" / "result.txt").read_text(encoding="utf-8") == "obsah result.txt"
    child_state = json.loads(Path(child.logger.state_path).read_text(encoding="utf-8"))
    source_path = child_state["cascade_runtime"]["legacy_context"]["step.1.out_file_path:source.txt"]
    assert source_path.startswith(child.logger.paths.run_dir)
    assert not child_state["cascade_runtime"]["primary_responses"]

    # Výslovné opakování hotového kroku musí vytvořit novou odpověď.
    repeated = _worker(resumed, tmp_path)
    repeated_errors, _, _, repeated_results = observe(repeated)
    recovered_client.create_response.return_value = manifest_response("result.txt")
    repeated.execute()
    recovered_client.create_response.assert_called_once()
    assert repeated_errors and not repeated_results
    repeated_state = json.loads(Path(repeated.logger.state_path).read_text(encoding="utf-8"))
    assert "PUBLISH_CONFLICT" in repeated_state["error"]
    assert repeated_state["cascade_runtime"]["cascade_original_expected_hashes"] == \
        child_state["cascade_runtime"]["cascade_original_expected_hashes"]


def test_process_crash_during_submit_blocks_restart(tmp_path, monkeypatch):
    definition = CascadeDefinition("unknown-crash", steps=[_text_step("První")])
    first = _worker(definition, tmp_path)
    client = _client()
    def crash_after_durable_index(payload):
        pointer = json.loads(Path(first._runtime_path()).read_text(encoding="utf-8"))
        canonical = json.loads(Path(first.logger.state_path).read_text(encoding="utf-8"))
        assert pointer["run_id"] == first.cfg.run_id
        assert pointer["runtime_identity"] == canonical["runtime_identity"] == first._runtime_identity()
        assert isinstance(canonical["cascade_runtime"], dict)
        raise ProcessCrash()

    client.create_response.side_effect = crash_after_durable_index
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    with pytest.raises(ProcessCrash):
        first.execute()
    restarted = _worker(definition, tmp_path)
    errors, details, events, results = observe(restarted)
    restarted.execute()
    assert errors and details and not results
    assert events[-1].state == "submission_unknown"
    assert client.create_response.call_count == 1


def test_first_step_recovers_primary_from_canonical_state_without_snapshot(tmp_path, monkeypatch):
    step = _text_step("První")
    definition = CascadeDefinition("primary-crash", steps=[step])
    first = _worker(definition, tmp_path)
    client = _client()
    client.create_response.return_value = _response("resp_primary", step.outputs[0], "hotovo")
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    monkeypatch.setattr(first, "_process_deterministic_output", Mock(side_effect=ProcessCrash()))
    with pytest.raises(ProcessCrash):
        first.execute()
    assert not first._read_runtime_state()["cache"]["primary_responses"]
    definition.run_from_step_id = step.id
    child = _worker(definition, tmp_path)
    errors, _, _, results = observe(child)
    child.execute()
    assert not errors and results
    assert client.create_response.call_count == 1


def test_resume_after_inherited_dynamic_input_verifies_relocated_signature(tmp_path, monkeypatch):
    definition = legacy_definition()
    third = _text_step("Závěr")
    definition.steps.append(third)
    client = _client()
    client.upload_file.return_value = {"id": "file_one"}
    not_sent = RuntimeError("Selhání před odesláním posledního kroku")
    not_sent.request_sent = False
    client.create_response.side_effect = [
        manifest_response("source.txt"), manifest_response("result.txt"),
        not_sent,
    ]
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    first = _worker(definition, tmp_path)
    errors, _, _, results = observe(first)
    first.execute()
    assert errors and not results
    definition.run_from_step_id = third.id
    child = _worker(definition, tmp_path)
    child_errors, _, _, child_results = observe(child)
    client.create_response.side_effect = None
    client.create_response.return_value = _response("resp_four", third.outputs[0], "nový závěr")
    child.execute()
    assert not child_errors and child_results
    assert client.create_response.call_count == 4


def test_changed_staging_blocks_recovery_before_request(tmp_path, monkeypatch):
    definition = legacy_definition()
    first = _worker(definition, tmp_path)
    client = _client()
    client.upload_file.return_value = {"id": "file_one"}
    client.create_response.side_effect = [manifest_response("source.txt"), manifest_response("result.txt")]
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    first.execute()
    snapshot = json.loads(Path(first.logger.state_path).read_text(encoding="utf-8"))["cascade_runtime"]
    source = Path(first.logger.paths.run_dir) / snapshot["cascade_staged_files"]["source.txt"]["staged_path"]
    source.write_text("pozměněný obsah", encoding="utf-8")
    definition.run_from_step_id = definition.steps[1].id
    child = _worker(definition, tmp_path)
    errors, _, _, results = observe(child)
    child.execute()
    assert errors and not results
    assert client.create_response.call_count == 2


def test_canonical_identity_mismatch_blocks_derived_cache(tmp_path, monkeypatch):
    step = _text_step("První")
    definition = CascadeDefinition("evidence", steps=[step])
    first = _worker(definition, tmp_path)
    client = _client()
    client.create_response.return_value = _response("resp_one", step.outputs[0], "hotovo")
    monkeypatch.setattr("kajovo.core.cascade_pipeline.OpenAIClient", lambda *a, **kw: client)
    first.execute()
    state_path = Path(first.logger.state_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["runtime_identity"]["project"] = "cizí projekt"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    child = _worker(definition, tmp_path)
    errors, details, events, results = observe(child)
    child.execute()
    assert errors and details and not results and events[-1].state == "failed"
    assert client.create_response.call_count == 1
