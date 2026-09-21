"""Nový převodník používá skutečné bezpečnostní a převodní funkce."""

from kajovo.studio.converter import ConverterWindow


def test_converter_requires_separate_backup(qtbot, tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    window = ConverterWindow()
    qtbot.addWidget(window)
    window.paths[0].setText(str(source))
    window.backup.setText(str(source))
    assert not window.start_button.isEnabled()


def test_converter_creates_backup_before_normalizing(qtbot, tmp_path):
    source = tmp_path / "source"
    backup = tmp_path / "backup"
    source.mkdir()
    backup.mkdir()
    text = source / "example.txt"
    original = b"\xef\xbb\xbfAhoj\r\n"
    text.write_bytes(original)
    window = ConverterWindow()
    qtbot.addWidget(window)
    window.paths[0].setText(str(source))
    window.backup.setText(str(backup))
    assert window.start_button.isEnabled()
    window.start()
    qtbot.waitUntil(lambda: not window.operations.active, timeout=15000)
    record = next(iter(window.operations.records.values()))
    assert record.error is None
    assert text.read_bytes() == b"Ahoj\r\n"
    assert list(backup.iterdir())
