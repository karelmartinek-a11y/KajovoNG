from pathlib import Path
import subprocess

from PySide6.QtGui import QFontDatabase, QImage

from kajovo.core.resources import resource_path


def test_repository_text_is_utf8_without_bom():
    root = Path(__file__).resolve().parents[1]
    names = subprocess.check_output(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root).decode("utf-8").split("\0")
    suffixes = {".py", ".ps1", ".bat", ".sh", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".svg"}
    errors = []
    for name in set(names):
        path = root / name
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        data = path.read_bytes()
        if data.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{name}: BOM")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{name}: není UTF-8")
    assert not errors, errors


def test_bundled_resources_are_readable(qapp):
    assert not QImage(str(resource_path("Kajovo_new.png"))).isNull()
    for name in ("montserrat_regular.ttf", "montserrat_bold.ttf"):
        font_id = QFontDatabase.addApplicationFont(str(resource_path(name)))
        assert font_id >= 0, name
        QFontDatabase.removeApplicationFont(font_id)
