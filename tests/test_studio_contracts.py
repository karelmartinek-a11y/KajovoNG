"""Regrese kontraktů, které zůstávají nezávislé na odstraněném legacy UI."""

import json
import pytest

from test_output_chunks import raw

from kajovo.core.batch_completion import import_bundle


def test_invalid_bundle_never_partially_overwrites_files(tmp_path):
    target = tmp_path / "first.txt"
    target.write_text("original", encoding="utf-8")
    result = import_bundle(
        raw({
            "contract": "C_FILES_ALL",
            "files": [
                {"path": "first.txt", "content": "changed"},
                {"path": "second.txt", "content": 123},
            ],
        }),
        str(tmp_path),
    )
    assert result["errors"] and not result["written"]
    assert target.read_text(encoding="utf-8") == "original"


def test_recovery_uses_events_and_related_structure(tmp_path):
    from kajovo.core.recovery import recover_run

    current = tmp_path / "run"
    (current / "requests").mkdir(parents=True)
    (current / "requests" / "01.json").write_text(
        json.dumps({"ui_state": {"out_dir": str(tmp_path / "OUT"), "project": "demo"}})
    )
    (current / "events.jsonl").write_text(
        json.dumps({"type": "api.trace", "data": {"action": "complete", "response_id": "resp_latest"}})
        + "\n"
    )
    related = tmp_path / "related"
    (related / "manifests").mkdir(parents=True)
    (related / "run_state.json").write_text(json.dumps({"out_dir": str(tmp_path / "OUT")}))
    (related / "manifests" / "resume_structure.json").write_text(
        json.dumps({"resume_files": [{"path": "main.py"}], "resume_prev_id": "resp_old"})
    )
    ui, previous, files = recover_run(tmp_path, "run")
    assert ui["project"] == "demo"
    assert previous == "resp_latest"
    assert files == [{"path": "main.py"}]

    (current / "events.jsonl").write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="poškozený"):
        recover_run(tmp_path, "run")


def test_recovery_saved_map_rejects_unsafe_paths(tmp_path):
    from kajovo.core.recovery import recover_run

    current = tmp_path / "run"
    (current / "requests").mkdir(parents=True)
    (current / "manifests").mkdir()
    (current / "requests" / "01.json").write_text(
        json.dumps({"ui_state": {"out_dir": str(tmp_path / "OUT")}})
    )
    (current / "manifests" / "01_out_saved_map.json").write_text(
        json.dumps({"saved": [{"path": "../escape"}, {"path": "valid.py"}]})
    )
    assert recover_run(tmp_path, "run")[2] == [{"path": "valid.py", "purpose": ""}]
