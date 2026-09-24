"""Regrese klonování, odpovědí a atomického exportu historie."""

import hashlib
import json
import stat
import threading
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.studio.history import HistoryPage
from kajovo.studio.history_artifacts import ArtifactGuard
from kajovo.studio.history_overview import human_answer


@pytest.fixture
def clone_page(tmp_path):
    workbench = Mock(_revision=0, widgets={"response_id": Mock()})
    workbench.state.return_value = {"prompt": "Rozpracované zadání"}
    page = SimpleNamespace(
        workbench=workbench, notice=Mock(), activate_workbench=Mock(), generation=0, _clone_generation=0,
        context=SimpleNamespace(api_key="test", operations=Mock(),
                                settings=SimpleNamespace(cache_dir=str(tmp_path / "cache"))),
    )
    page.clone_source = MethodType(HistoryPage.clone_source, page)
    return page


def test_clone_uses_canonical_prompt_not_display_projection(tmp_path, clone_page):
    state = {"ui_state": {"prompt": "Příklad token=abc", "response_id": "old", "mode": "QA",
                          "ssh_password": "neprenosne-heslo", "ssh_key": "cesta-ke-klici"}}
    (tmp_path / "run_state.json").write_text(json.dumps(state), encoding="utf-8")
    page = clone_page
    HistoryPage.clone_source(page, SimpleNamespace(root=tmp_path, run_id="source"),
                             {"ui_state": {"prompt": "Příklad token=[REDACTED]"}})
    page.workbench.apply_state.assert_not_called()
    _, read, receive = page.context.operations.start_read.call_args.args
    receive(read(None))
    cloned = page.workbench.apply_state.call_args.args[0]
    assert cloned["prompt"] == "Příklad token=abc"
    assert cloned["ssh_password"] == ""
    assert cloned["ssh_key"] == "cesta-ke-klici"
    assert "response_id" not in cloned
    assert json.loads((tmp_path / "run_state.json").read_text(encoding="utf-8")) == state


@pytest.mark.parametrize("change", ["form", "selection", "account", "new_clone"])
def test_delayed_clone_does_not_replace_new_context(tmp_path, clone_page, change):
    (tmp_path / "run_state.json").write_text(json.dumps({"ui_state": {"prompt": "Klon"}}), encoding="utf-8")
    adapter = SimpleNamespace(root=tmp_path, run_id="source")
    clone_page.clone_source(adapter, None)
    _, read, receive = clone_page.context.operations.start_read.call_args.args
    value = read(None)
    if change == "form":
        clone_page.workbench._revision += 1
    elif change == "selection":
        clone_page.generation += 1
    elif change == "account":
        clone_page.context.api_key = "jiny-ucet"
    else:
        clone_page.clone_source(adapter, None)
    receive(value)
    clone_page.workbench.apply_state.assert_not_called()
    clone_page.activate_workbench.emit.assert_not_called()


@pytest.mark.parametrize("error", [OSError("Nelze číst"), ValueError("Vadná evidence")])
def test_clone_read_failure_is_visible_without_changing_workbench(tmp_path, clone_page, monkeypatch, error):
    monkeypatch.setattr("kajovo.studio.history.read_state", Mock(side_effect=error))
    clone_page.clone_source(SimpleNamespace(root=tmp_path, run_id="source"), None)
    _, read, receive = clone_page.context.operations.start_read.call_args.args
    receive(read(None))
    clone_page.notice.setText.assert_called_once_with(str(error))
    clone_page.workbench.reset.assert_not_called()


def test_clone_reads_canonical_state_outside_gui(qtbot, tmp_path, clone_page, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from kajovo.studio.operations import Operations

    parent = QWidget()
    qtbot.addWidget(parent)
    clone_page.context.operations = Operations(parent)
    threads = []

    def read(_root):
        threads.append(threading.get_ident())
        return {"ui_state": {"prompt": "Příklad token=abc"}}

    monkeypatch.setattr("kajovo.studio.history.read_state", read)
    record = clone_page.clone_source(SimpleNamespace(root=tmp_path, run_id="source"), None)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == "completed"
    assert threads and threads[0] != threading.get_ident()
    assert clone_page.workbench.apply_state.call_args.args[0]["prompt"] == "Příklad token=abc"


def test_artifact_clone_applies_input_and_lineage_only_after_worker(tmp_path, clone_page):
    source = tmp_path / "source.txt"
    source.write_text("Obsah", encoding="utf-8")
    (tmp_path / "run_state.json").write_text(json.dumps({"ui_state": {"prompt": "Klon"}}), encoding="utf-8")
    clone_page.adapter = SimpleNamespace(root=tmp_path, run_id="source")
    artifact = {"artifact_id": "a1", "reusable": True, "path_in_bundle": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    HistoryPage.clone_with_artifact(clone_page, artifact)
    clone_page.workbench.apply_state.assert_not_called()
    assert not (tmp_path / "cache").exists()
    _, read, receive = clone_page.context.operations.start_read.call_args.args
    receive(read(None))
    cloned = clone_page.workbench.apply_state.call_args.args[0]
    assert (Path(cloned["in_dir"]) / source.name).read_bytes() == source.read_bytes()
    assert clone_page.workbench.pending_lineage["inherited_artifact_ids"] == ["a1"]


@pytest.mark.parametrize("field", ["structured_value", "structured_output", "output_text"])
@pytest.mark.parametrize("value, expected", [
    ({"result": {"status": "ready", "data": {"answer": "Odpověď"}}}, "Odpověď"),
    ({"text": "Starší odpověď"}, "Starší odpověď"),
])
def test_human_answer_reads_current_and_legacy_contracts(field, value, expected):
    if field == "output_text":
        value = json.dumps(value)
    rows = [{"response_id": "r1", "step_id": "QA", field: value}]
    assert human_answer({"responses": rows}, "QA") == expected
    assert human_answer({"responses": rows}, "other") == ""


@pytest.mark.parametrize("failure", ["copy", "replace", "hash", "copymode", None])
def test_artifact_export_preserves_target_on_failure(tmp_path, monkeypatch, failure):
    root = tmp_path / "bundle"
    root.mkdir()
    source = root / "source.bin"
    source.write_bytes(b"novy obsah")
    target = tmp_path / "export.bin"
    target.write_bytes(b"puvodni obsah")
    record = {"path_in_bundle": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    if failure in {"copy", "hash"}:
        def copy(_source, destination):
            Path(destination).write_bytes(b"neuplna kopie")
            if failure == "copy":
                raise OSError("Plny disk")
        monkeypatch.setattr("kajovo.studio.history_artifacts.shutil.copyfile", copy)
    elif failure == "replace":
        monkeypatch.setattr("kajovo.studio.history_artifacts.os.replace", Mock(side_effect=OSError("Zamceno")))
    elif failure == "copymode":
        monkeypatch.setattr("kajovo.studio.history_artifacts.shutil.copymode", Mock(side_effect=OSError("Oprávnění")))
    if failure:
        with pytest.raises((OSError, ValueError)):
            ArtifactGuard(root).export(record, target)
        assert target.read_bytes() == b"puvodni obsah"
    else:
        assert ArtifactGuard(root).export(record, target) == target
        assert target.read_bytes() == source.read_bytes()
    assert not list(tmp_path.glob(".artifact-export-*"))


def test_artifact_export_preserves_existing_target_mode(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    source = root / "source.bin"
    source.write_bytes(b"novy obsah")
    target = tmp_path / "export.bin"
    target.write_bytes(b"puvodni obsah")
    target.chmod(0o640)
    expected_mode = stat.S_IMODE(target.stat().st_mode)
    record = {"path_in_bundle": source.name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}

    ArtifactGuard(root).export(record, target)

    assert target.read_bytes() == b"novy obsah"
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode
    assert not list(tmp_path.glob(".artifact-export-*"))
