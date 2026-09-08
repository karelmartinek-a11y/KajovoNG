import importlib
import pkgutil
from pathlib import Path
from unittest.mock import patch

import pytest

import kajovo
from kajovo.core.config import AppSettings
from kajovo.ui.mainwindow import MainWindow
from kajovo.ui.progress_dialog import ProgressDialog
from kajovo.ui.response_request_panel import ResponseRequestPanel


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"), db_path=str(tmp_path / "data.sqlite"))
    settings.pricing.auto_refresh_on_start = False
    with patch.object(MainWindow, "_start_pricing_audit_loop"), patch("kajovo.ui.mainwindow.get_secret", return_value=None):
        widget = MainWindow(settings)
    qtbot.addWidget(widget)
    return widget


def test_all_package_modules_import():
    for module in pkgutil.walk_packages(kajovo.__path__, kajovo.__name__ + "."):
        if not module.name.endswith(".__main__"):
            importlib.import_module(module.name)


def test_main_window_controls_and_state(window):
    assert window.tabs.count() == 12
    assert window.sp_temp is not window.sp_default_temp
    window.sp_temp.setValue(0.7)
    window.sp_default_temp.setValue(0.3)
    state = window._gather_state()
    window._apply_state(state)
    assert window.sp_temp.value() == 0.7
    assert window.sp_default_temp.value() == 0.3
    window.chk_in_eq_out.setChecked(True)
    window.ed_in.setText("input-path")
    assert window.ed_out.text() == "input-path"


def test_window_fits_small_screen_and_selects_wrapped_tabs(window, qtbot):
    window.resize(1100, 700)
    window.show()
    qtbot.wait(20)
    assert window.height() <= 700
    window.on_pricing()
    assert window.tabs.currentWidget().widget() is window.tab_pricing
    window._select_tab(window.tab_run)
    assert window.tabs.currentWidget().widget() is window.tab_run


def test_progress_parses_actual_pipeline_message(qtbot):
    dialog = ProgressDialog()
    qtbot.addWidget(dialog)
    dialog.set_status("A3: FILE src/main.py (2/4)")
    assert "2/4" in dialog.lbl_file.text()


@pytest.mark.parametrize("value", ["2026-09-09", "09.09.2026", "09092026"])
def test_date_filter(value):
    assert ResponseRequestPanel._normalize_date_filter(None, value) == "20260909"


def test_rerun_does_not_skip_response_without_written_file(window, tmp_path):
    import json
    run = Path(window.s.log_dir) / "RUN_090920261200_ABCD"
    responses = run / "responses"
    responses.mkdir(parents=True)
    body = {"path": "missing.py", "content": "partial"}
    (responses / "A3_FILE.json").write_text(json.dumps({"output": [{"content": [{"text": json.dumps(body)}]}]}), encoding="utf-8")
    assert window._gather_completed_paths(run.name, str(tmp_path)) == []


def test_git_refresh_does_not_change_remote(window):
    import subprocess
    panel = window.git_panel
    calls = []
    def fake(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    with patch.object(panel, "_is_git_repo", return_value=True), patch.object(panel, "_run_git", side_effect=fake):
        panel.ed_remote.setText("git@github.com:owner/example.git")
        panel._check_sync()
    assert not any(args[:2] in (["remote", "remove"], ["remote", "add"], ["remote", "set-url"]) for args in calls)
