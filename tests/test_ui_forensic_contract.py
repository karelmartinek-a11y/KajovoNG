from pathlib import Path

from kajovo.studio.components import COLORS, Form, action, caption, panel
from kajovo.studio.ui_audit import audit_studio

ROOT = Path(__file__).resolve().parents[1]


def test_design_system_has_semantic_component_database():
    for key in (
        "canvas",
        "surface",
        "border",
        "text",
        "muted",
        "primary",
        "success",
        "warning",
        "danger",
        "focus",
    ):
        assert key in COLORS
    assert callable(action)
    assert callable(caption)
    assert callable(panel)
    assert Form.__name__ == "Form"


def test_ui_audit_covers_every_studio_module():
    result = audit_studio(ROOT)
    expected = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "kajovo" / "studio").glob("*.py")
        if path.name != "ui_audit.py"
    }
    actual = {module["source"] for module in result["modules"]}
    assert actual == expected
    assert result["totals"]["controls"] > 0
    assert result["totals"]["connections"] > 0
    assert result["totals"]["methods"] > 0


def test_ui_explicit_self_bindings_resolve_to_real_methods():
    result = audit_studio(ROOT)
    inherited_qt_actions = {
        "self.accept",
        "self.reject",
        "self.hide",
        "self.show",
        "self.close",
        "self.deleteLater",
    }
    unresolved = [
        (module["source"], issue)
        for module in result["modules"]
        for issue in module["unresolved"]
        if issue["target"] not in inherited_qt_actions
    ]
    assert unresolved == []


def test_active_ui_has_no_stale_redaction_claims():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "kajovo" / "studio").glob("*.py"))
    )
    assert "redakce známých tajných polí je vždy aktivní" not in source.lower()


def test_real_feature_installers_are_wired_into_application_entrypoint(qtbot, tmp_path):
    entry = (ROOT / "kajovo" / "app" / "main.py").read_text(encoding="utf-8")
    assert "window = create_window(settings, api_key=api_key)" in entry
    from kajovo.app.main import create_window
    from kajovo.core.config import AppSettings
    from kajovo.studio.history import HistoryPage
    from kajovo.studio.photos import PhotosPage

    window = create_window(
        AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache"))
    )
    qtbot.addWidget(window)
    assert isinstance(window.pages["history"], HistoryPage)
    assert isinstance(window.pages["photos"], PhotosPage)
