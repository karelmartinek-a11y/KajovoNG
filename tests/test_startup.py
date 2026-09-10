"""Spouštěč kontroluje a opravuje závislosti bez skutečných instalací v testech."""

import importlib.util
from importlib import metadata
from pathlib import Path

import pytest


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "scripts" / "start_app.py"
    spec = importlib.util.spec_from_file_location("start_app_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.sys, "prefix", str(tmp_path / ".venv"))
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["example>=2,<3"]\n', encoding="utf-8"
    )
    return module


def test_requirement_versions_missing_and_markers(launcher, monkeypatch):
    def version(name):
        if name == "absent":
            raise metadata.PackageNotFoundError(name)
        return "2.1"

    monkeypatch.setattr(launcher.metadata, "version", version)
    assert launcher.missing_requirements([
        "good>=2,<3", "old>=3", "absent", 'ignored; python_version < "3.0"'
    ]) == ["old>=3", "absent"]


@pytest.mark.parametrize("missing,consistent,install_code,expected", [
    (False, True, 0, 0),
    (True, True, 0, 0),
    (False, False, 0, 0),
    (True, True, 1, 1),
])
def test_prepare_repairs_only_when_needed(
    launcher, monkeypatch, missing, consistent, install_code, expected
):
    calls = []
    checks = iter([0 if consistent else 1, 0])
    requirements = iter([["example>=2,<3"] if missing else [], []])
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: next(requirements))

    def run(*args):
        calls.append(args)
        if args == ("-m", "pip", "check"):
            return next(checks)
        return install_code if "install" in args else 0

    monkeypatch.setattr(launcher, "run", run)
    assert launcher.prepare() == expected
    installs = [call for call in calls if "install" in call]
    assert installs == ([] if not missing and consistent else [
        ("-m", "pip", "install", "example>=2,<3")
    ])


def test_unresolved_conflict_blocks_start(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])
    monkeypatch.setattr(launcher, "run", lambda *args: int(args[-1] == "check"))
    assert launcher.prepare() == 1


@pytest.mark.parametrize("result,check_only,launched", [(1, False, False), (0, True, False), (0, False, True)])
def test_launch_requires_success(launcher, monkeypatch, result, check_only, launched):
    calls = []
    monkeypatch.setattr(launcher.sys, "argv", ["start_app.py"] + (["--check-only"] if check_only else []))
    monkeypatch.setattr(launcher, "prepare", lambda: result)
    monkeypatch.setattr(launcher, "run", lambda *args: calls.append(args) or 0)
    assert launcher.main() == result
    assert calls == ([("-m", "kajovo.app.main")] if launched else [])


def test_missing_pip_bootstrapped(launcher, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])

    def run(*args):
        calls.append(args)
        return int(args == ("-m", "pip", "--version"))

    monkeypatch.setattr(launcher, "run", run)
    assert launcher.prepare() == 0
    assert ("-m", "ensurepip", "--upgrade") in calls
