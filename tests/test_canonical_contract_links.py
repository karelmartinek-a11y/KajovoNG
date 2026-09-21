"""Negativní i pozitivní regrese skutečných vazeb, bez placených API volání."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.contracts import ContractError
from kajovo.core.orchestration.contracts import canonical_sha256
from kajovo.core.orchestration.errors import OrchestrationError
from kajovo.core.orchestration.provider_operations import prepare_batch, prepare_provider_request
from kajovo.core.orchestration.publish import commit_publish, prepare_publish, recover_publish_journal
from kajovo.core.orchestration.request_binding import validate_response_work_order
from kajovo.core.orchestration.work_order import freeze_order, work_order_from_mapping
from kajovo.core.response_journal import ResponseJournal
from kajovo.core.runlog import RunLogger
from kajovo.core.safe_config import persist_evidence, redact_evidence
from kajovo.core.structured_output import file_content_format, obj, response_format


def file_order():
    cfg = SimpleNamespace(execution_approval_id="explicit-test-start")
    payload = {"model": "gpt-5.6-luna", "instructions": "Vyrob přesně obsah.", "input": "synthetic", "text": file_content_format()}
    task = {"run_id": "RUN_BINDING", "step_id": "A3", "task_id": "file-task", "stage": "A3", "route": "responses_live", "target_id": "src/main.py", "target_path": "src/main.py", "expected_target_hash": None, "contract_name": "FILE_CONTENT_V1", "schema": payload["text"]["format"]["schema"], "prompt": payload["instructions"], "model": payload["model"], "attempt_no": 1}
    return cfg, task, payload, freeze_order(cfg, task, {"path": "src/main.py"})


@pytest.mark.parametrize("path", ["../outside.py", "/absolute.py", "C:/outside.py", "src\\main.py", "src/../main.py", "CON", "src/main.py "])
def test_work_order_rejects_noncanonical_target(path):
    cfg, task, _, _ = file_order()
    task["target_path"] = path
    with pytest.raises(ValueError):
        freeze_order(cfg, task, {})


def test_missing_expectation_is_not_expected_absence():
    cfg, task, _, order = file_order()
    assert order.expected_target_hash is None
    del task["expected_target_hash"]
    with pytest.raises(ValueError, match="očekávání"):
        freeze_order(cfg, task, {})


@pytest.mark.parametrize("field,value", [("expected_target_hash", "invalid"), ("schema_hash", "invalid"), ("attempt_id", "ATTEMPT-other"), ("attempt_no", True)])
def test_recovered_work_order_enforces_hash_and_attempt_identity(field, value):
    _, _, _, order = file_order()
    raw = order.to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        work_order_from_mapping(raw)


def test_recovered_work_order_rejects_stale_envelope_hash():
    _, _, _, order = file_order()
    with pytest.raises(ValueError, match="order_hash"):
        work_order_from_mapping({**order.to_dict(), "order_hash": "0" * 64})


@pytest.mark.parametrize("fault", ["model", "contract", "schema", "route", "credential"])
def test_request_binding_refuses_mismatch_before_provider_or_database(tmp_path, fault):
    cfg, _, payload, order = file_order()
    if fault == "model":
        payload["model"] = "another-model"
    elif fault == "contract":
        payload["text"]["format"]["name"] = "ANOTHER_CONTRACT"
    elif fault == "schema":
        payload["text"]["format"]["schema"]["properties"]["content"]["description"] = "changed"
    elif fault == "route":
        order = replace(order, route="image_live")
    else:
        payload["api_key"] = "synthetic-private-value"
    client = Mock()
    logger = Mock()
    with pytest.raises(ContractError):
        prepare_provider_request(logger, cfg, client, payload, work_order=order)
    assert client.mock_calls == []
    assert logger.mock_calls == []


def test_valid_wire_contract_is_not_modified():
    _, _, payload, order = file_order()
    before = copy.deepcopy(payload)
    validate_response_work_order(order, payload)
    assert payload == before


def test_batch_identifier_bijection_is_checked_before_effects():
    cfg, _, payload, order = file_order()
    logger = Mock()
    rows = [{"custom_id": "same", "method": "POST", "url": "/v1/responses", "body": payload}] * 2
    with pytest.raises(ContractError, match="custom_id"):
        prepare_batch(logger, cfg, rows, [{}, {}], work_orders={"same": order})
    assert logger.mock_calls == []


def test_persistence_preserves_schema_and_separates_runtime_credentials(tmp_path):
    schema = obj({"token": {"type": "string"}, "password": {"type": "string"}})
    payload = {"model": "gpt-5.6-luna", "input": "Popiš token=length a password=field.", "text": response_format("DOMAIN_FIELDS", schema)}
    log = RunLogger(str(tmp_path / "log"), "RUN_PERSIST", "synthetic")
    path = log.save_json("requests", "canonical", {"payload": payload, "ui_state": {"prompt": payload["input"], "ssh_password": "synthetic-private-password-123"}, "error": "password=synthetic-private-password-123"})
    stored = json.loads(Path(path).read_text(encoding="utf-8"))
    assert stored["payload"] == payload
    assert canonical_sha256(stored["payload"]) == canonical_sha256(payload)
    assert stored["ui_state"]["prompt"] == payload["input"]
    assert "ssh_password" not in stored["ui_state"]
    for artifact in Path(log.paths.run_dir).rglob("*"):
        if artifact.is_file():
            assert b"synthetic-private-password-123" not in artifact.read_bytes()
    assert redact_evidence({"Authorization": "Bearer abcdefghijklmnop"})["Authorization"] == "[REDACTED]"
    assert persist_evidence(stored) == stored




def test_direct_json_schema_evidence_is_not_redacted_by_domain_field_names():
    schema = obj({
        "password": {"type": "string"},
        "token": {"type": "string"},
        "secret": {"type": "string"},
    })
    assert persist_evidence(schema) == schema
    assert canonical_sha256(persist_evidence(schema)) == canonical_sha256(schema)

def test_response_journal_reopens_exact_payload_with_domain_secret_field_names(tmp_path):
    schema = obj({"token": {"type": "string"}})
    payload = {"model": "gpt-5.6-luna", "input": "Popiš token=length.", "text": response_format("DOMAIN_TOKEN", schema)}
    log = RunLogger(str(tmp_path / "log"), "RUN_JOURNAL", "synthetic")
    client = Mock()
    response = {"id": "resp_domain", "status": "completed", "output_text": json.dumps({"token": "length"})}
    client.create_response.return_value = response
    journal = ResponseJournal(log, 2)
    assert journal.execute(client, payload, stopped=lambda: False, cancelled=lambda: False, progress=lambda *_: None)["id"] == "resp_domain"
    restarted_log = RunLogger(str(tmp_path / "log"), "RUN_JOURNAL", "synthetic", resume=True)
    restarted = ResponseJournal(restarted_log, 2)
    assert restarted.has_entry(payload)
    assert restarted.confirmed_id(payload) == "resp_domain"
    assert restarted.execute(client, payload, stopped=lambda: False, cancelled=lambda: False, progress=lambda *_: None)["id"] == "resp_domain"
    client.create_response.assert_called_once()


@pytest.mark.parametrize("kind", ["bible", "story", "script", "storyboard", "continuity"])
def test_comic_text_reuses_completed_response_after_local_restart(tmp_path, kind):
    from test_comic_domain import WorkingClient
    from kajovo.core.comic_service import ComicService
    from kajovo.core.config import AppSettings

    settings = AppSettings(comic_library_dir=str(tmp_path / "comics"), log_dir=str(tmp_path / "log"))
    client = WorkingClient()
    service = ComicService(settings, client)
    project = service.store.project("synthetic")
    identifier = service.new_operation(project, kind, {"revision": 1})
    operation = service.store.get("operations", identifier)
    schema = obj({"text": {"type": "string"}})
    first, _ = service.text_request(operation, "Zpracuj syntetický podklad.", schema, {"source": "test"}, [])
    restarted = ComicService(settings, client)
    second, _ = restarted.text_request(operation, "Zpracuj syntetický podklad.", schema, {"source": "test"}, [])
    assert first == second
    assert len(client.responses) == 1


def publish_fixture(tmp_path):
    run = tmp_path / "run"
    out = tmp_path / "out"
    staged = run / "staging" / "candidate" / "generated"
    staged.mkdir(parents=True)
    out.mkdir()
    (out / "a.txt").write_bytes(b"original\n")
    (staged / "a.txt").write_bytes(b"changed\n")
    row = {"path": "a.txt", "staged_path": "staging/candidate/generated/a.txt", "sha256": hashlib.sha256(b"changed\n").hexdigest()}
    expected = {"a.txt": hashlib.sha256(b"original\n").hexdigest()}
    return run, out, prepare_publish([row], out, expected, run_dir=run)


def test_committed_publication_recovery_detects_foreign_edit(tmp_path):
    run, out, plan = publish_fixture(tmp_path)
    assert commit_publish(plan, run_dir=run)["status"] == "committed"
    (out / "a.txt").write_bytes(b"foreign edit\n")
    with pytest.raises(OrchestrationError, match="CONFLICT"):
        recover_publish_journal(run)
    assert (out / "a.txt").read_bytes() == b"foreign edit\n"


def test_publish_journal_entries_are_bound_to_frozen_plan(tmp_path):
    run, out, plan = publish_fixture(tmp_path)
    commit_publish(plan, run_dir=run)
    path = run / "manifests" / "publish_journal.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["entries"][0]["path"] = "foreign.txt"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(OrchestrationError, match="JOURNAL_INVALID"):
        recover_publish_journal(run)
    assert not (out / "foreign.txt").exists()


def test_corrupt_backup_never_replaces_our_written_target(tmp_path):
    from kajovo.core.orchestration.publish import _restore_old

    run, out, plan = publish_fixture(tmp_path)
    commit_publish(plan, run_dir=run)
    row = plan.to_dict()["entries"][0]
    (run / row["backup_ref"]).write_bytes(b"corrupt backup\n")
    with pytest.raises(OrchestrationError, match="BACKUP_HASH"):
        _restore_old(run, out, row)
    assert (out / "a.txt").read_bytes() == b"changed\n"
