"""Lokální Git a editor souborů s explicitními mutačními akcemi."""

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QWidget,
    QTabWidget,
    QListWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QCheckBox,
    QMessageBox,
    QPushButton,
)
from ..core.utils import safe_join_under_root, atomic_write_text
from .design import column, row, label, button, text, editor
from .dialogs import msg_info, msg_warning, msg_question, dialog_select_dir, dialog_input_text
from .jobs import Jobs


class GitHubPanel(QWidget):
    logline = Signal(str)
    EXCLUDES = {
        ".git",
        "venv",
        ".venv",
        "cache",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "node_modules",
        "LOG",
        "tmp_tests",
        "dist",
    }

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.root = os.getcwd()
        self.jobs = Jobs(self)
        self.jobs.busy_changed.connect(self._busy)
        self._loaded_path = None
        self._pending_state = None
        layout = column(self)
        self.ed_root = text(self.root)
        layout.addWidget(
            row(
                self.ed_root,
                button("Vybrat projekt", self._pick_root),
                button("Obnovit", self.refresh),
            )
        )
        self.lbl_status = label("Vyberte projekt a načtěte jeho stav.", "Hint")
        layout.addWidget(self.lbl_status)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        git = QWidget()
        gl = column(git, 16)
        self.ed_repo_name = text(placeholder="Název projektu")
        self.ed_remote = text(placeholder="https://github.com/vlastník/repozitář.git")
        gl.addWidget(row(label("Název"), self.ed_repo_name))
        gl.addWidget(row(label("Remote"), self.ed_remote))
        gl.addWidget(
            row(
                button("Založit Git", self.init_repo),
                button("Odeslat (push)", self._push),
                button("Stáhnout (pull)", self._pull),
            )
        )
        self.lbl_sync = label("Synchronizace nebyla ověřena.", "Hint")
        gl.addWidget(self.lbl_sync)
        self.lst_tags = QListWidget()
        gl.addWidget(label("Milníky projektu"))
        gl.addWidget(
            label(
                "Milník označuje poslední commit. Necommitované změny se přiloží jako samostatný diff.",
                "Hint",
            )
        )
        gl.addWidget(self.lst_tags, 1)
        gl.addWidget(
            row(
                button("Nový milník", self.create_milestone),
                button("Obnovit milník", self.restore_milestone),
                button("Smazat milník", self.delete_milestone, "Danger"),
            )
        )
        gl.addWidget(button("Odstranit místní Git repozitář", self.delete_repo, "Danger"))
        tabs.addTab(git, "Verze a synchronizace")
        files = QWidget()
        fl = column(files, 16)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Soubory projektu", "Změna"])
        self.tree.itemSelectionChanged.connect(self._on_tree_clicked)
        fl.addWidget(self.tree, 1)
        self.chk_diff = QCheckBox("Porovnat se zvoleným milníkem")
        self.chk_diff.toggled.connect(self.refresh)
        self.lst_tags.itemSelectionChanged.connect(
            lambda: self.refresh() if self.chk_diff.isChecked() else None
        )
        fl.addWidget(self.chk_diff)
        self.lbl_file = label("Žádný soubor není otevřen.")
        fl.addWidget(self.lbl_file)
        self.txt_file = editor()
        fl.addWidget(self.txt_file, 2)
        fl.addWidget(button("Uložit soubor", self._save_file, "Primary"))
        tabs.addTab(files, "Soubory a porovnání")

    def _busy(self, active):
        for control in [
            self.ed_root,
            self.lst_tags,
            self.tree,
            self.chk_diff,
            *self.findChildren(QPushButton),
        ]:
            if control.window() is self.window():
                control.setEnabled(not active)

    def _run_git(self, args, cwd=None):
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )

    def _git(self, args, cwd=None):
        result = self._run_git(args, cwd)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Git operace selhala.")
        return result.stdout

    def _pick_root(self):
        path = dialog_select_dir(self, "Kořen projektu", self.root)
        if path:
            self.ed_root.setText(path)
            self.refresh()

    def refresh(self):
        if self.jobs.active:
            return
        root = self.ed_root.text().strip() or self.root
        restore = self._pending_state or {}
        self._pending_state = None
        selected_tag = restore.get("selected_tag") or self._selected_tag()
        compare = self.chk_diff.isChecked()

        def execute(job):
            probe = self._run_git(["rev-parse", "--show-toplevel"], root)
            actual = probe.stdout.strip() if probe.returncode == 0 else str(Path(root).resolve())
            result = {
                "root": actual,
                "status": "Adresář nemá Git repozitář.",
                "tags": [],
                "files": [],
                "sync": "",
                "changes": {},
            }
            if probe.returncode == 0:
                result["status"] = (
                    self._git(["status", "--short"], actual) or "Pracovní strom je čistý."
                )
                result["tags"] = self._git(["tag", "--sort=-creatordate"], actual).splitlines()
                remote = self._run_git(["remote", "get-url", "origin"], actual)
                result["remote"] = remote.stdout.strip() if remote.returncode == 0 else ""
                ahead = self._run_git(
                    ["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], actual
                )
                result["sync"] = (
                    ("Lokální / vzdálené commity navíc: " + ahead.stdout.strip())
                    if ahead.returncode == 0
                    else "Větev nemá sledovaný upstream."
                )
                if compare and selected_tag:
                    old_files = self._git(
                        ["ls-tree", "-r", "--name-only", "-z", "refs/tags/" + selected_tag], actual
                    ).split("\0")
                    result["files"].extend(
                        path
                        for path in old_files
                        if path and not any(part in self.EXCLUDES for part in Path(path).parts)
                    )
                    changes = self._git(
                        [
                            "diff",
                            "--name-status",
                            "--no-renames",
                            "refs/tags/" + selected_tag,
                            "--",
                        ],
                        actual,
                    )
                    result["changes"] = {
                        line.split("\t", 1)[1]: line.split("\t", 1)[0]
                        for line in changes.splitlines()
                        if "\t" in line
                    }
            for folder, directories, names in os.walk(actual):
                directories[:] = [name for name in directories if name not in self.EXCLUDES]
                for name in names:
                    path = Path(folder) / name
                    if path.is_symlink():
                        continue
                    result["files"].append(str(path.relative_to(actual)))
            return result

        def receive(result):
            self.root = result["root"]
            self.ed_root.setText(self.root)
            self.ed_repo_name.setText(Path(self.root).name)
            self.lbl_status.setText(result["status"])
            self.lbl_sync.setText(result["sync"])
            if not self.ed_remote.text().strip():
                self.ed_remote.setText(result.get("remote", ""))
            self.lst_tags.blockSignals(True)
            self.lst_tags.clear()
            self.lst_tags.addItems(result["tags"])
            matches = self.lst_tags.findItems(selected_tag, Qt.MatchExactly)
            if matches:
                self.lst_tags.setCurrentItem(matches[0])
            self.lst_tags.blockSignals(False)
            self.tree.clear()
            nodes = {}
            for filename in sorted(set(result["files"])):
                parent = self.tree.invisibleRootItem()
                prefix = []
                for part in Path(filename).parts:
                    prefix.append(part)
                    key = "/".join(prefix)
                    if key not in nodes:
                        node = QTreeWidgetItem([part])
                        node.setData(0, Qt.UserRole, key)
                        parent.addChild(node)
                        nodes[key] = node
                    parent = nodes[key]
                change = result["changes"].get(filename, "")
                parent.setText(
                    1, {"A": "Přidáno", "M": "Změněno", "D": "Odstraněno"}.get(change, change)
                )
            saved_file = restore.get("selected_file")
            if saved_file:
                relative = os.path.relpath(saved_file, self.root).replace("\\", "/")
                if relative in nodes:
                    self.tree.setCurrentItem(nodes[relative])

        self.jobs.start("Načtení projektu", execute, receive)

    def _operation(self, title, operation):
        if self.jobs.active:
            return
        root = str(Path(self.ed_root.text().strip() or self.root).resolve())
        if not Path(root).is_dir():
            msg_warning(self, title, "Vybraný adresář neexistuje.")
            return
        self.jobs.start(title, lambda job: operation(root), lambda result: self.refresh())

    def _ensure_safe_tracking(self, root):
        tracked = self._git(["ls-files", "-z"], root).split("\0")
        blocked = [
            path for path in tracked if any(part in self.EXCLUDES for part in Path(path).parts)
        ]
        if blocked:
            raise ValueError(
                "Repozitář sleduje provozní nebo vyloučené adresáře: " + ", ".join(blocked[:10])
            )

    def init_repo(self):
        def execute(root):
            self._git(["init"], root)
            path = Path(root) / ".gitignore"
            existing = path.read_text(encoding="utf-8") if path.exists() else ""
            additions = [
                name + "/"
                for name in sorted(self.EXCLUDES - {".git"})
                if name + "/" not in existing.splitlines()
            ]
            if additions:
                atomic_write_text(str(path), existing.rstrip() + "\n" + "\n".join(additions) + "\n")

        self._operation("Založení repozitáře", execute)

    def _selected_tag(self):
        item = self.lst_tags.currentItem()
        return item.text() if item else ""

    def create_milestone(self):
        name, ok = dialog_input_text(
            self, "Nový milník", "Název Git tagu:", f"milestone-{int(time.time())}"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        def execute(root):
            self._ensure_safe_tracking(root)
            if name.startswith("-"):
                raise ValueError("Název milníku nesmí začínat pomlčkou.")
            self._git(["check-ref-format", "refs/tags/" + name], root)
            for field, fallback in (("user.name", "Kajovo"), ("user.email", "kajovo@example.com")):
                identity = self._run_git(["config", "--get", field], root)
                if identity.returncode or not identity.stdout.strip():
                    self._git(["config", "--local", field, fallback], root)
            tags = self._git(["tag", "--sort=-creatordate"], root).splitlines()
            head = self._run_git(["rev-parse", "--verify", "HEAD"], root)
            if head.returncode:
                self._git(["commit", "--allow-empty", "-m", "Initial milestone seed"], root)
            self._git(["tag", "-a", name, "-m", name], root)
            gitdir = self._git(["rev-parse", "--absolute-git-dir"], root).strip()
            folder = Path(gitdir) / "kajovo_milestones"
            folder.mkdir(exist_ok=True)
            committed = (
                self._git(
                    ["diff", "--binary", "--full-index", "refs/tags/" + tags[0], "HEAD", "--"], root
                )
                if tags
                else self._git(
                    [
                        "diff-tree",
                        "--root",
                        "--no-commit-id",
                        "-p",
                        "--binary",
                        "--full-index",
                        "HEAD",
                    ],
                    root,
                )
            )
            changes = (
                committed
                + "\n# Necommitované změny\n"
                + self._git(["diff", "--binary", "--full-index", "HEAD"], root)
            )
            atomic_write_text(
                str(folder / (hashlib.sha256(name.encode()).hexdigest() + ".diff")),
                f"# milestone: {name}\n" + changes,
            )

        self._operation("Vytvoření milníku", execute)

    def restore_milestone(self):
        tag = self._selected_tag()
        if not tag:
            return
        if msg_question(
            self, "Obnovit milník?",
            f"Obnovit tracked soubory a index z {tag} při zachování aktuální větve? Necommitované změny musí být nejprve uloženy nebo commitnuty."
        ) != QMessageBox.Yes:
            return

        def execute(root):
            branch = self._run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
            if branch.returncode or not branch.stdout.strip():
                raise RuntimeError("Repozitář je v detached HEAD. Nejprve explicitně přepněte nebo vytvořte větev.")
            branch_name = branch.stdout.strip()
            if self._git(["status", "--porcelain"], root).strip():
                raise RuntimeError("Pracovní strom není čistý. Před obnovou milníku změny commitněte nebo jinak bezpečně uložte.")
            self._git(["restore", "--source", "refs/tags/" + tag, "--staged", "--worktree", "--", "."], root)
            after = self._git(["symbolic-ref", "--quiet", "--short", "HEAD"], root).strip()
            if after != branch_name:
                raise RuntimeError("Obnova změnila aktivní větev; operace nesplnila bezpečnostní postcondition.")
            return {"branch": branch_name, "tag": tag}

        self._operation("Obnova milníku", execute)

    def delete_milestone(self):
        tag = self._selected_tag()
        if tag and msg_question(self, "Smazat milník?", tag) == QMessageBox.Yes:
            self._operation(
                "Odstranění milníku", lambda root: self._git(["tag", "-d", "--", tag], root)
            )

    def delete_repo(self):
        path = Path(self.root) / ".git"
        if not path.is_dir() or path.is_symlink():
            msg_warning(
                self,
                "Odstranění Git",
                "Vybraný projekt nemá vlastní adresář .git. Připojený worktree nelze takto odstranit.",
            )
            return
        if (
            msg_question(
                self,
                "Odstranit místní historii Git?",
                f"Odstraní se {path}. Pracovní soubory zůstanou zachované. Historii nebude možné vrátit.",
            )
            == QMessageBox.Yes
        ):
            self._operation("Odstranění Git", lambda root: shutil.rmtree(Path(root) / ".git"))

    def _configure_origin(self, root, remote):
        if not remote:
            self._git(["remote", "get-url", "origin"], root)
            return
        if remote.startswith("-"):
            raise ValueError("Vyplňte platnou adresu remote.")
        current = self._run_git(["remote", "get-url", "origin"], root)
        self._git(
            ["remote", "set-url", "origin", remote]
            if current.returncode == 0
            else ["remote", "add", "origin", remote],
            root,
        )

    def _push(self):
        remote = self.ed_remote.text().strip()

        def execute(root):
            self._ensure_safe_tracking(root)
            self._configure_origin(root, remote)
            probe = self._run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
            if probe.returncode or not probe.stdout.strip():
                raise RuntimeError("Repozitář je v detached HEAD. Před odesláním explicitně přepněte nebo vytvořte větev.")
            branch = probe.stdout.strip()
            self._git(["push", "-u", "origin", branch], root)

        self._operation("Odeslání projektu", execute)

    def _pull(self):
        remote = self.ed_remote.text().strip()

        def execute(root):
            self._configure_origin(root, remote)
            probe = self._run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
            if probe.returncode or not probe.stdout.strip():
                raise RuntimeError("Repozitář je v detached HEAD. Před stažením explicitně přepněte nebo vytvořte větev.")
            branch = probe.stdout.strip()
            self._git(["pull", "--ff-only", "origin", branch], root)

        self._operation("Stažení projektu", execute)

    def _on_tree_clicked(self):
        item = self.tree.currentItem()
        if not item:
            return
        relative = item.data(0, Qt.UserRole)
        try:
            path = Path(safe_join_under_root(self.root, relative))
            self.lbl_file.setText(relative)
            if self.chk_diff.isChecked() and self._selected_tag():
                tag = self._selected_tag()
                self.jobs.start(
                    "Porovnání souboru",
                    lambda job: self._git(["diff", "refs/tags/" + tag, "--", relative]),
                    self.txt_file.setPlainText,
                    popup=False,
                )
                self.txt_file.setReadOnly(True)
            else:
                if not path.is_file():
                    return
                if path.stat().st_size > 8_000_000:
                    raise ValueError("Soubor přesahuje 8 MB; otevřete jej externím editorem.")
                content = path.read_text(encoding="utf-8")
                self._loaded_path = str(path)
                self.txt_file.setPlainText(content)
                self.txt_file.setReadOnly(False)
        except (OSError, ValueError) as exc:
            msg_warning(self, "Otevření souboru", str(exc))

    def _save_file(self):
        if self._loaded_path and not self.txt_file.isReadOnly():
            try:
                relative = os.path.relpath(self._loaded_path, self.root)
                atomic_write_text(
                    safe_join_under_root(self.root, relative), self.txt_file.toPlainText()
                )
                msg_info(self, "Soubor uložen", relative)
            except (OSError, ValueError) as exc:
                msg_warning(self, "Uložení souboru", str(exc))

    def get_state(self):
        return {
            "root": self.root,
            "remote": self.ed_remote.text(),
            "repo_name": self.ed_repo_name.text(),
            "diff": self.chk_diff.isChecked(),
            "selected_tag": self._selected_tag(),
            "selected_file": self._loaded_path or "",
        }

    def apply_state(self, state):
        self.root = state.get("root") or self.root
        self.ed_root.setText(self.root)
        self.ed_remote.setText(state.get("remote", ""))
        self.ed_repo_name.setText(state.get("repo_name", ""))
        self.chk_diff.blockSignals(True)
        self.chk_diff.setChecked(bool(state.get("diff")))
        self.chk_diff.blockSignals(False)
        self._pending_state = dict(state)
