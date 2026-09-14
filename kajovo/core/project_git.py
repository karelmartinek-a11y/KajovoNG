"""Lokální Git a textové soubory bez závislosti na grafickém rozhraní."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .utils import atomic_write_text, safe_join_under_root


EXCLUDED = {".git", ".venv", "venv", "node_modules", "LOG", "cache", "__pycache__", "dist", ".pytest_cache", ".ruff_cache"}


def allowed_file(relative):
    path = Path(relative)
    if any(part in EXCLUDED for part in path.parts):
        return False
    if path.parts[:2] in {("Build", "lib"), ("Build", "Kajovo")}:
        return False
    if len(path.parts) > 1 and path.parts[0] == "Build" and path.parts[1].startswith("bdist."):
        return False
    return not (path.name.startswith(".env") or path.name == "kajovo_settings.json" or path.suffix.lower() in {".pem", ".key", ".db", ".sqlite"})


class ProjectGit:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Adresář projektu neexistuje.")

    def command(self, *arguments, required=True):
        result = subprocess.run(["git", *arguments], cwd=self.root, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=120,
                                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if required and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, ["git", *arguments], result.stdout, result.stderr)
        return result

    def text(self, *arguments):
        return self.command(*arguments).stdout

    def snapshot(self):
        probe = self.command("rev-parse", "--show-toplevel", required=False)
        if probe.returncode:
            return {"root": str(self.root), "repository": False, "status": "Adresář nemá repozitář Git.", "files": [], "tags": [], "remote": ""}
        actual = Path(probe.stdout.strip()).resolve()
        if actual != self.root:
            raise ValueError(f"Vyberte kořen repozitáře: {actual}")
        remote = self.command("remote", "get-url", "origin", required=False)
        paths = self.text("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
        return {"root": str(self.root), "repository": True,
                "status": self.text("status", "--short", "--branch"),
                "files": sorted({path for path in paths if path and allowed_file(path)}),
                "tags": self.text("tag", "--sort=-creatordate").splitlines(),
                "remote": remote.stdout.strip() if remote.returncode == 0 else ""}

    def init(self):
        self.command("init")
        path = self.root / ".gitignore"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        rules = sorted({f"{value}/" for value in EXCLUDED - {".git"}} | {".env*", "*.pem", "*.key", "kajovo_settings.json"})
        missing = [rule for rule in rules if rule not in old.splitlines()]
        if missing:
            atomic_write_text(str(path), old.rstrip() + "\n" + "\n".join(missing) + "\n")
        return self.snapshot()

    def set_remote(self, address):
        if not address.strip() or address.startswith("-") or "\n" in address:
            raise ValueError("Vyplňte platnou adresu vzdáleného repozitáře.")
        found = self.command("remote", "get-url", "origin", required=False).returncode == 0
        self.command("remote", "set-url" if found else "add", "origin", address)
        return self.snapshot()

    def branch(self):
        return self.text("symbolic-ref", "--quiet", "--short", "HEAD").strip()

    def synchronize(self, push=False):
        if push:
            tracked = self.text("ls-files", "-z").split("\0")
            if any(path and not allowed_file(path) for path in tracked):
                raise ValueError("Repozitář sleduje vyloučené nebo citlivé soubory; před odesláním je vyřaďte.")
            self.command("push", "-u", "origin", self.branch())
        else:
            self.command("pull", "--ff-only", "origin", self.branch())
        return self.snapshot()

    def milestone(self, name):
        self.command("check-ref-format", "refs/tags/" + name)
        self.command("rev-parse", "--verify", "HEAD")
        changes = self.text("diff", "--binary", "--full-index", "HEAD", "--")
        self.command("tag", "-a", name, "-m", name)
        gitdir = Path(self.text("rev-parse", "--absolute-git-dir").strip())
        archive = gitdir / "kajovo_milestones"
        archive.mkdir(exist_ok=True)
        atomic_write_text(str(archive / (hashlib.sha256(name.encode()).hexdigest() + ".diff")), changes)
        return self.snapshot()

    def restore(self, name):
        branch = self.branch()
        if self.text("status", "--porcelain").strip():
            raise ValueError("Před obnovou milníku uložte nebo commitněte všechny místní změny.")
        self.command("restore", "--source", "refs/tags/" + name, "--staged", "--worktree", "--", ".")
        if self.branch() != branch:
            raise RuntimeError("Po obnově neodpovídá aktivní větev původní větvi.")
        return self.snapshot()

    def remove_milestone(self, name):
        self.command("tag", "-d", "--", name)
        return self.snapshot()

    def remove_repository(self):
        directory = self.root / ".git"
        if directory.is_symlink() or not directory.is_dir() or directory.resolve().parent != self.root:
            raise ValueError("Odstranit lze pouze vlastní adresář .git vybraného projektu.")
        if Path(self.text("rev-parse", "--absolute-git-dir").strip()).resolve() != directory.resolve():
            raise ValueError("Úložiště Git nepatří přímo vybranému projektu.")
        # Ověření obsahu i cíle těsně před odstraněním vlastní historie.
        if not (directory / "HEAD").is_file() or not (directory / "objects").is_dir():
            raise ValueError("Adresář nemá očekávanou strukturu repozitáře Git.")
        shutil.rmtree(directory)
        return self.snapshot()

    def read_file(self, relative, tag=""):
        if not allowed_file(relative):
            raise ValueError("Tento soubor je vyloučen z editoru projektu.")
        path = Path(safe_join_under_root(self.root, relative))
        if tag:
            return self.text("diff", "refs/tags/" + tag, "--", relative), None
        if path.stat().st_size > 8_000_000:
            raise ValueError("Soubor je větší než osm megabajtů; otevřete jej v externím editoru.")
        data = path.read_bytes()
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()

    def write_file(self, relative, text, expected_hash):
        if not allowed_file(relative) or not expected_hash:
            raise ValueError("Soubor nebyl bezpečně načten k úpravě.")
        path = Path(safe_join_under_root(self.root, relative))
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Soubor se od otevření změnil; načtěte aktuální obsah před uložením.")
        atomic_write_text(str(path), text)
        return self.read_file(relative)
