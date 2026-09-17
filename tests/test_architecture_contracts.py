from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python_files(*relative_roots: str):
    for relative_root in relative_roots:
        root = ROOT / relative_root
        if not root.exists():
            continue
        yield from root.rglob("*.py")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _violations(paths, forbidden_prefixes: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    for path in paths:
        for imported in _imports(path):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden_prefixes):
                violations.append(f"{path.relative_to(ROOT)} -> {imported}")
    return sorted(violations)


def test_production_ui_does_not_import_legacy_desktop():
    """Produkční app/studio nesmí znovu získat runtime závislost na legacy UI."""
    violations = _violations(
        _python_files("kajovo/app", "kajovo/studio"),
        ("kajovo.desktop",),
    )
    assert not violations, violations


def test_core_does_not_depend_on_ui_packages():
    """Core smí být používán UI vrstvami, nikoliv naopak."""
    violations = _violations(
        _python_files("kajovo/core"),
        ("kajovo.studio", "kajovo.desktop"),
    )
    assert not violations, violations



def test_pipeline_facade_is_qt_free():
    """Legacy název modulu smí zůstat jen jako Qt-free core fasáda."""
    path = ROOT / "kajovo" / "core" / "pipeline.py"
    violations = _violations([path], ("PySide6",))
    assert not violations, violations

def test_new_runs_layer_is_qt_free():
    """Nově extrahovaná doménová run vrstva musí zůstat nezávislá na PySide6."""
    runs = ROOT / "kajovo" / "core" / "runs"
    if not runs.exists():
        return
    violations = _violations(runs.rglob("*.py"), ("PySide6",))
    assert not violations, violations


def test_app_entrypoint_uses_studio_factory():
    main = (ROOT / "kajovo" / "app" / "main.py").read_text(encoding="utf-8")
    assert "kajovo.studio.application" in main
    assert "create_window" in main


def test_distribution_excludes_legacy_desktop_package():
    """Regresní zdroje mohou zůstat v repu, ale nesmějí být součástí instalace/release."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(config["tool"]["setuptools"]["packages"]["find"]["exclude"])
    assert "kajovo.desktop" in excluded
    assert "kajovo.desktop.*" in excluded


def test_compatibility_renderer_targets_production_studio():
    """Historický CLI renderer nesmí znovu renderovat duplicitní desktop implementaci."""
    renderer = ROOT / "scripts" / "render_ui.py"
    imports = _imports(renderer)
    assert "kajovo.desktop" not in imports
    source = renderer.read_text(encoding="utf-8")
    assert "from render_studio import main" in source

def test_cascade_pipeline_is_qt_free():
    """Cascade orchestrace musí zůstat v core bez QThread/Signal závislosti."""
    path = ROOT / "kajovo" / "core" / "cascade_pipeline.py"
    violations = _violations([path], ("PySide6",))
    assert not violations, violations
    source = path.read_text(encoding="utf-8")
    assert "class CascadeRunExecutor" in source
    assert "class CascadeRunWorker" not in source
    assert "QThread" not in source

