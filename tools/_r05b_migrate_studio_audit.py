from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "kajovo" / "desktop" / "ui_audit.py"
TARGET = ROOT / "kajovo" / "studio" / "ui_audit.py"


def build_audit() -> None:
    text = LEGACY.read_text(encoding="utf-8")
    text = text.replace("desktopového UI", "Studio UI")
    text = text.replace(
        '    "page_header",\n    "section_title",\n',
        '    "page_header",\n    "section_title",\n'
        '    "action",\n    "caption",\n    "panel",\n'
        '    "scroll",\n    "actions",\n',
    )
    text = text.replace("def audit_desktop(root: str | Path)", "def audit_studio(root: str | Path)")
    text = text.replace(
        '    desktop = root_path / "kajovo" / "desktop"\n',
        '    studio = root_path / "kajovo" / "studio"\n',
    )
    text = text.replace('sorted(desktop.glob("*.py"))', 'sorted(studio.glob("*.py"))')
    text = text.replace("AST všech kajovo/desktop modulů", "AST všech kajovo/studio modulů")
    text = text.replace("result = audit_desktop(root)", "result = audit_studio(root)")

    old_prefixes = '''            root in core_names
            or expression.startswith("self.client.")
            or expression.startswith("client.")
            or expression.startswith("OpenAIClient")
            or expression.startswith("self.jobs.")
            or expression.startswith("self.log.")
            or expression.startswith("self.bundle.")
'''
    new_prefixes = '''            root in core_names
            or expression.startswith(
                ("self.client.", "client.", "OpenAIClient", "self.jobs.", "self.log.", "self.bundle.")
            )
'''
    if old_prefixes in text:
        text = text.replace(old_prefixes, new_prefixes, 1)
    elif new_prefixes not in text:
        raise RuntimeError("Studio audit backend-call predicate has an unexpected shape")

    helpers = '''

def _callable_parameter_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    parameters = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        parameters.append(node.args.vararg)
    if node.args.kwarg is not None:
        parameters.append(node.args.kwarg)
    return {
        parameter.arg
        for parameter in parameters
        if "Callable" in _name(parameter.annotation)
    }


def _is_callable_value(value: ast.AST, callable_names: set[str]) -> bool:
    if isinstance(value, ast.Lambda):
        return True
    if isinstance(value, ast.Name):
        return value.id in callable_names
    if isinstance(value, ast.BoolOp):
        return bool(value.values) and all(
            _is_callable_value(item, callable_names) for item in value.values
        )
    return False


def _class_declares_callable_target(node: ast.ClassDef, target_name: str) -> bool:
    for statement in node.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        if value is None:
            continue
        is_named_target = any(
            isinstance(target, ast.Name) and target.id == target_name for target in targets
        )
        if (
            is_named_target
            and isinstance(value, ast.Call)
            and _call_name(value) in {"Signal", "pyqtSignal"}
        ):
            return True

    for method in node.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        callable_names = _callable_parameter_names(method)
        for statement in ast.walk(method):
            if isinstance(statement, ast.Assign):
                targets = statement.targets
                value = statement.value
            elif isinstance(statement, ast.AnnAssign):
                targets = [statement.target]
                value = statement.value
            else:
                continue
            if value is None or not _is_callable_value(value, callable_names):
                continue
            if any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == target_name
                for target in targets
            ):
                return True
    return False
'''
    marker = "\ndef audit_studio(root: str | Path) -> dict[str, Any]:\n"
    if marker not in text:
        raise RuntimeError("Studio audit entrypoint marker not found")
    text = text.replace(marker, helpers + marker, 1)

    resolver_marker = "        for base in node.bases:\n"
    resolver_extension = (
        "        if _class_declares_callable_target(node, method):\n"
        "            return True\n"
        "        for base in node.bases:\n"
    )
    if resolver_marker not in text:
        raise RuntimeError("Studio audit inheritance resolver marker not found")
    text = text.replace(resolver_marker, resolver_extension, 1)

    if "audit_desktop" in text or "desktop.glob" in text:
        raise RuntimeError("Studio audit still contains legacy desktop audit ownership")
    TARGET.write_text(text, encoding="utf-8")


def write_inheritance_test() -> None:
    path = ROOT / "tests" / "test_ui_audit_inheritance.py"
    content = '''"""Studio audit resolves inherited methods, signals and callable attributes."""

from kajovo.studio.ui_audit import audit_studio


def _unresolved(result):
    return [
        issue["target"]
        for module in result["modules"]
        for issue in module["unresolved"]
    ]


def test_inherited_handler_is_resolved_but_unknown_handler_is_reported(tmp_path):
    studio = tmp_path / "kajovo" / "studio"
    studio.mkdir(parents=True)
    (studio / "base.py").write_text(
        "class Base:\\n    def known(self):\\n        return True\\n", encoding="utf-8"
    )
    (studio / "page.py").write_text(
        "from .base import Base as Parent\\n"
        "class Page(Parent):\\n"
        "    def build(self):\\n"
        "        button('Known', self.known)\\n"
        "        button('Missing', self.missing)\\n",
        encoding="utf-8",
    )
    assert _unresolved(audit_studio(tmp_path)) == ["self.missing"]


def test_signal_and_typed_callable_attributes_are_valid_connection_targets(tmp_path):
    studio = tmp_path / "kajovo" / "studio"
    studio.mkdir(parents=True)
    (studio / "page.py").write_text(
        "from collections.abc import Callable\\n"
        "class Page:\\n"
        "    changed = Signal()\\n"
        "    def __init__(self, refreshed: Callable[[], None] | None = None):\\n"
        "        self.refreshed = refreshed or (lambda: None)\\n"
        "    def build(self, widget):\\n"
        "        widget.changed.connect(self.changed)\\n"
        "        widget.finished.connect(self.refreshed)\\n",
        encoding="utf-8",
    )
    assert _unresolved(audit_studio(tmp_path)) == []
'''
    path.write_text(content, encoding="utf-8")


def write_forensic_test() -> None:
    path = ROOT / "tests" / "test_ui_forensic_contract.py"
    content = '''from pathlib import Path

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
    source = "\\n".join(
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
'''
    path.write_text(content, encoding="utf-8")


def write_cli() -> None:
    path = ROOT / "scripts" / "audit_ui.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("desktopového UI", "Studio UI")
    text = text.replace(
        "from kajovo.desktop.ui_audit import audit_desktop, write_inventory",
        "from kajovo.studio.ui_audit import audit_studio, write_inventory",
    )
    text = text.replace("audit_desktop(root)", "audit_studio(root)")
    text = text.replace(" – ", " - ")
    if "kajovo.desktop" in text or "audit_desktop" in text:
        raise RuntimeError("audit_ui CLI still depends on legacy desktop")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    build_audit()
    write_inheritance_test()
    write_forensic_test()
    write_cli()
    print("R05-B Studio forensic audit migration prepared.")


if __name__ == "__main__":
    main()
