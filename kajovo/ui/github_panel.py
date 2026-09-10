from __future__ import annotations
from .layouts import FlowLayout, ContentSplitter
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_select_dir, dialog_input_text

import os
import shutil
import subprocess
import time
import difflib
from typing import Optional, Dict, List, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QCheckBox,
)
from .widgets import BusyPopup
from ..core.utils import safe_join_under_root


class GitHubPanel(QWidget):
    logline = Signal(str)

    _EXCLUDE_DIRS = ("venv", ".venv", "cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules", "LOG", "tmp_tests", "dist")

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.root: str = os.getcwd()

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        top = FlowLayout()
        self.ed_root = QLineEdit(self.root)
        self.btn_browse = QPushButton("Vybrat…")
        self.btn_refresh = QPushButton("Obnovit")
        self.btn_init = QPushButton("Založit repozitář")
        self.btn_milestone = QPushButton("Vytvořit milník")
        self.btn_restore = QPushButton("Obnovit milník")
        self.btn_delete_milestone = QPushButton("Odstranit milník")
        self.btn_delete = QPushButton("Odstranit repozitář")
        self.chk_diff = QCheckBox("Diff vs milestone")
        for w in (
            self.ed_root,
            self.btn_browse,
            self.btn_refresh,
            self.btn_init,
            self.btn_milestone,
            self.btn_restore,
            self.btn_delete_milestone,
            self.btn_delete,
            self.chk_diff,
        ):
            top.addWidget(w)
        v.addLayout(top)

        remote_row = QHBoxLayout()
        self.ed_repo_name = QLineEdit()
        self.ed_repo_name.setPlaceholderText("Repo name (optional)")
        self.ed_remote = QLineEdit()
        self.ed_remote.setPlaceholderText("https://github.com/user/repo.git")
        self.btn_upload = QPushButton("Upload (push)")
        self.btn_download = QPushButton("Download (pull)")
        self.lbl_sync = QLabel("Sync: unknown")
        for w in (QLabel("Name"), self.ed_repo_name, QLabel("Remote"), self.ed_remote, self.btn_upload, self.btn_download, self.lbl_sync):
            remote_row.addWidget(w)
        v.addLayout(remote_row)

        status_row = QHBoxLayout()
        self.lbl_status = QLabel("")
        status_row.addWidget(self.lbl_status)
        status_row.addStretch(1)
        v.addLayout(status_row)

        split = ContentSplitter()
        v.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)

        lv.addWidget(QLabel("Milestones (tags)"))
        self.lst_tags = QListWidget()
        lv.addWidget(self.lst_tags, 1)
        self.lst_tags.itemSelectionChanged.connect(self._on_tag_selection_changed)

        lv.addWidget(QLabel("File tree"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self._on_tree_clicked)
        lv.addWidget(self.tree, 3)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        self.lbl_file = QLabel("No file selected")
        self.txt_file = QTextEdit()
        self.btn_save = QPushButton("Uložit soubor")
        self.btn_save.clicked.connect(self._save_file)
        rv.addWidget(self.lbl_file)
        rv.addWidget(self.txt_file, 1)
        rv.addWidget(self.btn_save)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)

        self.btn_browse.clicked.connect(self._pick_root)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_init.clicked.connect(self.init_repo)
        self.btn_milestone.clicked.connect(self.create_milestone)
        self.btn_restore.clicked.connect(self.restore_milestone)
        self.btn_delete_milestone.clicked.connect(self.delete_milestone)
        self.btn_delete.clicked.connect(self.delete_repo)
        self.chk_diff.stateChanged.connect(self.refresh)
        self.btn_upload.clicked.connect(self._push)
        self.btn_download.clicked.connect(self._pull)

        self.refresh()

    # Pomocné metody.
    def _log(self, msg: str):
        try:
            self.logline.emit(msg)
        except Exception:
            pass

    def get_state(self) -> Dict[str, Any]:
        sel_tag = self._selected_tag()
        sel_file = ""
        if self.tree.selectedItems():
            sel_file = self.tree.selectedItems()[0].data(0, Qt.UserRole) or ""
        return {
            "root": self.root,
            "remote": self.ed_remote.text(),
            "repo_name": self.ed_repo_name.text(),
            "diff": bool(self.chk_diff.isChecked()),
            "selected_tag": sel_tag,
            "selected_file": sel_file,
        }

    def apply_state(self, state: Dict[str, Any]):
        if not state:
            return
        root = state.get("root") or ""
        if root:
            self.root = root
            self.ed_root.setText(root)
        self.ed_remote.setText(state.get("remote", ""))
        self.ed_repo_name.setText(state.get("repo_name", ""))
        self.chk_diff.setChecked(bool(state.get("diff", False)))
        self.refresh()
        tag = state.get("selected_tag")
        if tag:
            for i in range(self.lst_tags.count()):
                item = self.lst_tags.item(i)
                if item and item.data(Qt.UserRole) == tag:
                    self.lst_tags.setCurrentItem(item)
                    break
        sel_file = state.get("selected_file") or ""
        if sel_file:
            def _dfs(it):
                if it.data(0, Qt.UserRole) == sel_file:
                    return it
                for idx in range(it.childCount()):
                    res = _dfs(it.child(idx))
                    if res:
                        return res
                return None

            found = None
            for i in range(self.tree.topLevelItemCount()):
                found = _dfs(self.tree.topLevelItem(i))
                if found:
                    break
            if found:
                self.tree.setCurrentItem(found)
                self._on_tree_clicked()

    def _run_git(self, args, cwd=None) -> subprocess.CompletedProcess:
        from .background import run_io
        if cwd is None:
            cwd = self.root
        return run_io(lambda: subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}))

    def _get_repo_root(self) -> Optional[str]:
        try:
            res = self._run_git(["rev-parse", "--show-toplevel"], cwd=self.root)
            if res.returncode == 0:
                rp = res.stdout.strip()
                return rp if rp else None
        except Exception:
            return None
        return None

    def _is_git_repo(self) -> bool:
        if os.path.isdir(os.path.join(self.root, ".git")):
            return True
        return self._get_repo_root() is not None

    def _ensure_git_identity(self):
        """Doplní místní identitu Git potřebnou k vytvoření commitu."""
        try:
            name = self._run_git(["config", "--get", "user.name"], cwd=self.root)
            email = self._run_git(["config", "--get", "user.email"], cwd=self.root)
            if name.returncode != 0 or not (name.stdout or "").strip():
                self._run_git(["config", "user.name", "Kajovo"], cwd=self.root)
            if email.returncode != 0 or not (email.stdout or "").strip():
                self._run_git(["config", "user.email", "kajovo@example.com"], cwd=self.root)
        except Exception:
            pass

    def _ensure_gitignore(self):
        if not self._is_git_repo():
            return
        gitignore_path = os.path.join(self.root, ".gitignore")
        existing = []
        if os.path.isfile(gitignore_path):
            try:
                with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                    existing = [line.strip() for line in f.readlines()]
            except Exception:
                existing = []
        needed = [f"{d}/" for d in self._EXCLUDE_DIRS]
        missing = [p for p in needed if p not in existing]
        if not missing:
            return
        try:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                if existing and existing[-1] != "":
                    f.write("\n")
                f.write("# Kajovo excludes\n")
                for p in missing:
                    f.write(p + "\n")
            self._log(f".gitignore updated: {', '.join(missing)}")
        except Exception as e:
            self._log(f".gitignore update failed: {e}")

    def _is_excluded_path(self, rel_path: str) -> bool:
        parts = rel_path.replace("\\", "/").split("/")
        return any(part in self._EXCLUDE_DIRS for part in parts)

    def _find_tracked_excluded(self) -> List[str]:
        res = self._run_git(["ls-files", "-z"])
        if res.returncode != 0:
            return []
        items = res.stdout.split("\x00")
        return [p for p in items if p and self._is_excluded_path(p)]

    def _require_no_tracked_excludes(self) -> bool:
        blocked = self._find_tracked_excluded()
        if not blocked:
            return True
        preview = "\n".join(blocked[:10])
        msg_warning(
            self,
            "Git",
            "Repo obsahuje sledované cache/venv položky.\n"
            "Odstraň je z Gitu (git rm --cached) a přidej do .gitignore:\n"
            f"{preview}",
        )
        return False

    def _latest_tag(self) -> Optional[str]:
        res = self._run_git(["for-each-ref", "--format=%(refname:short)", "refs/tags", "--sort=-creatordate"])
        if res.returncode != 0:
            return None
        for line in res.stdout.splitlines():
            tag = line.strip()
            if tag:
                return tag
        return None

    def _write_milestone_diff(self, tag_name: str, base_tag: Optional[str]):
        res = self._run_git(["rev-parse", "--git-dir"])
        if res.returncode != 0:
            return
        git_dir = res.stdout.strip() or ".git"
        if not os.path.isabs(git_dir):
            git_dir = os.path.join(self.root, git_dir)
        out_dir = os.path.join(git_dir, "kajovo_milestones")
        os.makedirs(out_dir, exist_ok=True)
        import hashlib
        out_path = os.path.join(out_dir, hashlib.sha256(tag_name.encode("utf-8")).hexdigest() + ".diff")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines: List[str] = [
            f"# milestone: {tag_name}",
            f"# created: {stamp}",
            f"# diff_base: {base_tag or 'root'}",
        ]
        if base_tag:
            diff = self._run_git(["diff", "--binary", "--full-index", f"{base_tag}..HEAD"])
        else:
            diff = self._run_git(["diff", "--binary", "--full-index", "--root", "HEAD"])
        if diff.returncode == 0:
            lines.append(diff.stdout.rstrip())
        else:
            lines.append(f"# diff_error: {diff.stderr.strip()}")
        wt = self._run_git(["diff", "--binary", "--full-index"])
        if wt.returncode == 0 and wt.stdout.strip():
            lines.append("")
            lines.append("# worktree_diff")
            lines.append(wt.stdout.rstrip())
        elif wt.returncode != 0:
            lines.append(f"# worktree_diff_error: {wt.stderr.strip()}")
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines).strip() + "\n")
            self._log(f"Milestone diff saved: {out_path}")
        except Exception as e:
            self._log(f"Milestone diff write failed: {e}")

    def _resolve_head_branch(self) -> str:
        head_path = os.path.join(self.root, ".git", "HEAD")
        branch = "master"
        try:
            with open(head_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content.startswith("ref:"):
                ref = content.split(":", 1)[1].strip()
                if ref.startswith("refs/heads/"):
                    branch = ref[len("refs/heads/") :]
                else:
                    branch = ref
        except Exception:
            pass
        if not branch:
            branch = "master"
        return branch

    def _ensure_head_commit(self) -> bool:
        head_check = self._run_git(["rev-parse", "--verify", "HEAD"])
        if head_check.returncode == 0:
            return True
        self._ensure_git_identity()
        seed = self._run_git(["commit", "--allow-empty", "-m", "Initial milestone seed"])
        if seed.returncode == 0:
            return True
        tree_res = self._run_git(["write-tree"])
        if tree_res.returncode != 0:
            self._log(f"write-tree failed: {tree_res.stderr.strip()}")
            return False
        tree_sha = tree_res.stdout.strip()
        if not tree_sha:
            self._log("write-tree produced empty output")
            return False
        commit_res = self._run_git(["commit-tree", tree_sha, "-m", "Initial milestone seed"])
        if commit_res.returncode != 0:
            self._log(f"commit-tree failed: {commit_res.stderr.strip()}")
            return False
        commit_sha = commit_res.stdout.strip()
        if not commit_sha:
            self._log("commit-tree produced empty hash")
            return False
        branch = self._resolve_head_branch()
        ref_res = self._run_git(["update-ref", f"refs/heads/{branch}", commit_sha])
        if ref_res.returncode != 0:
            self._log(f"update-ref failed: {ref_res.stderr.strip()}")
            return False
        sym_res = self._run_git(["symbolic-ref", "HEAD", f"refs/heads/{branch}"])
        if sym_res.returncode != 0:
            self._log(f"symbolic-ref failed: {sym_res.stderr.strip()}")
        return True

    def _pick_root(self):
        d = dialog_select_dir(self, "Select root", self.root)
        if not d:
            return
        self.root = d
        self.ed_root.setText(self.root)
        self.refresh()

    def refresh(self):
        self.root = self.ed_root.text().strip() or self.root
        status = "Not a git repo"
        with BusyPopup(self, "Načítám stav repozitáře..."):
            repo_root = self._get_repo_root() if self._is_git_repo() else None
            if repo_root:
                self.root = repo_root
                self.ed_root.setText(self.root)
            if repo_root:
                try:
                    self.ed_repo_name.setText(os.path.basename(repo_root))
                except Exception:
                    pass
                try:
                    res = self._run_git(["status", "--short"], cwd=self.root)
                    if res.returncode == 0:
                        status = "Git repo | status: clean" if not res.stdout.strip() else "Git repo | pending changes"
                    else:
                        status = f"Git error: {res.stderr.strip()}"
                except Exception as e:
                    status = f"Git error: {e}"
            self.lbl_status.setText(f"{status} | root: {self.root}")
            self._load_tags()
            self._refresh_tree()
            self._check_sync()

    def init_repo(self):
        if self._is_git_repo():
            msg_info(self, "Git", "Repo už existuje.")
            return
        with BusyPopup(self, "Inicializuji git repo..."):
            res = self._run_git(["init"])
            if res.returncode == 0:
                self._ensure_gitignore()
                msg_info(self, "Git", "Inicializováno.")
            else:
                msg_critical(self, "Git", res.stderr or "Init failed.")
            self.refresh()

    def _load_tags(self):
        self.lst_tags.blockSignals(True)
        self.lst_tags.clear()
        if not self._is_git_repo():
            self.lst_tags.blockSignals(False)
            return
        res = self._run_git(["for-each-ref", "--format=%(refname:short)|%(creatordate:iso)", "refs/tags", "--sort=-creatordate"])
        if res.returncode != 0:
            self._log(f"Tag list error: {res.stderr.strip()}")
            self.lst_tags.blockSignals(False)
            return
        for line in res.stdout.splitlines():
            if "|" not in line:
                continue
            name, dt = line.split("|", 1)
            item = QListWidgetItem(f"{name} | {dt}")
            item.setData(Qt.UserRole, name)
            self.lst_tags.addItem(item)
        self.lst_tags.blockSignals(False)

    def _on_tag_selection_changed(self):
        if not self.chk_diff.isChecked():
            return
        self._refresh_tree()

    def create_milestone(self):
        if not self._is_git_repo():
            msg_warning(self, "Git", "Nejprve inicializuj repo.")
            return
        self._ensure_gitignore()
        if not self._require_no_tracked_excludes():
            return
        default_name = f"milestone-{int(time.time())}"
        name, ok = dialog_input_text(self, "Milestone", "Název tagu:", default=default_name)
        if not ok or not name.strip():
            return
        tag_name = name.strip()
        base_tag = self._latest_tag()
        # Repozitář bez HEAD dostane prázdný úvodní commit.
        if not self._ensure_head_commit():
            msg_critical(self, "Git", "Nelze vytvořit počáteční commit pro milestone.")
            return
        with BusyPopup(self, "Vytvářím milestone..."):
            res = self._run_git(["tag", "-a", tag_name, "-m", tag_name])
            if res.returncode != 0:
                msg_critical(self, "Git", res.stderr or "Nelze vytvořit tag.")
            else:
                self._log(f"Milestone created: {tag_name}")
                self._write_milestone_diff(tag_name, base_tag)
            self._load_tags()

    def restore_milestone(self):
        if not self._is_git_repo():
            msg_warning(self, "Git", "Nejprve inicializuj repo.")
            return
        sel = self.lst_tags.currentItem()
        if not sel:
            msg_info(self, "Git", "Vyber milestone.")
            return
        name = sel.data(Qt.UserRole)
        if msg_question(self, "Restore", f"Vrátit repo do stavu tagu {name}? (může přepsat změny)") != QMessageBox.Yes:
            return
        with BusyPopup(self, "Obnovuji milestone..."):
            res = self._run_git(["checkout", name])
            if res.returncode != 0:
                msg_critical(self, "Git", res.stderr or "Checkout selhal.")
            else:
                self._log(f"Checked out {name}")
            self.refresh()

    def delete_milestone(self):
        if not self._is_git_repo():
            msg_warning(self, "Git", "Nejprve inicializuj repo.")
            return
        sel = self.lst_tags.currentItem()
        if not sel:
            msg_info(self, "Git", "Vyber milestone.")
            return
        name = sel.data(Qt.UserRole)
        if msg_question(
            self, "Delete", f"Smazat milestone tag {name}?"
        ) != QMessageBox.Yes:
            return
        with BusyPopup(self, "Mažu milestone..."):
            res = self._run_git(["tag", "-d", name])
            if res.returncode != 0:
                msg_critical(self, "Git", res.stderr or "Smazání selhalo.")
                return
            self._log(f"Milestone deleted: {name}")
            self.refresh()

    def delete_repo(self):
        if not self._is_git_repo():
            msg_info(self, "Git", "Repo nenalezeno.")
            return
        if msg_question(self, "Delete", "Smazat .git a zrušit repo?") != QMessageBox.Yes:
            return
        try:
            shutil.rmtree(os.path.join(self.root, ".git"), ignore_errors=False)
            self._log("Repo .git smazáno.")
        except Exception as e:
            msg_critical(self, "Git", str(e))
        self.refresh()

    # Synchronizace a vzdálený repozitář.
    def _check_sync(self):
        if not self._is_git_repo():
            self._set_sync_status("Sync: n/a", "#6b7b8c")
            return
        try:
            remote = self._run_git(["remote", "get-url", "origin"])
            if remote.returncode == 0 and not self.ed_remote.text().strip():
                self.ed_remote.setText(remote.stdout.strip())
            res = self._run_git(["status", "-sb"])
            txt = res.stdout.strip()
            if "ahead" in txt or "behind" in txt:
                self._set_sync_status("Sync: NOT in sync", "#6b7b8c")
            else:
                self._set_sync_status("Sync: maybe in sync", "#2FA0FF")
        except Exception as e:
            self._set_sync_status(f"Sync: error {e}", "#9bb3c9")

    def _configure_origin(self, url: str) -> bool:
        if not url:
            return True
        current = self._run_git(["remote", "get-url", "origin"])
        command = "set-url" if current.returncode == 0 else "add"
        result = self._run_git(["remote", command, "origin", url])
        if result.returncode:
            msg_critical(self, "Git", result.stderr or "Nelze nastavit origin.")
            return False
        return True

    def _push(self):
        if not self._is_git_repo():
            msg_warning(self, "Git", "Není git repo.")
            return
        self._ensure_gitignore()
        if not self._require_no_tracked_excludes():
            return
        url = self.ed_remote.text().strip()
        if not self._configure_origin(url):
            return
        with BusyPopup(self, "Push na remote..."):
            res = self._run_git(["push", "-u", "origin", "HEAD"])
            if res.returncode != 0:
                msg_critical(self, "Push", res.stderr or "Push failed")
            else:
                msg_info(self, "Push", "Upload hotov.")
            self._check_sync()

    def _pull(self):
        if not self._is_git_repo():
            msg_warning(self, "Git", "Není git repo.")
            return
        url = self.ed_remote.text().strip()
        if not self._configure_origin(url):
            return
        branch = self._run_git(["symbolic-ref", "--quiet", "--short", "HEAD"])
        if branch.returncode:
            msg_warning(self, "Git", "Pull vyžaduje aktivní větev.")
            return
        with BusyPopup(self, "Pull z remote..."):
            res = self._run_git(["pull", "--ff-only", "origin", branch.stdout.strip()])
            if res.returncode != 0:
                msg_critical(self, "Pull", res.stderr or "Pull failed")
            else:
                msg_info(self, "Pull", "Download hotov.")
            self.refresh()

    def _set_sync_status(self, text: str, color: str):
        self.lbl_sync.setText(text)
        self.lbl_sync.setStyleSheet(f"color: {color};")

    # Zobrazení rozdílů.
    def _git_show(self, tag: str, rel_path: str) -> Optional[str]:
        res = self._run_git(["show", f"{tag}:{rel_path}"])
        if res.returncode != 0:
            return None
        return res.stdout

    def _diff_status(self, tag: Optional[str]) -> Dict[str, str]:
        status: Dict[str, str] = {}
        if not tag or not self._is_git_repo():
            return status
        res = self._run_git(["diff", "--name-status", f"{tag}"])
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    status[parts[1].replace("\\", "/")] = parts[0]
        # Neverzované soubory.
        res2 = self._run_git(["status", "--porcelain"])
        if res2.returncode == 0:
            for line in res2.stdout.splitlines():
                if line.startswith("??"):
                    p = line[2:].strip().replace("\\", "/")
                    status[p] = "A"
        return status

    def _build_tree_items(self, root_item: QTreeWidgetItem, rel_dir: str, status_map: Dict[str, str], include_removed: List[str]):
        abs_dir = os.path.join(self.root, rel_dir) if rel_dir else self.root
        try:
            entries = sorted(os.listdir(abs_dir))
        except Exception:
            entries = []
        for name in entries:
            if name == ".git" or name in self._EXCLUDE_DIRS:
                continue
            rel_path = os.path.join(rel_dir, name).replace("\\", "/") if rel_dir else name
            abs_path = os.path.join(abs_dir, name)
            if os.path.islink(abs_path) or os.path.isjunction(abs_path):
                continue
            st = status_map.get(rel_path)
            item = QTreeWidgetItem([name])
            item.setData(0, Qt.UserRole, rel_path)
            if os.path.isdir(abs_path):
                if st == "D":
                    item.setForeground(0, QColor("#6b7b8c"))
                elif st == "A":
                    item.setForeground(0, QColor("#2FA0FF"))
                root_item.addChild(item)
                self._build_tree_items(item, rel_path, status_map, include_removed)
            else:
                if st == "D":
                    item.setForeground(0, QColor("#6b7b8c"))
                elif st == "A":
                    item.setForeground(0, QColor("#2FA0FF"))
                root_item.addChild(item)
        # Odstraněné soubory, které nejsou na disku.
        for rel_path in list(include_removed):
            if rel_path.startswith(rel_dir) and "/" not in rel_path[len(rel_dir):].strip("/"):
                name = os.path.basename(rel_path)
                item = QTreeWidgetItem([name])
                item.setData(0, Qt.UserRole, rel_path)
                item.setForeground(0, QColor("#6b7b8c"))
                root_item.addChild(item)
                include_removed.remove(rel_path)

    def _refresh_tree(self):
        previous_path = ""
        sel = self.tree.selectedItems()
        if sel:
            previous_path = sel[0].data(0, Qt.UserRole) or ""
        self.tree.clear()
        status_map = self._diff_status(self._selected_tag())
        include_removed = [p for p, s in status_map.items() if s == "D"]
        root_item = QTreeWidgetItem([os.path.basename(self.root.rstrip(os.sep)) or self.root])
        root_item.setData(0, Qt.UserRole, "")
        self._build_tree_items(root_item, "", status_map, include_removed)
        self.tree.addTopLevelItem(root_item)
        self.tree.expandToDepth(2)
        if previous_path:
            found = self._find_tree_item(previous_path)
            if found:
                self.tree.setCurrentItem(found)

    def _find_tree_item(self, rel_path: str) -> Optional[QTreeWidgetItem]:
        def recurse(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            if item.data(0, Qt.UserRole) == rel_path:
                return item
            for idx in range(item.childCount()):
                res = recurse(item.child(idx))
                if res:
                    return res
            return None

        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            found = recurse(root)
            if found:
                return found
        return None

    def _selected_tag(self) -> Optional[str]:
        sel = self.lst_tags.currentItem()
        if not sel:
            return None
        return sel.data(Qt.UserRole)

    # Prohlížeč souborů.
    def _on_tree_clicked(self):
        sel = self.tree.selectedItems()
        if not sel:
            return
        item = sel[0]
        rel_path = item.data(0, Qt.UserRole) or ""
        self.lbl_file.setText(rel_path if rel_path else "No file selected")

        if self.chk_diff.isChecked():
            self._show_diff(rel_path)
            self.txt_file.setReadOnly(True)
            self.btn_save.setEnabled(False)
        else:
            loaded = self._load_current(rel_path)
            self.txt_file.setReadOnly(not loaded)
            self.btn_save.setEnabled(loaded)

    def _save_file(self):
        path = self.lbl_file.text()
        if not path or not os.path.isfile(path if os.path.isabs(path) else os.path.join(self.root, path)):
            msg_info(self, "Save", "Není vybrán soubor.")
            return
        try:
            abs_path = safe_join_under_root(self.root, path)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(self.txt_file.toPlainText())
            self._log(f"Soubor uložen: {abs_path}")
        except Exception as e:
            msg_critical(self, "Save", str(e))

    def _load_current(self, rel_path: str):
        if not rel_path:
            self.txt_file.clear()
            return False
        try:
            abs_path = safe_join_under_root(self.root, rel_path)
            with open(abs_path, "r", encoding="utf-8") as f:
                self.txt_file.setPlainText(f.read())
            return True
        except Exception as e:
            self.txt_file.setPlainText(f"<< nelze načíst soubor: {e} >>")
            return False

    def _show_diff(self, rel_path: str):
        self.txt_file.clear()
        tag = self._selected_tag()
        status_map = self._diff_status(tag)
        st = status_map.get(rel_path, "")
        tag_content = self._git_show(tag, rel_path) if tag else None
        cur_content = ""
        abs_path = os.path.join(self.root, rel_path)
        if os.path.isfile(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    cur_content = f.read()
            except Exception:
                cur_content = ""

        # Nový soubor.
        if st == "A" and cur_content:
            html = "<pre style='color: #2FA0FF;'>" + self._html_escape(cur_content) + "</pre>"
            self.txt_file.setHtml(html)
            return
        # Odstraněný soubor.
        if st == "D" and tag_content is not None:
            html = "<pre style='color: #6b7b8c;'>" + self._html_escape(tag_content) + "</pre>"
            self.txt_file.setHtml(html)
            return

        # Zobrazení bloků změn souboru.
        before = (tag_content or "").splitlines()
        after = (cur_content or "").splitlines()
        diff = difflib.ndiff(before, after)
        lines: List[str] = []
        for line in diff:
            if line.startswith("+ "):
                lines.append(f"<span style='color: #2FA0FF;'>+ {self._html_escape(line[2:])}</span>")
            elif line.startswith("- "):
                lines.append(f"<span style='color: #6b7b8c;'>- {self._html_escape(line[2:])}</span>")
            else:
                lines.append(self._html_escape(line[2:] if line.startswith('? ') else line))
        self.txt_file.setHtml("<pre>" + "\n".join(lines) + "</pre>")

    @staticmethod
    def _html_escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
