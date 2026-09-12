"""Historie požadavků, odpovědí a dokončování uložených pracovních dávek."""

import re
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QListWidget, QListWidgetItem, QTabWidget
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from ..core.batch_completion import pending_batch_ids, batch_ids, read_state, read_batch_statuses
from .batch_view import project_name, history_batch_detail
from .design import column, row, label, button, text, editor
from .dialogs import msg_warning, dialog_save_file


class ResponseRequestPanel(QWidget):
    rerun = Signal(str)
    complete_batch = Signal(str)

    def __init__(self, log_dir, parent=None):
        super().__init__(parent)
        self.log_dir = log_dir
        self.batch_busy = False
        self.active_runs = set()
        layout = column(self)
        self.ed_run_id = text(placeholder="RUN ID")
        self.ed_resp_id = text(placeholder="Response ID")
        self.ed_date = text(placeholder="Datum DD.MM.YYYY")
        self.ed_fulltext = text(placeholder="Hledat v obsahu")
        layout.addWidget(row(self.ed_run_id, self.ed_resp_id, self.ed_date))
        layout.addWidget(
            row(
                self.ed_fulltext,
                button("Filtrovat", self.apply_filters),
                button("Obnovit", self.refresh_runs),
            )
        )
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        listing = QWidget()
        ll = column(listing, 12)
        self.lst_runs = QListWidget()
        self.lst_runs.setSelectionMode(QListWidget.ExtendedSelection)
        self.lst_runs.itemSelectionChanged.connect(self.load_entries_for_selected_run)
        ll.addWidget(label("Běhy"))
        ll.addWidget(self.lst_runs, 1)
        self.btn_rerun = button("Pokračovat vybraným během (ReRun)", self._rerun)
        ll.addWidget(self.btn_rerun)
        self.lst_entries = QListWidget()
        self.lst_entries.itemSelectionChanged.connect(self.load_selected_entry)
        ll.addWidget(label("Požadavky a odpovědi vybraných běhů"))
        ll.addWidget(self.lst_entries, 2)
        self.tabs.addTab(listing, "Přehled běhů")
        detail = QWidget()
        dl = column(detail, 12)
        self.txt_detail = editor(readonly=True)
        dl.addWidget(self.txt_detail, 1)
        dl.addWidget(
            row(
                button("Uložit TXT", self.save_selected_entry),
                button("Tisknout", self.print_selected_entry),
            )
        )
        self.tabs.addTab(detail, "Detail požadavku / odpovědi")
        self.refresh_runs()

    @staticmethod
    def _normalize_date_filter(value):
        value = value.strip()
        if not value:
            return ""
        for pattern in ("%d.%m.%Y", "%Y-%m-%d", "%d%m%Y"):
            try:
                return datetime.strptime(value, pattern).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return value

    @staticmethod
    def _parse_run_date(name):
        match = re.match(r"RUN_(\d{12})", name)
        if match:
            try:
                date = datetime.strptime(match[1], "%d%m%Y%H%M")
                return date.strftime("%d.%m.%Y"), date.strftime("%Y-%m-%d"), date.timestamp()
            except ValueError:
                pass
        return "", "", 0

    @staticmethod
    def _collect_entries(directory):
        entries = []
        for folder, kind in (("requests", "Požadavek"), ("responses", "Odpověď")):
            for path in (Path(directory) / folder).glob("*"):
                if path.is_file() and path.suffix.lower() in (".json", ".jsonl", ".txt"):
                    entries.append(
                        dict(path=str(path), name=path.name, kind=kind, mtime=path.stat().st_mtime)
                    )
        return sorted(entries, key=lambda record: record["mtime"])

    def refresh_runs(self):
        self._runs = []
        for path in Path(self.log_dir).glob("RUN_*"):
            if path.is_dir():
                display, date, timestamp = self._parse_run_date(path.name)
                self._runs.append(
                    dict(
                        id=path.name,
                        path=str(path),
                        date_label=display,
                        date_key=date,
                        ts_key=timestamp,
                    )
                )
        self._runs.sort(key=lambda record: (record["ts_key"], record["id"]), reverse=True)
        self.apply_filters()

    def apply_filters(self):
        snapshots = read_batch_statuses(self.log_dir)
        selected = {item.data(Qt.UserRole)["id"] for item in self.lst_runs.selectedItems()}
        current = self.lst_runs.currentItem()
        current_id = current.data(Qt.UserRole)["id"] if current else None
        self.lst_runs.clear()
        date = self._normalize_date_filter(self.ed_date.text())
        for run in self._runs:
            if (
                self.ed_run_id.text().lower() not in run["id"].lower()
                or date
                and date != run["date_key"]
            ):
                continue
            queries = [
                value.strip().lower()
                for value in (self.ed_resp_id.text(), self.ed_fulltext.text())
                if value.strip()
            ]
            if queries:
                contents = []
                for entry in self._collect_entries(run["path"]):
                    try:
                        contents.append(
                            Path(entry["path"])
                            .read_text(encoding="utf-8", errors="replace")
                            .lower()
                        )
                    except OSError:
                        continue
                if not all(any(query in content for content in contents) for query in queries):
                    continue
            from .dialogs import STATES

            state = read_state(run["path"])
            status = state.get("status", "")
            suffix = " · " + STATES.get(status, status) if status else ""
            project = project_name(state)
            title = run["id"] + " · " + run["date_label"] + suffix
            if project:
                title += " · " + project
            detail = history_batch_detail(state, snapshots)
            if detail:
                title += "\n" + detail
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, run)
            self.lst_runs.addItem(item)
            if pending_batch_ids(state):
                action = button(
                    "Dokončit",
                    lambda checked=False, rid=run["id"]: self.complete_batch.emit(rid),
                    "Primary",
                )
                action.setEnabled(not self.batch_busy and run["id"] not in self.active_runs)
                action.setToolTip("Ověřit stav pracovní dávky a převzít dostupné výsledky do původního OUT.")
                title_label = label(title)
                title_label.setWordWrap(True)
                title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
                widget = row(title_label, action)
                widget.setObjectName("HistoryRunRow")
                widget.setAutoFillBackground(True)
                widget.layout().setStretch(0, 1)
                item.setSizeHint(widget.sizeHint())
                self.lst_runs.setItemWidget(item, widget)
            if run["id"] == current_id:
                from PySide6.QtCore import QItemSelectionModel

                self.lst_runs.setCurrentItem(item, QItemSelectionModel.NoUpdate)
            item.setSelected(run["id"] in selected)
        self._update_rerun_action()

    def set_batch_busy(self, busy):
        self.batch_busy = busy
        self.apply_filters()

    def _update_rerun_action(self):
        item = self.lst_runs.currentItem()
        state = read_state(item.data(Qt.UserRole)["path"]) if item else {}
        self.btn_rerun.setEnabled(
            bool(item)
            and not batch_ids(state)
            and not self.batch_busy
            and item.data(Qt.UserRole)["id"] not in self.active_runs
        )
        self.btn_rerun.setToolTip(
            "Odeslané pracovní dávky převezměte tlačítkem Dokončit." if batch_ids(state) else ""
        )

    def load_entries_for_selected_run(self):
        self._update_rerun_action()
        for index in range(self.lst_runs.count()):
            item = self.lst_runs.item(index)
            widget = self.lst_runs.itemWidget(item)
            if widget:
                color = "#e8f0f8" if item.isSelected() else "#ffffff"
                widget.setStyleSheet(f"QWidget#HistoryRunRow {{ background: {color}; }}")
        self.lst_entries.clear()
        self.txt_detail.clear()
        entries = []
        for item in self.lst_runs.selectedItems():
            run = item.data(Qt.UserRole)
            entries.extend(
                dict(entry, run_id=run["id"])
                for entry in self._collect_entries(run["path"])
            )
        for entry in sorted(entries, key=lambda value: value["mtime"]):
            item = QListWidgetItem(
                entry["kind"] + " · " + entry["name"] + "\n" + entry["run_id"]
            )
            item.setData(Qt.UserRole, entry)
            self.lst_entries.addItem(item)

    def load_selected_entry(self):
        item = self.lst_entries.currentItem()
        if not item:
            return
        try:
            self.txt_detail.setPlainText(
                Path(item.data(Qt.UserRole)["path"]).read_text(
                    encoding="utf-8", errors="replace"
                )
            )
            self.tabs.setCurrentIndex(1)
        except OSError as exc:
            msg_warning(self, "Načtení detailu", str(exc))

    def save_selected_entry(self):
        path, _ = dialog_save_file(
            self, "Uložit detail", "request-response.txt", "Text (*.txt)"
        )
        if path:
            try:
                Path(path).write_text(self.txt_detail.toPlainText(), encoding="utf-8")
            except OSError as exc:
                msg_warning(self, "Uložení detailu", str(exc))

    def print_selected_entry(self):
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.Accepted:
            self.txt_detail.document().print_(printer)

    def _rerun(self):
        item = self.lst_runs.currentItem()
        if item and self.btn_rerun.isEnabled():
            self.rerun.emit(item.data(Qt.UserRole)["id"])
