"""Git operace nad dočasnými lokálními repozitáři bez sítě."""

import subprocess

import pytest

from kajovo.core.project_git import ProjectGit, allowed_file


@pytest.mark.parametrize("path", [".env", "secret.pem", "kajovo_settings.json",
                                   "Build/lib/a.py", "Build/Kajovo/app.exe", "Build/bdist.test/a"])
def test_sensitive_and_generated_files_are_excluded(path):
    assert not allowed_file(path)


def test_repository_lifecycle_and_conflict_guard(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = ProjectGit(root)
    assert not service.snapshot()["repository"]
    assert service.init()["repository"]
    service.command("config", "user.name", "Offline test")
    service.command("config", "user.email", "test@example.invalid")
    target = root / "test.txt"
    target.write_text("původní", encoding="utf-8")
    service.command("add", ".")
    service.command("commit", "-m", "výchozí")
    branch = service.branch()
    text, digest = service.read_file("test.txt")
    assert text == "původní"
    service.milestone("first")
    service.write_file("test.txt", "nové", digest)
    with pytest.raises(ValueError, match="změnil"):
        service.write_file("test.txt", "přepsat", digest)
    with pytest.raises(ValueError, match="změny"):
        service.restore("first")
    diff, checksum = service.read_file("test.txt", "first")
    assert "nové" in diff and checksum is None
    service.command("add", "test.txt")
    service.command("commit", "-m", "změna")
    service.restore("first")
    assert target.read_text(encoding="utf-8") == "původní"
    assert service.branch() == branch
    service.remove_milestone("first")
    assert service.snapshot()["tags"] == []
    service.remove_repository()
    assert target.exists() and not service.snapshot()["repository"]


def test_sync_to_local_bare_repository_only(tmp_path):
    service = ProjectGit(tmp_path)
    service.init()
    service.command("config", "user.name", "Offline test")
    service.command("config", "user.email", "test@example.invalid")
    service.command("add", ".gitignore")
    service.command("commit", "-m", "výchozí")
    remote = tmp_path / "remote.git"
    service.command("init", "--bare", str(remote))
    service.set_remote(str(remote))
    service.set_remote(str(remote))
    service.synchronize(push=True)
    service.synchronize()
    (tmp_path / ".env").write_text("TEST=not-a-secret", encoding="utf-8")
    service.command("add", "-f", ".env")
    with pytest.raises(ValueError, match="citlivé"):
        service.synchronize(push=True)


def test_invalid_repository_and_editor_operations(tmp_path):
    with pytest.raises(ValueError):
        ProjectGit(tmp_path / "missing")
    service = ProjectGit(tmp_path)
    for remote in ("", "--invalid", "bad\naddress"):
        with pytest.raises(ValueError):
            service.set_remote(remote)
    with pytest.raises(ValueError):
        service.remove_repository()
    with pytest.raises(ValueError):
        service.read_file(".env")
    with pytest.raises(ValueError):
        service.write_file("test.txt", "text", "")
    with pytest.raises(subprocess.CalledProcessError):
        service.command("rev-parse", "HEAD")
    service.init()
    child = tmp_path / "child"
    child.mkdir()
    with pytest.raises(ValueError, match="kořen"):
        ProjectGit(child).snapshot()



def test_milestone_restore_rejects_ambiguous_json_metadata(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = ProjectGit(root)
    service.init()
    service.command("config", "user.name", "Offline test")
    service.command("config", "user.email", "test@example.invalid")
    target = root / "tracked.txt"
    target.write_text("data", encoding="utf-8")
    service.command("add", ".")
    service.command("commit", "-m", "base")
    service.milestone("strict-meta")
    commit = service.text(
        "rev-parse", "refs/tags/strict-meta^{commit}"
    ).strip()
    service._milestone_meta_path("strict-meta").write_text(
        (
            '{"version":2,"version":2,'
            f'"snapshot_commit":"{commit}"'
            "}"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Metadata milníku"):
        service.restore("strict-meta")
