"""Read-only historická evidence a obnova bez opakování odmítnuté práce."""

import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from kajovo.core.openai_client import OpenAIError
from kajovo.core.recoverable_artifacts import artifact_path, save_artifact
from kajovo.core.recovery import recover_run
from kajovo.core.response_journal import ResponseJournal
from kajovo.core.run_bundle import LegacyRunAdapter
from kajovo.core.runlog import RunLogger, find_last_incomplete_run


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def file_snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_legacy_adapter_preserves_raw_evidence_without_inventing_provenance(tmp_path):
    root = tmp_path / "RUN_LEGACY"
    write_json(root / "run_state.json", {
        "created_at": 0, "completed_at": 60, "status": "partial", "batch_id": "batch_old",
        "ui_state": {"project": "Původní projekt", "mode": "GENERATE", "prompt": "Přesné zadání",
                     "model": "model-one", "model_a1": "model-one", "model_a2": "model-two"},
    })
    request = {"payload": {"input": "Přesný vstup"}}
    response = {"id": "resp_old", "status": "completed", "output_text": "Přesný výstup"}
    write_json(root / "requests" / "old.json", request)
    write_json(root / "responses" / "old.json", response)
    write_json(root / "responses" / "ignored.json", [])
    (root / "requests" / "nested").mkdir()
    events = [
        {"type": "api.trace", "ts": 0, "data": {"response_id": "resp_old"}},
        {"event_type": "explicit", "sequence": 19, "event_id": "known", "severity": "warning"},
    ]
    (root / "events.jsonl").write_text(
        "\n\npoškozený řádek\n[]\n" + "\n".join(json.dumps(row) for row in events), encoding="utf-8")
    before = file_snapshot(root)

    adapter = LegacyRunAdapter(root)
    record = adapter.run_record()
    assert record["legacy"] and record["project"] == "Původní projekt"
    assert record["model_summary"] == ["model-one", "model-two"]
    assert record["created_at"] == "1970-01-01T00:00:00+00:00"
    assert record["finished_at"] == "1970-01-01T00:01:00+00:00"
    assert record["related_batch_ids"] == ["batch_old"]
    normalized = adapter.events()
    assert len(normalized) == 2
    assert normalized[0]["event_type"] == "api.trace"
    assert normalized[0]["sequence"] == 1
    assert normalized[0]["severity"] == "unknown"
    assert normalized[0]["step_id"] == normalized[0]["event_id"] == ""
    assert normalized[1]["sequence"] == 19 and normalized[1]["event_id"] == "known"
    assert normalized[1]["severity"] == "warning" and normalized[1]["timestamp"] == ""
    assert adapter.requests()[0]["full_payload"] == request
    restored = adapter.responses()[0]
    assert restored["full_response"] == response
    assert restored["response_id"] == "resp_old" and restored["output_text"] == "Přesný výstup"
    assert restored["response_record_id"] == restored["step_id"] == ""
    artifacts = adapter.artifacts()
    assert {row["path_in_bundle"] for row in artifacts} == {
        "requests/old.json", "responses/old.json", "responses/ignored.json"}
    for row in artifacts:
        raw = before[row["path_in_bundle"]]
        assert row["sha256"] == hashlib.sha256(raw).hexdigest()
        assert row["size_bytes"] == len(raw)
        assert row["artifact_id"] == ""
    assert adapter.steps() == adapter.checkpoints() == adapter.lineage() == adapter.validations() == []
    assert adapter.integrity()["status"] == "legacy"
    assert file_snapshot(root) == before


def test_find_incomplete_run_skips_corrupt_terminal_and_unrelated_entries(tmp_path):
    assert find_last_incomplete_run(str(tmp_path / "absent")) is None
    write_json(tmp_path / "OTHER_999" / "run_state.json", {"status": "running"})
    write_json(tmp_path / "RUN_005" / "run_state.json", {"status": "completed"})
    corrupt = tmp_path / "RUN_004" / "run_state.json"
    corrupt.parent.mkdir()
    corrupt.write_text("{neúplný JSON", encoding="utf-8")
    write_json(tmp_path / "RUN_003" / "run_state.json", {"status": "response_pending", "created_at": 3})
    write_json(tmp_path / "RUN_002" / "run_state.json", {"status": "running", "created_at": 2})
    (tmp_path / "RUN_006").write_text("soubor není běh", encoding="utf-8")
    before = file_snapshot(tmp_path)
    assert find_last_incomplete_run(str(tmp_path)) == "RUN_003"
    assert file_snapshot(tmp_path) == before
    write_json(tmp_path / "RUN_003" / "run_state.json", {"status": "cancelled"})
    write_json(tmp_path / "RUN_002" / "run_state.json", {"status": "closed"})
    assert find_last_incomplete_run(str(tmp_path)) is None


@pytest.mark.parametrize("status_code", [400, 429])
def test_definitive_submit_rejection_is_durable_and_cannot_be_replayed(tmp_path, status_code):
    logger = RunLogger(str(tmp_path), "RUN_REJECTED")
    journal = ResponseJournal(logger)
    payload = {"model": "gpt-4o-mini", "input": "Testovací vstup"}
    client = Mock()
    error = OpenAIError("Požadavek odmítnut")
    error.status_code = status_code
    client.create_response.side_effect = error
    callbacks = {"stopped": lambda: False, "cancelled": lambda: False, "progress": Mock()}
    with pytest.raises(OpenAIError) as caught:
        journal.execute(client, payload, **callbacks)
    assert caught.value is error
    assert "response_pending" not in json.loads(Path(logger.state_path).read_text("utf-8"))
    replay = ResponseJournal(logger)
    entry, = replay.entries.values()
    assert entry["status"] == "rejected" and "id" not in entry
    assert replay.confirmed_id(payload) == ""
    before = file_snapshot(Path(logger.paths.run_dir))
    with pytest.raises(RuntimeError, match="nový běh"):
        replay.execute(client, payload, **callbacks)
    client.create_response.assert_called_once()
    client.retrieve_response.assert_not_called()
    client.cancel_response.assert_not_called()
    assert file_snapshot(Path(logger.paths.run_dir)) == before


@pytest.mark.parametrize("operation", ["read", "save"])
def test_corrupted_content_addressed_artifact_is_never_replaced(tmp_path, operation):
    value = {"prompt": "Přesné zadání"}
    path = Path(save_artifact(tmp_path, "state/ui_state", value))
    path.write_text("poškozený obsah", encoding="utf-8")
    before = file_snapshot(tmp_path)
    with pytest.raises(ValueError, match="hash|poškozen"):
        if operation == "read":
            artifact_path(tmp_path, "state/ui_state")
        else:
            save_artifact(tmp_path, "state/ui_state", value)
    assert file_snapshot(tmp_path) == before


@pytest.mark.parametrize("operation", ["read", "save"])
def test_unknown_artifact_index_version_blocks_access_without_rewriting_index(tmp_path, operation):
    write_json(tmp_path / "artifacts" / "index.json", {"version": 999, "entries": {}})
    index = tmp_path / "artifacts" / "index.json"
    before = index.read_bytes()
    with pytest.raises(ValueError, match="verze indexu"):
        if operation == "read":
            artifact_path(tmp_path, "state/ui_state")
        else:
            save_artifact(tmp_path, "state/ui_state", {"prompt": "zadání"})
    assert index.read_bytes() == before


def test_recovery_does_not_replace_missing_canonical_state_with_legacy_request(tmp_path):
    root = tmp_path / "RUN_MISSING_STATE"
    write_json(root / "requests" / "old.json", {"ui_state": {"prompt": "náhradní zadání"}})
    save_artifact(root, "state/ui_state", {"prompt": "kanonické zadání"})
    before = file_snapshot(root)
    with pytest.raises(ValueError, match="Chybí stav nového běhu"):
        recover_run(tmp_path, root.name)
    assert file_snapshot(root) == before


def test_legacy_manifest_recovers_structure_without_claiming_a_response(tmp_path):
    root = tmp_path / "RUN_MANIFEST"
    ui = {"mode": "GENERATE", "prompt": "Původní zadání"}
    files = [{"path": "main.py", "purpose": "Vstup aplikace"}]
    write_json(root / "run_state.json", {"ui_state": ui})
    write_json(root / "manifests" / "resume_structure.json", {"resume_files": files})
    before = file_snapshot(root)
    restored_ui, response_id, restored_files = recover_run(tmp_path, root.name)
    assert restored_ui == ui and restored_files == files
    assert response_id is None
    assert file_snapshot(root) == before



def test_find_incomplete_run_skips_ambiguous_duplicate_key_state(tmp_path):
    ambiguous = tmp_path / "RUN_999" / "run_state.json"
    ambiguous.parent.mkdir(parents=True)
    ambiguous.write_text(
        '{"status":"running","status":"completed"}',
        encoding="utf-8",
    )
    write_json(
        tmp_path / "RUN_998" / "run_state.json",
        {"status": "response_pending", "created_at": 998},
    )
    assert find_last_incomplete_run(str(tmp_path)) == "RUN_998"
