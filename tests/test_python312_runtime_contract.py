from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from PySide6.QtWidgets import QApplication

from kajovo.core.config import AppSettings

ROOT = Path(__file__).resolve().parents[1]


def test_declared_python_contract_includes_312_and_ci_gate():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.12"
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python312-contract:" in workflow
    assert "python-version: '3.12'" in workflow


def test_production_import_contract():
    import kajovo  # noqa: F401
    import kajovo.app.main  # noqa: F401
    import kajovo.core.openai_client  # noqa: F401
    import kajovo.core.run_bundle  # noqa: F401
    import kajovo.core.runs  # noqa: F401
    import kajovo.studio.application  # noqa: F401


def test_headless_studio_factory_smoke(tmp_path):
    from kajovo.studio.application import create_window

    app = QApplication.instance() or QApplication([])
    settings = AppSettings(
        comic_library_dir=str(tmp_path / "COMICS"),
        log_dir=str(tmp_path / "LOG"),
        cache_dir=str(tmp_path / "cache"),
    )
    window = create_window(settings, api_key="")
    try:
        assert window.objectName() == "studio.window"
        assert window.isHidden()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_runtime_is_supported_by_project_when_executed():
    assert sys.version_info >= (3, 12)
