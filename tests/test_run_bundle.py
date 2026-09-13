from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kajovo.core.run_bundle import HistoryIndex, LegacyRunAdapter, RunBundle
from kajovo.core.runlog import RunLogger


def test_run_bundle_records_monotonic_events_and_exact_evidence(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_140920260100_TEST", "demo")
    exact = {
        "model": "gpt-5.6-luna",
        "input": "Authorization Bearer is part of the documented contract",
        "credential_label": "literal-evidence-value",
    }
    path = logger.save_json("requests", "A0_request", {"payload": exact})
    stored = Path(path).read_text(encoding="utf-8")
    assert "literal-evidence-value" in stored
    assert "Authorization Bearer is part of the documented contract" in stored
    logger.event("custom", {"private_field": "exact-private-value", "message": "evidence"})
    events = LegacyRunAdapter(logger.paths.run_dir).events()
    sequences = [event["sequence"] for event in events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert any(event.get("data", {}).get("private_field") == "exact-private-value" for event in events)
    requests = LegacyRunAdapter(logger.paths.run_dir).requests()
    canonical = [record for record in requests if record.get("request_record_id")]
    assert canonical
    assert canonical[-1]["payload_sha256"] == hashlib.sha256(
        json.dumps(
            {"payload": exact},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_response_record_preserves_completed_and_incomplete_states(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_140920260101_TEST", "demo")
    logger.save_json(
        "responses",
        "A1_response",
        {
            "id": "resp_ok",
            "status": "completed",
            "output_text": '{"answer":"ok"}',
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "output_tokens_details": {"reasoning_tokens": 2},
                "input_tokens_details": {"cached_tokens": 3},
            },
        },
    )
    logger.save_json(
        "responses",
        "A2_response",
        {
            "id": "resp_incomplete",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output_text": "partial",
        },
    )
    records = [
        record
        for record in LegacyRunAdapter(logger.paths.run_dir).responses()
        if record.get("response_record_id")
    ]
    assert [record["status"] for record in records] == ["completed", "incomplete"]
    assert records[0]["structured_value"] == {"answer": "ok"}
    assert records[0]["reasoning_tokens"] == 2
    assert records[0]["cached_tokens"] == 3
    assert records[1]["incomplete_reason"] == "max_output_tokens"


def test_binary_artifact_is_self_contained_and_tamper_is_detected(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_140920260102_TEST", "demo")
    source = tmp_path / "photo.bin"
    source.write_bytes(b"\x00\x01exact-bytes")
    artifact = logger.bundle.archive_artifact(
        source,
        role="user_input",
        kind="binary",
        reusable=True,
        reconstruction_role="input",
    )
    archived = Path(logger.paths.run_dir) / artifact["path_in_bundle"]
    assert archived.read_bytes() == source.read_bytes()
    assert artifact["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    logger.update_state({"status": "completed", "completed_at": 1_800_000_000})
    assert logger.bundle.verify_integrity()["valid"] is True
    archived.write_bytes(b"changed")
    result = logger.bundle.verify_integrity()
    assert result["valid"] is False
    assert any("Hash nesouhlasí" in error for error in result["errors"])


def test_checkpoint_validates_required_artifact_and_blocks_missing(tmp_path):
    bundle = RunBundle(tmp_path / "RUN_CP", "RUN_CP", create=True)
    source = tmp_path / "input.txt"
    source.write_text("data", encoding="utf-8")
    artifact = bundle.archive_artifact(
        source, role="user_input", reusable=True, reconstruction_role="input"
    )
    checkpoint = bundle.checkpoint(
        "requirements",
        state_snapshot={"ui_state": {"mode": "QA"}},
        safe_to_continue=True,
        reason="Validovaný vstup.",
        required_artifact_ids=[artifact["artifact_id"]],
    )
    assert bundle.validate_checkpoint(checkpoint["checkpoint_id"])["safe_to_continue"] is True
    archived = bundle.root / artifact["path_in_bundle"]
    archived.unlink()
    with pytest.raises(ValueError, match="artefakt"):
        bundle.validate_checkpoint(checkpoint["checkpoint_id"])


def test_checkpoint_accepts_canonical_response_record(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_140920260103_TEST", "demo")
    logger.save_json(
        "responses",
        "A1_response",
        {"id": "resp_provider", "status": "completed", "output_text": "ok"},
    )
    records = [
        record
        for record in LegacyRunAdapter(logger.paths.run_dir).responses()
        if record.get("response_record_id")
    ]
    checkpoint = logger.bundle.checkpoint(
        "plan",
        state_snapshot={"ui_state": {"mode": "QA"}},
        safe_to_continue=True,
        reason="Plan je uložen.",
        required_response_ids=[records[-1]["response_record_id"]],
    )
    assert logger.bundle.validate_checkpoint(checkpoint["checkpoint_id"])


def test_lineage_never_modifies_source_run(tmp_path):
    source = RunBundle(tmp_path / "RUN_SOURCE", "RUN_SOURCE", create=True)
    source_before = (source.root / "run.json").read_bytes()
    target = RunBundle(tmp_path / "RUN_TARGET", "RUN_TARGET", create=True)
    record = target.record_lineage(
        "RUN_SOURCE", "clone", inherited_configuration={"prompt": "same"}
    )
    assert record["source_run_id"] == "RUN_SOURCE"
    assert record["target_run_id"] == "RUN_TARGET"
    assert (source.root / "run.json").read_bytes() == source_before
    assert target.run_record()["cloned_from_run_id"] == "RUN_SOURCE"


def test_legacy_adapter_does_not_invent_steps_or_checkpoints(tmp_path):
    run = tmp_path / "RUN_130920261200_LEGACY"
    (run / "requests").mkdir(parents=True)
    (run / "responses").mkdir()
    (run / "run_state.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "project": "legacy",
                "ui_state": {"mode": "GENERATE", "prompt": "old"},
            }
        ),
        encoding="utf-8",
    )
    adapter = LegacyRunAdapter(run)
    assert adapter.legacy is True
    assert adapter.steps() == []
    assert adapter.checkpoints() == []
    assert adapter.integrity()["status"] == "legacy"


def test_history_index_is_rebuildable_and_searchable(tmp_path):
    logger = RunLogger(str(tmp_path), "RUN_140920260104_TEST", "hotel")
    logger.update_state(
        {
            "ui_state": {
                "mode": "QA",
                "prompt": "forenzní dotaz",
                "model": "gpt-5.6-luna",
            },
            "status": "running",
        }
    )
    logger.save_json(
        "responses",
        "QA_response",
        {"id": "resp_searchable", "status": "completed", "output_text": "answer"},
    )
    index = HistoryIndex(tmp_path)
    first = index.rebuild()
    assert len(first) == 1
    assert first[0]["run_id"] == logger.run_id
    assert "resp_searchable" in first[0]["search_text"]
    assert "forenzní dotaz" in first[0]["search_text"]
    Path(index.path).unlink()
    second = index.rebuild()
    assert second[0]["run_id"] == first[0]["run_id"]


def test_history_run_explorer_constructs_and_shows_legacy_without_guessing(qtbot, tmp_path):
    from kajovo.desktop.history import ResponseRequestPanel

    run = tmp_path / "RUN_130920261230_LEGACY"
    (run / "requests").mkdir(parents=True)
    (run / "responses").mkdir()
    (run / "run_state.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "project": "legacy",
                "ui_state": {"mode": "QA", "prompt": "old prompt"},
            }
        ),
        encoding="utf-8",
    )
    panel = ResponseRequestPanel(str(tmp_path))
    qtbot.addWidget(panel)
    assert panel.lst_runs.count() == 1
    assert "LEGACY" in panel.lst_runs.item(0).text()
    assert panel.timeline.topLevelItemCount() == 1
    assert panel.timeline.topLevelItem(0).text(1) == "Legacy běh"
    assert not panel.btn_continue.isEnabled()
    assert panel.btn_clone.isEnabled()


def test_history_filters_use_derived_index(qtbot, tmp_path):
    from kajovo.desktop.history import ResponseRequestPanel

    for run_id, project, mode in (
        ("RUN_140920260105_A", "Alpha", "QA"),
        ("RUN_140920260106_B", "Beta", "QFILE"),
    ):
        logger = RunLogger(str(tmp_path), run_id, project)
        logger.update_state(
            {
                "ui_state": {"mode": mode, "prompt": project, "model": "gpt-5.6-luna"},
                "status": "running",
            }
        )
    panel = ResponseRequestPanel(str(tmp_path))
    qtbot.addWidget(panel)
    assert panel.lst_runs.count() == 2
    panel.ed_fulltext.setText("alpha")
    panel.apply_filters()
    assert panel.lst_runs.count() == 1
    assert "Alpha" in panel.lst_runs.item(0).text()
    panel.reset_filters()
    panel.mode_filter.setCurrentText("QFILE")
    panel.apply_filters()
    assert panel.lst_runs.count() == 1
    assert "QFILE" in panel.lst_runs.item(0).text()
