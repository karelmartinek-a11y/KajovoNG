from pathlib import Path

from kajovo.desktop.design import COMPONENT_CATALOG, DESIGN_TOKENS
from kajovo.desktop.ui_audit import audit_desktop


ROOT = Path(__file__).resolve().parents[1]


def test_design_system_has_semantic_component_database():
    colors = DESIGN_TOKENS["color"]
    for key in (
        "canvas",
        "surface",
        "border",
        "text",
        "text_muted",
        "sidebar",
        "primary",
        "accent",
        "success",
        "warning",
        "danger",
        "focus",
    ):
        assert key in colors
    assert {"Primary", "Secondary", "Quiet", "Success", "Warning", "Danger"} <= set(
        COMPONENT_CATALOG["button"]
    )
    assert {"Card", "NoticeInfo", "NoticeSuccess", "NoticeWarning", "NoticeDanger"} <= set(
        COMPONENT_CATALOG["surface"]
    )


def test_ui_audit_covers_every_desktop_module():
    result = audit_desktop(ROOT)
    expected = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "kajovo" / "desktop").glob("*.py")
        if path.name != "ui_audit.py"
    }
    actual = {module["source"] for module in result["modules"]}
    assert actual == expected
    assert result["totals"]["controls"] > 100
    assert result["totals"]["connections"] > 50
    assert result["totals"]["methods"] > 100


def test_ui_explicit_self_bindings_resolve_to_real_methods():
    result = audit_desktop(ROOT)
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


def test_active_ui_has_no_stale_paid_preflight_or_redaction_claims():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "kajovo" / "desktop").glob("*.py"))
    )
    assert "žádná samostatná placená zkouška" in source.lower()
    assert "redakce známých tajných polí je vždy aktivní" not in source.lower()
    assert "kanonická evidence běhů se v tomto důvěrném prostředí obsahově nerediguje" in source.lower()


def test_real_feature_installers_are_wired_into_application_entrypoint(qtbot, tmp_path):
    entry = (ROOT / "kajovo" / "app" / "main.py").read_text(encoding="utf-8")
    assert "window = create_window(settings, api_key=api_key)" in entry
    from kajovo.app.main import create_window
    from kajovo.core.config import AppSettings
    from kajovo.studio.history import HistoryPage
    from kajovo.studio.photos import PhotosPage
    window = create_window(AppSettings(log_dir=str(tmp_path / "LOG"), cache_dir=str(tmp_path / "cache")))
    qtbot.addWidget(window)
    assert isinstance(window.pages["history"], HistoryPage)
    assert isinstance(window.pages["photos"], PhotosPage)
