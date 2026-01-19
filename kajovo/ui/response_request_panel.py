from __future__ import annotations

import os
import re
from typing import List, Optional, Dict, Any

from PySide6.QtCore import Qt, QSizeF
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from .theme import DARK_STYLESHEET

from .widgets import msg_warning, msg_info, dialog_save_file


_RUN_RE = re.compile(r"^RUN_(\d{2})(\d{2})(\d{4})(\d{2})(\d{2})")


class ResponseRequestPanel(QWidget):
    def __init__(self, log_dir: str, parent=None):
        super().__init__(parent)
        self.log_dir = log_dir
        self._runs: List[Dict[str, Any]] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        filters = QHBoxLayout()
        self.ed_run_id = QLineEdit()
        self.ed_run_id.setPlaceholderText("RUN ID (část)")
        self.ed_resp_id = QLineEdit()
        self.ed_resp_id.setPlaceholderText("Response ID (část)")
        self.ed_date = QLineEdit()
        self.ed_date.setPlaceholderText("Datum (DD.MM.YYYY / YYYY-MM-DD)")
        self.ed_fulltext = QLineEdit()
        self.ed_fulltext.setPlaceholderText("Fulltext (obsah request/response)")
        self.btn_filter = QPushButton("Filtrovat")
        self.btn_refresh = QPushButton("Refresh")
        filters.addWidget(self.ed_run_id, 1)
        filters.addWidget(self.ed_resp_id, 1)
        filters.addWidget(self.ed_date, 1)
        filters.addWidget(self.ed_fulltext, 2)
        filters.addWidget(self.btn_filter)
        filters.addWidget(self.btn_refresh)
        v.addLayout(filters)

        split = QSplitter()
        v.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QLabel("RUNy"))
        self.lst_runs = QListWidget()
        self.lst_runs.setSelectionMode(QListWidget.ExtendedSelection)
        lv.addWidget(self.lst_runs, 1)

        middle = QWidget()
        mv = QVBoxLayout(middle)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.addWidget(QLabel("Request/Response pořadí"))
        self.lst_entries = QListWidget()
        mv.addWidget(self.lst_entries, 1)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("Detail (request/response)"))
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_save = QPushButton("Save TXT")
        self.btn_print = QPushButton("Print TXT")
        action_row.addWidget(self.btn_save)
        action_row.addWidget(self.btn_print)
        rv.addLayout(action_row)
        self.txt_detail = QPlainTextEdit()
        self.txt_detail.setReadOnly(True)
        rv.addWidget(self.txt_detail, 1)

        split.addWidget(left)
        split.addWidget(middle)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 2)

        self.btn_refresh.clicked.connect(self.refresh_runs)
        self.btn_filter.clicked.connect(self.apply_filters)
        self.btn_save.clicked.connect(self.save_selected_entry)
        self.btn_print.clicked.connect(self.print_selected_entry)
        self.lst_runs.itemSelectionChanged.connect(self.load_entries_for_selected_run)
        self.lst_entries.itemSelectionChanged.connect(self.load_selected_entry)

        self.refresh_runs()

    def showEvent(self, event):
        super().showEvent(event)
        # Refresh when the tab becomes visible so newly created RUNs show up.
        self.refresh_runs()

    def refresh_runs(self):
        if not os.path.isdir(self.log_dir):
            msg_warning(self, "Logs", f"LOG adresář nenalezen: {self.log_dir}")
            return
        runs: List[Dict[str, Any]] = []
        for name in os.listdir(self.log_dir):
            if not name.startswith("RUN_"):
                continue
            path = os.path.join(self.log_dir, name)
            if not os.path.isdir(path):
                continue
            date_label, date_key, ts_key = self._parse_run_date(name)
            runs.append({"id": name, "path": path, "date_label": date_label, "date_key": date_key, "ts_key": ts_key})
        self._runs = sorted(runs, key=lambda r: r.get("ts_key") or r.get("id") or "", reverse=True)
        self.apply_filters()

    def apply_filters(self):
        run_filter = (self.ed_run_id.text() or "").strip().lower()
        resp_filter = (self.ed_resp_id.text() or "").strip().lower()
        date_filter = self._normalize_date_filter(self.ed_date.text() or "")
        fulltext_filter = (self.ed_fulltext.text() or "").strip().lower()

        self.lst_runs.clear()
        for run in self._runs:
            if run_filter and run_filter not in run["id"].lower():
                continue
            if date_filter and run.get("date_key") != date_filter:
                continue
            if resp_filter and not self._run_has_response_id(run["path"], resp_filter):
                continue
            if fulltext_filter and not self._run_has_fulltext(run["path"], fulltext_filter):
                continue
            label = f"{run['id']}  |  {run.get('date_label') or ''}"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, run)
            self.lst_runs.addItem(it)

    def load_entries_for_selected_run(self):
        self.lst_entries.clear()
        self.txt_detail.setPlainText("")
        sel = self.lst_runs.selectedItems()
        if not sel:
            return
        entries: List[Dict[str, Any]] = []
        for item in sel:
            run = item.data(Qt.UserRole)
            if not run:
                continue
            run_entries = self._collect_entries(run["path"])
            for e in run_entries:
                e["run_id"] = run.get("id", "")
            entries.extend(run_entries)
        for e in sorted(entries, key=lambda x: x.get("mtime") or 0):
            run_id = e.get("run_id") or ""
            it = QListWidgetItem(f"{run_id} | {e['kind']} | {e['name']}")
            it.setData(Qt.UserRole, e)
            self.lst_entries.addItem(it)

    def load_selected_entry(self):
        sel = self.lst_entries.selectedItems()
        if not sel:
            return
        entry = sel[0].data(Qt.UserRole) or {}
        path = entry.get("path")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            content = f"(nelze načíst: {e})"
        self.txt_detail.setPlainText(content)

    def print_selected_entry(self):
        content = self.txt_detail.toPlainText().strip()
        if not content:
            msg_info(self, "Print", "Není vybrán žádný request/response.")
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPageSize(QPrinter.A4)
        printer.setFullPage(False)
        dlg = QPrintDialog(printer, self)
        dlg.setStyleSheet(DARK_STYLESHEET)
        dlg.resize(720, 520)
        if dlg.exec() != QPrintDialog.Accepted:
            return
        doc = self.txt_detail.document().clone()
        doc.setDefaultFont(self.txt_detail.font())
        doc.setPageSize(QSizeF(printer.pageRect(QPrinter.Unit.Point).size()))
        doc.print(printer)

    def save_selected_entry(self):
        content = self.txt_detail.toPlainText()
        if not content.strip():
            msg_info(self, "Save", "Není vybrán žádný request/response.")
            return
        sel = self.lst_entries.selectedItems()
        name = "request_response.txt"
        if sel:
            entry = sel[0].data(Qt.UserRole) or {}
            run_id = entry.get("run_id", "")
            base = f"{entry.get('kind','')}_{entry.get('name','')}"
            if run_id:
                base = f"{run_id}_{base}"
            name = f"{base}.txt".replace("/", "_").replace("\\", "_")
        path, _ = dialog_save_file(self, "Uložit TXT", name, "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            msg_info(self, "Save", f"Uloženo: {path}")
        except Exception as e:
            msg_warning(self, "Save", f"Nelze uložit: {e}")

    def _collect_entries(self, run_path: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for kind, sub in (("REQ", "requests"), ("RESP", "responses")):
            folder = os.path.join(run_path, sub)
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue
                try:
                    mtime = os.path.getmtime(path)
                except Exception:
                    mtime = 0
                entries.append({"kind": kind, "name": name, "path": path, "mtime": mtime})
        return sorted(entries, key=lambda e: e.get("mtime") or 0)

    def _run_has_response_id(self, run_path: str, resp_filter: str) -> bool:
        resp_dir = os.path.join(run_path, "responses")
        if not os.path.isdir(resp_dir):
            return False
        for name in os.listdir(resp_dir):
            if resp_filter in name.lower():
                return True
        return False

    def _run_has_fulltext(self, run_path: str, text: str) -> bool:
        for sub in ("requests", "responses"):
            folder = os.path.join(run_path, sub)
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        chunk = f.read(2_000_000)
                    if text in chunk.lower():
                        return True
                except Exception:
                    continue
        return False

    def _parse_run_date(self, run_id: str) -> tuple[str, Optional[str], Optional[str]]:
        m = _RUN_RE.match(run_id)
        if not m:
            return "", None, None
        dd, mm, yyyy, hh, mi = m.groups()
        return f"{dd}.{mm}.{yyyy} {hh}:{mi}", f"{yyyy}{mm}{dd}", f"{yyyy}{mm}{dd}{hh}{mi}"

    def _normalize_date_filter(self, text: str) -> Optional[str]:
        t = text.strip()
        if not t:
            return None
        if re.match(r"^\\d{4}-\\d{2}-\\d{2}$", t):
            y, m, d = t.split("-")
            return f"{y}{m}{d}"
        if re.match(r"^\\d{2}\\.\\d{2}\\.\\d{4}$", t):
            d, m, y = t.split(".")
            return f"{y}{m}{d}"
        if re.match(r"^\\d{8}$", t):
            d, m, y = t[:2], t[2:4], t[4:]
            return f"{y}{m}{d}"
        return None
