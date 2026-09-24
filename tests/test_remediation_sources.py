"""Regrese schváleného inventáře, přihlašovacích údajů a milníků."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kajovo.core.config import AppSettings, save_settings
from kajovo.core.project_git import ProjectGit
from kajovo.core.runlog import RunLogger


def test_ui_archive_does_not_expand_frozen_inventory(tmp_path):
    source = tmp_path / "in"
    source.mkdir()
    (source / ".env").write_text("NEARCHIVOVAT", encoding="utf-8")
    bundle = Mock()
    bundle.artifacts.return_value = []
    logger = SimpleNamespace(bundle=bundle)
    state = {"ui_state": {"in_dir": str(source)}, "source_pack": {"sources": []}}
    RunLogger._archive_run_inputs(logger, {"ui_state": state["ui_state"]}, state)
    bundle.archive_artifact.assert_not_called()
    assert state["input_archive"]["complete"]


def test_settings_failure_preserves_json_and_rolls_back(monkeypatch, tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"original":true}', encoding="utf-8")
    previous = path.read_bytes()
    writes = []
    monkeypatch.setattr("kajovo.core.config.get_secret", lambda key: "old-" + key)
    def write(key, value):
        writes.append((key, value))
        return value != "fail"
    monkeypatch.setattr("kajovo.core.config.set_secret", write)
    settings = AppSettings()
    settings.smtp.password = "new"
    settings.ssh.password = "fail"
    with pytest.raises(ValueError, match="trvale"):
        save_settings(settings, str(path))
    assert path.read_bytes() == previous
    assert writes[-2:] == [("ssh_password", "old-ssh_password"), ("smtp_password", "old-smtp_password")]


def test_secret_write_requires_readback(monkeypatch):
    from kajovo.core.secret_store import set_secret
    monkeypatch.setattr("keyring.set_password", lambda *args: None)
    monkeypatch.setattr("keyring.get_password", lambda *args: "old")
    monkeypatch.setenv("KAJOVO_SECRET_SMTP_PASSWORD", "old")
    assert set_secret("smtp_password", "new") is False


@pytest.mark.parametrize("legacy", [False, True])
def test_milestone_preserves_excluded_tracked_files(tmp_path, legacy):
    git = ProjectGit(tmp_path)
    git.init()
    git.command("config", "user.name", "Offline")
    git.command("config", "user.email", "offline@example.invalid")
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    (tmp_path / ".env").write_text("old", encoding="utf-8")
    git.command("add", "-f", "a.txt", ".env", ".gitignore")
    git.command("commit", "-m", "base")
    if legacy:
        git.command("tag", "snapshot")
    else:
        git.milestone("snapshot")
        assert ".env" not in git.text("ls-tree", "-r", "--name-only", "snapshot").splitlines()
        metadata = json.loads(git._milestone_meta_path("snapshot").read_text(encoding="utf-8"))
        assert ".env" not in metadata["included_paths"]
    (tmp_path / "a.txt").write_text("second", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    (tmp_path / ".env").write_text("current", encoding="utf-8")
    git.command("add", "-f", "a.txt", "new.txt", ".env")
    git.command("commit", "-m", "update")
    git.restore("snapshot")
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "current"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "first"
    assert not (tmp_path / "new.txt").exists()
