"""Výchozí macOS bundle nesmí zapisovat relativní runtime data vůči `/`."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from kajovo.app import main
from kajovo.core.config import load_settings


def prepare(qtbot, tmp_path, monkeypatch, frozen, explicit):
    launch = tmp_path / "launch"
    launch.mkdir()
    marker = launch / "archiv.json"
    marker.write_bytes(b'{"archive":true}')
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(main.sys, "platform", "darwin")
    monkeypatch.setattr(main.sys, "frozen", frozen, raising=False)
    monkeypatch.chdir(launch)
    if explicit:
        config = launch / "selected.json"
        config.write_text('{"log_dir":"selected"}', encoding="utf-8")
        monkeypatch.setenv("KAJOVO_SETTINGS_FILE", str(config))
    else:
        monkeypatch.delenv("KAJOVO_SETTINGS_FILE", raising=False)
    app = QApplication.instance()
    monkeypatch.setattr(main, "QApplication", lambda *args: app)
    monkeypatch.setattr(app, "exec", lambda: 0)
    monkeypatch.setattr(main, "_load_fonts", lambda: None)
    monkeypatch.setattr(main, "_load_app_icon", QIcon)
    monkeypatch.setattr(main, "load_settings", lambda: load_settings(resolve_secrets=False))
    monkeypatch.setattr(main, "load_api_key", lambda: "")
    window = QWidget()
    qtbot.addWidget(window)
    return launch, home, marker, window


@pytest.mark.parametrize("frozen,explicit", [(True, False), (True, True), (False, False)])
def test_main_selects_writable_mac_data_root_without_changing_explicit_config(qtbot, tmp_path, monkeypatch, frozen, explicit):
    launch, home, marker, window = prepare(qtbot, tmp_path, monkeypatch, frozen, explicit)
    expected = home / "Library" / "Application Support" / "KajovoNG" if frozen and not explicit else launch

    def create(settings, **kwargs):
        assert Path.cwd() == expected
        assert settings.log_dir == ("selected" if explicit else "LOG")
        output = Path(settings.log_dir)
        output.mkdir()
        (output / "probe").write_bytes(b"runtime")
        return window

    monkeypatch.setattr(main, "create_window", create)
    with pytest.raises(SystemExit) as caught:
        main.main()
    assert caught.value.code == 0
    assert (expected / ("selected" if explicit else "LOG") / "probe").read_bytes() == b"runtime"
    assert marker.read_bytes() == b'{"archive":true}'
    if explicit:
        assert (launch / "selected.json").read_text() == '{"log_dir":"selected"}'
    window.close()


def test_main_exposes_unavailable_mac_data_root_before_loading_credentials(qtbot, tmp_path, monkeypatch):
    _launch, home, _marker, window = prepare(qtbot, tmp_path, monkeypatch, True, False)
    home.mkdir()
    (home / "Library").write_bytes(b"existing file")
    settings, credentials, create, show = Mock(), Mock(), Mock(return_value=window), Mock()
    monkeypatch.setattr(main, "load_settings", settings)
    monkeypatch.setattr(main, "load_api_key", credentials)
    monkeypatch.setattr(main, "create_window", create)
    monkeypatch.setattr("kajovo.studio.components.show_error", show)
    with pytest.raises(SystemExit) as caught:
        main.main()
    assert caught.value.code == 1
    assert isinstance(show.call_args.args[1], OSError)
    settings.assert_not_called()
    credentials.assert_not_called()
    create.assert_not_called()
    assert (home / "Library").read_bytes() == b"existing file"
