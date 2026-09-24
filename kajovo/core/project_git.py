"""Lokální Git a textové soubory bez závislosti na grafickém rozhraní."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import json
import tempfile
from pathlib import Path

from .orchestration.contracts import parse_json_strict
from .orchestration.errors import OrchestrationError
from .utils import atomic_write_text, safe_join_under_root


EXCLUDED = {".git", ".venv", "venv", "node_modules", "LOG", "cache", "__pycache__", "dist", ".pytest_cache", ".ruff_cache"}


def allowed_file(relative):
    path = Path(relative)
    parts = tuple(part.casefold() for part in path.parts)
    if any(part in {value.casefold() for value in EXCLUDED} for part in parts):
        return False
    if parts[:2] in {("build", "lib"), ("build", "kajovo")}:
        return False
    if len(parts) > 1 and parts[0] == "build" and parts[1].startswith("bdist."):
        return False
    database_suffixes = (".db", ".sqlite", ".sqlite3")
    name = path.name.lower()
    database = any(
        name.endswith(suffix + sidecar)
        for suffix in database_suffixes
        for sidecar in ("", "-wal", "-shm", "-journal")
    )
    return not (name.startswith(".env") or name == "kajovo_settings.json" or database or path.suffix.lower() in {".pem", ".key"})


class ProjectGit:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Adresář projektu neexistuje.")

    def command(self, *arguments, required=True, extra_env=None):
        env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            **(extra_env or {}),
        }
        result = subprocess.run(["git", *arguments], cwd=self.root, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=120,
                                env=env,
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
        tags = self.text("tag", "--sort=-creatordate").splitlines()
        milestone_types = {}
        for tag in tags:
            meta = self._milestone_meta_path(tag)
            milestone_types[tag] = (
                "snapshot_v2" if meta.is_file() else "legacy_commit_only"
            )
        return {"root": str(self.root), "repository": True,
                "status": self.text("status", "--short", "--branch"),
                "files": sorted({path for path in paths if path and allowed_file(path)}),
                "tags": tags,
                "milestone_types": milestone_types,
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

    def _milestone_dir(self):
        gitdir = Path(self.text("rev-parse", "--absolute-git-dir").strip())
        directory = gitdir / "kajovo_milestones"
        directory.mkdir(exist_ok=True)
        return directory

    def _milestone_meta_path(self, name):
        gitdir = Path(self.text("rev-parse", "--absolute-git-dir").strip())
        return (
            gitdir
            / "kajovo_milestones"
            / (hashlib.sha256(name.encode("utf-8")).hexdigest() + ".json")
        )

    def milestone(self, name):
        self.command("check-ref-format", "refs/tags/" + name)
        head = self.text("rev-parse", "--verify", "HEAD").strip()
        if self.command(
            "show-ref", "--verify", "--quiet", "refs/tags/" + name,
            required=False,
        ).returncode == 0:
            raise ValueError("Milník s tímto názvem již existuje.")

        real_index = Path(
            self.text("rev-parse", "--git-path", "index").strip()
        )
        if not real_index.is_absolute():
            real_index = self.root / real_index
        before_index = (
            hashlib.sha256(real_index.read_bytes()).hexdigest()
            if real_index.is_file()
            else None
        )
        candidates = {
            value
            for value in self.text(
                "ls-files", "--cached", "--others", "--exclude-standard", "-z"
            ).split("\0")
            if value and allowed_file(value)
        }
        tracked = {
            value
            for value in self.text("ls-files", "--cached", "-z").split("\0")
            if value
        }

        with tempfile.TemporaryDirectory(prefix="kajovo_milestone_") as tmp:
            index_path = Path(tmp) / "index"
            env = {
                "GIT_INDEX_FILE": str(index_path),
                "GIT_AUTHOR_NAME": "Kajovo Milestone",
                "GIT_AUTHOR_EMAIL": "milestone@localhost",
                "GIT_COMMITTER_NAME": "Kajovo Milestone",
                "GIT_COMMITTER_EMAIL": "milestone@localhost",
            }
            self.command("read-tree", "--empty", extra_env=env)
            for relative in sorted(candidates):
                path = Path(safe_join_under_root(self.root, relative))
                if path.exists() or path.is_symlink():
                    self.command("add", "--", relative, extra_env=env)
                elif relative in tracked:
                    self.command(
                        "rm", "--cached", "--ignore-unmatch", "--", relative,
                        extra_env=env,
                    )
            # Allowed tracked deletions are absent from --others output and
            # therefore need an explicit removal from the temporary index.
            for relative in sorted(tracked):
                if not allowed_file(relative):
                    continue
                path = Path(safe_join_under_root(self.root, relative))
                if not path.exists() and not path.is_symlink():
                    self.command(
                        "rm", "--cached", "--ignore-unmatch", "--", relative,
                        extra_env=env,
                    )
            tree = self.text_with_env(env, "write-tree").strip()
            commit = self.text_with_env(
                env,
                "commit-tree",
                tree,
                "-p",
                head,
                "-m",
                "Kájovo milestone: " + name,
            ).strip()

        after_index = (
            hashlib.sha256(real_index.read_bytes()).hexdigest()
            if real_index.is_file()
            else None
        )
        if before_index != after_index:
            raise RuntimeError(
                "Vytváření milníku změnilo uživatelský index; milník nebyl označen."
            )

        self.command(
            "update-ref",
            "refs/tags/" + name,
            commit,
            "",
        )
        metadata = {
            "version": 2,
            "name": name,
            "snapshot_commit": commit,
            "base_head": head,
            "branch": self.branch(),
            "included_paths": sorted(value for value in self.text(
                "ls-tree", "-r", "--name-only", "-z", commit
            ).split("\0") if value),
            "excluded_policy": "allowed_file",
        }
        self._milestone_dir()
        atomic_write_text(
            str(self._milestone_meta_path(name)),
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        )
        result = self.snapshot()
        result["milestone_notice"] = (
            "Milník je úplný snapshot povoleného pracovního stromu; "
            "uživatelský index ani aktivní větev nebyly při vytvoření změněny."
        )
        return result

    def text_with_env(self, env, *arguments):
        return self.command(*arguments, extra_env=env).stdout

    def restore(self, name):
        branch = self.branch()
        if self.text("status", "--porcelain").strip():
            raise ValueError(
                "Před obnovou milníku uložte nebo commitněte všechny místní změny "
                "včetně netrackovaných souborů."
            )
        self.command("rev-parse", "--verify", "refs/tags/" + name + "^{commit}")
        meta_path = self._milestone_meta_path(name)
        if meta_path.is_file():
            try:
                metadata = parse_json_strict(
                    meta_path.read_text(encoding="utf-8")
                )
            except (OSError, OrchestrationError) as exc:
                raise ValueError("Metadata milníku jsou poškozená.") from exc
            if (
                metadata.get("version") != 2
                or metadata.get("snapshot_commit")
                != self.text(
                    "rev-parse", "refs/tags/" + name + "^{commit}"
                ).strip()
            ):
                raise ValueError("Milník nemá platnou snapshot provenance.")
            milestone_type = "snapshot_v2"
        else:
            # Legacy tag captured only a commit. It is deliberately restored
            # only as that narrower committed tree; no archived diff is claimed.
            milestone_type = "legacy_commit_only"

        target = "refs/tags/" + name
        expected = {}
        for row in self.text("ls-tree", "-r", "-z", target).split("\0"):
            if not row:
                continue
            info, relative = row.split("\t", 1)
            mode, _, digest = info.split()
            if allowed_file(relative):
                expected[relative] = (mode, digest)
        current = {value for value in self.text("ls-files", "-z").split("\0")
                   if value and allowed_file(value)}
        for relative in sorted(current | expected.keys()):
            safe_join_under_root(self.root, relative)
            self.command("--literal-pathspecs", "restore", "--source", target,
                         "--staged", "--worktree", "--", relative)
        actual = {}
        for row in self.text("ls-files", "--stage", "-z").split("\0"):
            if not row:
                continue
            info, relative = row.split("\t", 1)
            mode, digest, stage = info.split()
            if allowed_file(relative):
                if stage != "0":
                    raise RuntimeError("Obnovený index obsahuje konflikt.")
                actual[relative] = (mode, digest)
        if actual != expected:
            raise RuntimeError("Povolená část indexu neodpovídá snapshotu milníku.")
        if self.branch() != branch:
            raise RuntimeError("Po obnově neodpovídá aktivní větev původní větvi.")
        result = self.snapshot()
        result["milestone_notice"] = (
            "Obnoven úplný snapshot pracovního stromu."
            if milestone_type == "snapshot_v2"
            else "Obnoven starý milník typu legacy: pouze commit, bez rozpracovaného diffu."
        )
        return result

    def remove_milestone(self, name):
        self.command("tag", "-d", "--", name)
        meta = self._milestone_meta_path(name)
        if meta.is_file():
            meta.unlink()
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
        def remove_readonly(function, path, error):
            target = Path(path)
            if (os.name != "nt" or not isinstance(error, PermissionError)
                    or target.is_symlink() or not target.is_file()
                    or not target.resolve().is_relative_to(directory.resolve())):
                raise error
            target.chmod(target.stat().st_mode | stat.S_IWRITE)
            function(path)

        shutil.rmtree(directory, onexc=remove_readonly)
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
