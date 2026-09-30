"""CheckOnly musí prověřit skutečnou konfiguraci a lokální datové hranice."""

import json
from unittest.mock import Mock

import pytest

from test_startup import launcher as launcher_fixture


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    return launcher_fixture.__wrapped__(monkeypatch, tmp_path)


@pytest.mark.parametrize("fault", ["config", "path", "foreign_drive", "resource", "import"])
def test_check_only_rejects_runtime_fault(launcher, monkeypatch, fault):
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])
    monkeypatch.setattr(launcher, "run", lambda *args: 0)
    if fault == "config":
        (launcher.ROOT / "kajovo_settings.json").write_text('{"retry":{"max_attempts":0}}')
    elif fault == "path":
        blocked = launcher.ROOT / "blocked"
        blocked.write_text("user data")
        (launcher.ROOT / "kajovo_settings.json").write_text(json.dumps({"log_dir": str(blocked)}))
    elif fault == "foreign_drive":
        if launcher.sys.platform == "win32":
            pytest.skip("Cizí Windows cesta je regresí pro ne-Windows runtime.")
        (launcher.ROOT / "kajovo_settings.json").write_text(json.dumps({"log_dir": "D:\\kajovong\\LOG"}))
    elif fault == "resource":
        monkeypatch.setattr("kajovo.core.resources.resource_path", lambda name: launcher.ROOT / "absent" / name)
    else:
        # Metadata balíčku existují, import může přesto selhat (DLL/runtime).
        monkeypatch.setattr("importlib.import_module", Mock(side_effect=ImportError("broken runtime")))
    assert launcher.prepare() == 1
    if fault == "path":
        assert blocked.read_text() == "user data"


def test_runtime_sanity_does_not_migrate_or_read_credentials(launcher, monkeypatch):
    from kajovo.core import config
    get_secret = Mock(side_effect=AssertionError("credential access"))
    set_secret = Mock(side_effect=AssertionError("credential write"))
    monkeypatch.setattr(config, "get_secret", get_secret)
    monkeypatch.setattr(config, "set_secret", set_secret)
    path = launcher.ROOT / "kajovo_settings.json"
    raw = '{"smtp":{"password":"legacy-test-only"}}'
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])
    monkeypatch.setattr(launcher, "run", lambda *args: 0)
    assert launcher.prepare() == 0
    assert path.read_text(encoding="utf-8") == raw
    assert get_secret.call_count == set_secret.call_count == 0
