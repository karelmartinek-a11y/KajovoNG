"""Run Explorer: lidský i forenzní pohled na běhy, kroky a artefakty."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.batch_completion import batch_ids, pending_batch_ids, read_state
from ..core.run_bundle import HistoryIndex, LegacyRunAdapter
from .batch_view import history_batch_detail, project_name
from .design import button, label, row, table, text
from .dialogs import dialog_save_file, msg_info, msg_warning

UNKNOWN = "Není evidováno"
MAX_PREVIEW_BYTES = 1024 * 1024


def _pretty(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value)


def _short(value: str, limit: int = 160) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _human_time(value: str) -> str:
    parsed = _parse_iso(value)
    return parsed.astimezone().strftime("%d.%m.%Y %H:%M:%S") if parsed else UNKNOWN


def _duration(start: str, end: str) -> str:
    first, second = _parse_iso(start), _parse_iso(end)
    if not first:
        return UNKNOWN
    second = second or datetime.now(timezone.utc)
    seconds = max(0, int((second - first).total_seconds()))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _group_label(value: str) -> str:
    parsed = _parse_iso(value)
    if not parsed:
        return "Starší"
    local = parsed.astimezone().date()
    today = datetime.now().astimezone().date()
    if local == today:
        return "Dnes"
    if local == today - timedelta(days=1):
        return "Včera"
    if local >= today - timedelta(days=6):
        return "Tento týden"
    return local.strftime("%d.%m.%Y")


class TechnicalViewer(QWidget):
    """Raw viewer s hledáním; důkazní obsah se nerediguje ani nepřepisuje."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Hledat v technickém detailu")
        self.search.returnPressed.connect(self.find_next)
        controls.addWidget(self.search, 1)
        controls.addWidget(button("Další", self.find_next))
        controls.addWidget(button("Kopírovat vše", self.copy_all))
        layout.addLayout(controls)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.text, 1)

    def set_value(self, value) -> None:
        self.text.setPlainText(_pretty(value))
        self.text.moveCursor(QTextCursor.Start)

    def clear(self) -> None:
        self.text.clear()

    def find_next(self) -> None:
        query = self.search.text()
        if not query:
            return
        if not self.text.find(query):
            self.text.moveCursor(QTextCursor.Start)
            self.text.find(query)

    def copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.text.toPlainText())


class StepDetailDialog(QDialog):
    def __init__(self, adapter: LegacyRunAdapter, step: dict, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.step = step
        self.setWindowTitle("Detail kroku · " + str(step.get("title") or step.get("stage") or UNKNOWN))
        self.resize(1050, 760)
        layout = QVBoxLayout(self)
        heading = QLabel(str(step.get("title") or step.get("stage") or UNKNOWN))
        heading.setStyleSheet("font-size:18px;font-weight:600;")
        layout.addWidget(heading)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        step_id = str(step.get("step_id") or "")

        overview = TechnicalViewer()
        overview.set_value(
            {
                "stav": step.get("status") or UNKNOWN,
                "stage": step.get("stage") or UNKNOWN,
                "typ": step.get("kind") or UNKNOWN,
                "model": step.get("model") or UNKNOWN,
                "reasoning": step.get("reasoning_effort") or UNKNOWN,
                "zahájeno": step.get("started_at") or UNKNOWN,
                "dokončeno": step.get("finished_at") or UNKNOWN,
                "lidské_shrnutí": step.get("human_summary") or UNKNOWN,
                "technické_shrnutí": step.get("technical_summary") or UNKNOWN,
            }
        )
        tabs.addTab(overview, "Přehled")
        sources = (
            (
                "Vstupy",
                [
                    item.get("full_payload", item)
                    for item in adapter.requests()
                    if not step_id or item.get("step_id") == step_id
                ],
            ),
            (
                "Odpovědi",
                [
                    item
                    for item in adapter.responses()
                    if not step_id or item.get("step_id") == step_id
                ],
            ),
            (
                "Soubory",
                [
                    item
                    for item in adapter.artifacts()
                    if not step_id or item.get("step_id") == step_id
                ],
            ),
            (
                "Validace",
                [
                    item
                    for item in adapter.validations()
                    if not step_id or item.get("step_id") == step_id
                ],
            ),
            (
                "Události",
                [
                    item
                    for item in adapter.events()
                    if not step_id or item.get("step_id") == step_id
                ],
            ),
        )
        for title, value in sources:
            viewer = TechnicalViewer()
            viewer.set_value(value)
            tabs.addTab(viewer, title)
        technical = TechnicalViewer()
        technical.set_value(step)
        tabs.addTab(technical, "Technické")


class ResponseRequestPanel(QWidget):
    """Interaktivní Historie. Název třídy zůstává kompatibilní se starší aplikací."""

    rerun = Signal(str)
    complete_batch = Signal(str)
    continue_run = Signal(str, str)
    clone_run = Signal(str)
    repair_run = Signal(str, str)
    reuse_artifacts = Signal(str, object)

    def __init__(self, log_dir, parent=None):
        super().__init__(parent)
        self.log_dir = str(Path(log_dir))
        self.index = HistoryIndex(self.log_dir)
        self.batch_busy = False
        self.active_runs = set()
        self._runs = []
        self._adapter: LegacyRunAdapter | None = None
        self._selected_run_id = ""
        self._reverse_lineage = {}
        self._selected_response = None
        self._selected_artifact = None
        self._build_ui()
        self.refresh_runs()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.ed_run_id = text(placeholder="RUN ID")
        self.ed_resp_id = text(placeholder="Response ID")
        self.ed_date = text(placeholder="Datum DD.MM.YYYY")
        self.ed_fulltext = text(placeholder="Hledat v historii")
        self.period = QComboBox()
        self.period.addItems(
            [
                "Vše",
                "Dnes",
                "Včera",
                "Posledních 7 dní",
                "Posledních 30 dní",
                "Tento měsíc",
                "Vlastní datum",
            ]
        )
        self.project_filter = QComboBox()
        self.mode_filter = QComboBox()
        self.status_filter = QComboBox()
        self.model_filter = QComboBox()
        for combo in (
            self.project_filter,
            self.mode_filter,
            self.status_filter,
            self.model_filter,
        ):
            combo.addItem("Vše")
        first = QHBoxLayout()
        first.addWidget(self.ed_fulltext, 2)
        first.addWidget(self.period)
        first.addWidget(self.ed_date)
        first.addWidget(self.ed_run_id)
        first.addWidget(self.ed_resp_id)
        layout.addLayout(first)
        second = QHBoxLayout()
        for title, combo in (
            ("Projekt", self.project_filter),
            ("Režim", self.mode_filter),
            ("Stav", self.status_filter),
            ("Model", self.model_filter),
        ):
            second.addWidget(QLabel(title))
            second.addWidget(combo)
        self.chk_error = QCheckBox("Má chybu")
        self.chk_batch = QCheckBox("Má BATCH")
        self.chk_checkpoint = QCheckBox("Má checkpoint")
        self.chk_output = QCheckBox("Má výstupy")
        self.chk_lineage = QCheckBox("Navazuje")
        for check in (
            self.chk_error,
            self.chk_batch,
            self.chk_checkpoint,
            self.chk_output,
            self.chk_lineage,
        ):
            second.addWidget(check)
        second.addStretch(1)
        second.addWidget(button("Reset", self.reset_filters))
        second.addWidget(button("Obnovit", self.refresh_runs, "Primary"))
        second.addWidget(button("Exportovat běh", self.export_selected_run))
        second.addWidget(button("Otevřít Run Bundle", self.open_bundle))
        layout.addLayout(second)
        for widget in (self.ed_run_id, self.ed_resp_id, self.ed_date, self.ed_fulltext):
            widget.returnPressed.connect(self.apply_filters)
        for combo in (
            self.period,
            self.project_filter,
            self.mode_filter,
            self.status_filter,
            self.model_filter,
        ):
            combo.currentTextChanged.connect(lambda _value: self.apply_filters())
        for check in (
            self.chk_error,
            self.chk_batch,
            self.chk_checkpoint,
            self.chk_output,
            self.chk_lineage,
        ):
            check.toggled.connect(lambda _value: self.apply_filters())

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.addWidget(label("Běhy", "Heading"))
        self.lst_runs = QListWidget()
        self.lst_runs.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lst_runs.itemSelectionChanged.connect(self.load_entries_for_selected_run)
        self.lst_runs.itemDoubleClicked.connect(lambda _item: self.tabs.setCurrentIndex(0))
        left_layout.addWidget(self.lst_runs, 1)
        self.btn_rerun = button("ReRun jako nový běh", self._rerun)
        self.btn_continue = button("Pokračovat od checkpointu", self._continue)
        self.btn_clone = button("Klonovat jako nový běh", self._clone)
        left_layout.addWidget(self.btn_rerun)
        left_layout.addWidget(self.btn_continue)
        left_layout.addWidget(self.btn_clone)
        splitter.addWidget(left)
        self.tabs = QTabWidget()
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        self._build_overview_tab()
        self._build_timeline_tab()
        self._build_responses_tab()
        self._build_files_tab()
        self._build_events_tab()
        self._build_lineage_tab()
        self._build_technical_tab()
        self.lst_entries = self.response_list
        self.txt_detail = self.response_human

    def _build_overview_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        body.setContentsMargins(10, 10, 10, 10)
        self.overview_title = QLabel("Vyberte běh")
        self.overview_title.setStyleSheet("font-size:20px;font-weight:600;")
        body.addWidget(self.overview_title)
        self.integrity_label = QLabel("")
        self.integrity_label.setWordWrap(True)
        body.addWidget(self.integrity_label)
        form = QFormLayout()
        self.overview_fields = {}
        for key, title in (
            ("project", "Projekt"),
            ("run_id", "RUN ID"),
            ("mode", "Režim"),
            ("status", "Stav"),
            ("started", "Zahájeno"),
            ("finished", "Dokončeno"),
            ("duration", "Trvání"),
            ("models", "Modely"),
            ("requests", "API požadavky"),
            ("responses", "Odpovědi"),
            ("errors", "Chyby"),
            ("batch", "BATCH"),
            ("inputs", "Vstupy"),
            ("outputs", "Výstupy"),
            ("parent", "Nadřazený běh"),
        ):
            value = QLabel(UNKNOWN)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setWordWrap(True)
            self.overview_fields[key] = value
            form.addRow(title, value)
        body.addLayout(form)
        self.overview_summary = QTextEdit()
        self.overview_summary.setReadOnly(True)
        self.overview_summary.setMaximumHeight(150)
        body.addWidget(QLabel("Shrnutí"))
        body.addWidget(self.overview_summary)
        checkpoint_row = QHBoxLayout()
        checkpoint_row.addWidget(QLabel("Checkpoint"))
        self.checkpoint_combo = QComboBox()
        self.checkpoint_combo.currentIndexChanged.connect(self._update_actions)
        checkpoint_row.addWidget(self.checkpoint_combo, 1)
        body.addLayout(checkpoint_row)
        actions = QHBoxLayout()
        self.btn_overview_continue = button("Pokračovat", self._continue, "Primary")
        self.btn_overview_clone = button("Klonovat", self._clone)
        self.btn_overview_rerun = button("ReRun", self._rerun)
        self.btn_overview_repair = button("Opravit", self._repair)
        self.btn_overview_outputs = button("Otevřít výstupy", self.open_outputs)
        self.btn_overview_bundle = button("Otevřít Run Bundle", self.open_bundle)
        for action in (
            self.btn_overview_continue,
            self.btn_overview_clone,
            self.btn_overview_rerun,
            self.btn_overview_repair,
            self.btn_overview_outputs,
            self.btn_overview_bundle,
        ):
            actions.addWidget(action)
        actions.addStretch(1)
        body.addLayout(actions)
        body.addStretch(1)
        self.tabs.addTab(page, "Přehled")

    def _build_timeline_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        self.timeline = QTreeWidget()
        self.timeline.setHeaderLabels(
            [
                "#",
                "Krok",
                "Stav",
                "Čas",
                "Model",
                "Reasoning",
                "Requesty",
                "Response",
                "Artefakty",
                "Checkpoint",
            ]
        )
        self.timeline.setRootIsDecorated(False)
        self.timeline.setAlternatingRowColors(True)
        self.timeline.itemDoubleClicked.connect(self.open_step_detail)
        body.addWidget(self.timeline, 1)
        body.addWidget(
            QLabel(
                "Dvojklikem otevřete vstupy, odpovědi, soubory, validace, události a raw detail kroku."
            )
        )
        self.tabs.addTab(page, "Průběh")

    def _build_responses_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.response_status_filter = QComboBox()
        self.response_status_filter.addItems(
            ["Vše", "completed", "incomplete", "failed", "error", "unknown"]
        )
        self.response_id_filter = QLineEdit()
        self.response_id_filter.setPlaceholderText("Response ID")
        filters.addWidget(QLabel("Stav"))
        filters.addWidget(self.response_status_filter)
        filters.addWidget(self.response_id_filter, 1)
        filters.addWidget(button("Filtrovat", self.render_responses))
        body.addLayout(filters)
        split = QSplitter(Qt.Horizontal)
        self.response_list = QListWidget()
        self.response_list.currentItemChanged.connect(
            lambda _now, _old: self.load_selected_entry()
        )
        split.addWidget(self.response_list)
        right = QTabWidget()
        self.response_human = QTextEdit()
        self.response_human.setReadOnly(True)
        self.response_technical = TechnicalViewer()
        right.addTab(self.response_human, "Lidsky")
        right.addTab(self.response_technical, "Technický detail")
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        body.addWidget(split, 1)
        actions = QHBoxLayout()
        actions.addWidget(button("Uložit TXT", self.save_selected_entry))
        actions.addWidget(button("Tisknout", self.print_selected_entry))
        actions.addStretch(1)
        body.addLayout(actions)
        self.tabs.addTab(page, "Odpovědi")

    def _build_files_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        self.artifact_table = table(
            ["Název", "Role", "Krok", "Velikost", "SHA-256", "Čas", "Zdroj", "Reuse"]
        )
        self.artifact_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.artifact_table.itemSelectionChanged.connect(self._artifact_selected)
        body.addWidget(self.artifact_table, 1)
        actions = QHBoxLayout()
        for action in (
            button("Otevřít", self.open_artifact),
            button("Náhled", self.preview_artifact),
            button("Uložit jako", self.save_artifact_as),
            button("Použít v novém běhu", self.reuse_selected_artifacts, "Primary"),
            button("Zobrazit původ", self.show_artifact_origin),
        ):
            actions.addWidget(action)
        actions.addStretch(1)
        body.addLayout(actions)
        self.artifact_preview = TechnicalViewer()
        self.artifact_preview.setMinimumHeight(180)
        body.addWidget(self.artifact_preview)
        self.tabs.addTab(page, "Soubory")

    def _build_events_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        filters = QHBoxLayout()
        self.event_severity = QComboBox()
        self.event_severity.addItems(["Vše", "info", "warning", "error", "unknown"])
        self.event_type = QLineEdit()
        self.event_type.setPlaceholderText("Typ / step / request / response / artifact")
        filters.addWidget(QLabel("Severity"))
        filters.addWidget(self.event_severity)
        filters.addWidget(self.event_type, 1)
        filters.addWidget(button("Filtrovat", self.render_events))
        body.addLayout(filters)
        self.event_table = table(
            ["#", "Čas", "Krok", "Typ", "Severity", "Lidská zpráva"]
        )
        self.event_table.itemSelectionChanged.connect(self._event_selected)
        body.addWidget(self.event_table, 1)
        self.event_detail = TechnicalViewer()
        self.event_detail.setMinimumHeight(180)
        body.addWidget(self.event_detail)
        self.tabs.addTab(page, "Události")

    def _build_lineage_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        self.lineage_tree = QTreeWidget()
        self.lineage_tree.setHeaderLabels(
            ["Vztah", "RUN", "Checkpoint", "Čas", "Poznámka"]
        )
        self.lineage_tree.itemDoubleClicked.connect(self._lineage_open)
        self.lineage_tree.itemSelectionChanged.connect(self._lineage_selected)
        body.addWidget(self.lineage_tree, 1)
        self.lineage_detail = TechnicalViewer()
        self.lineage_detail.setMinimumHeight(200)
        body.addWidget(self.lineage_detail)
        self.tabs.addTab(page, "Návaznosti")

    def _build_technical_tab(self):
        page = QWidget()
        body = QVBoxLayout(page)
        self.technical_tabs = QTabWidget()
        self.raw_bundle = TechnicalViewer()
        self.raw_run = TechnicalViewer()
        self.raw_state = TechnicalViewer()
        self.raw_index = TechnicalViewer()
        for widget, title in (
            (self.raw_bundle, "bundle.json"),
            (self.raw_run, "run.json"),
            (self.raw_state, "run_state.json"),
            (self.raw_index, "Index / integrita"),
        ):
            self.technical_tabs.addTab(widget, title)
        body.addWidget(self.technical_tabs, 1)
        self.tabs.addTab(page, "Technické")

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
                return (
                    date.strftime("%d.%m.%Y"),
                    date.strftime("%Y-%m-%d"),
                    date.timestamp(),
                )
            except ValueError:
                pass
        return "", "", 0

    @staticmethod
    def _collect_entries(directory):
        adapter = LegacyRunAdapter(directory)
        entries = []
        for kind, records in (
            ("Požadavek", adapter.requests()),
            ("Odpověď", adapter.responses()),
        ):
            for record in records:
                path = str(record.get("source_path") or "")
                entries.append(
                    {
                        "path": path,
                        "name": Path(path).name
                        if path
                        else str(
                            record.get("response_id")
                            or record.get("request_record_id")
                            or record.get("response_record_id")
                            or kind
                        ),
                        "kind": kind,
                        "mtime": Path(path).stat().st_mtime
                        if path and Path(path).exists()
                        else 0,
                    }
                )
        return sorted(entries, key=lambda record: record["mtime"])

    def reset_filters(self):
        for widget in (
            self.ed_run_id,
            self.ed_resp_id,
            self.ed_date,
            self.ed_fulltext,
        ):
            widget.clear()
        self.period.setCurrentText("Vše")
        for combo in (
            self.project_filter,
            self.mode_filter,
            self.status_filter,
            self.model_filter,
        ):
            combo.setCurrentText("Vše")
        for check in (
            self.chk_error,
            self.chk_batch,
            self.chk_checkpoint,
            self.chk_output,
            self.chk_lineage,
        ):
            check.setChecked(False)
        self.apply_filters()

    def refresh_runs(self):
        selected = self._selected_run_id
        self._runs = self.index.refresh()
        self._reverse_lineage = self.index.reverse_lineage()
        self._refill_filter_values()
        self.apply_filters()
        if selected:
            for index in range(self.lst_runs.count()):
                item = self.lst_runs.item(index)
                data = item.data(Qt.UserRole)
                if isinstance(data, dict) and data.get("run_id") == selected:
                    self.lst_runs.setCurrentItem(item)
                    break

    def _refill_filter_values(self):
        mappings = (
            (
                self.project_filter,
                sorted(
                    {
                        str(item.get("project") or "")
                        for item in self._runs
                        if item.get("project")
                    }
                ),
            ),
            (
                self.mode_filter,
                sorted(
                    {
                        str(item.get("mode") or "")
                        for item in self._runs
                        if item.get("mode")
                    }
                ),
            ),
            (
                self.status_filter,
                sorted(
                    {
                        str(item.get("status") or "")
                        for item in self._runs
                        if item.get("status")
                    }
                ),
            ),
            (
                self.model_filter,
                sorted(
                    {
                        model
                        for item in self._runs
                        for model in item.get("models", [])
                        if model
                    }
                ),
            ),
        )
        for combo, values in mappings:
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Vše")
            combo.addItems(values)
            combo.setCurrentText(
                current if current in values or current == "Vše" else "Vše"
            )
            combo.blockSignals(False)

    def _period_match(self, record: dict) -> bool:
        choice = self.period.currentText()
        if choice == "Vše":
            return True
        created = _parse_iso(str(record.get("created_at") or ""))
        if not created:
            _display, legacy_date, _timestamp = self._parse_run_date(
                str(record.get("run_id") or "")
            )
            if legacy_date:
                try:
                    created = datetime.strptime(legacy_date, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    created = None
        if not created:
            return False
        local_date = created.astimezone().date()
        today = datetime.now().astimezone().date()
        if choice == "Dnes":
            return local_date == today
        if choice == "Včera":
            return local_date == today - timedelta(days=1)
        if choice == "Posledních 7 dní":
            return local_date >= today - timedelta(days=6)
        if choice == "Posledních 30 dní":
            return local_date >= today - timedelta(days=29)
        if choice == "Tento měsíc":
            return local_date.year == today.year and local_date.month == today.month
        if choice == "Vlastní datum":
            expected = self._normalize_date_filter(self.ed_date.text())
            return bool(expected) and local_date.strftime("%Y-%m-%d") == expected
        return True

    def apply_filters(self):
        current_id = self._selected_run_id
        self.lst_runs.clear()
        query = self.ed_fulltext.text().strip().casefold()
        run_query = self.ed_run_id.text().strip().casefold()
        response_query = self.ed_resp_id.text().strip().casefold()
        legacy_date = self._normalize_date_filter(self.ed_date.text())
        explicit_date = bool(legacy_date) and self.period.currentText() == "Vše"
        snapshots = {}
        try:
            from ..core.batch_completion import read_batch_statuses

            snapshots = read_batch_statuses(self.log_dir)
        except Exception:
            snapshots = {}
        for record in self._runs:
            if run_query and run_query not in str(record.get("run_id") or "").casefold():
                continue
            if query and query not in str(record.get("search_text") or ""):
                continue
            if response_query and not any(
                response_query in value.casefold()
                for value in record.get("response_ids", [])
            ):
                continue
            if explicit_date:
                created = _parse_iso(str(record.get("created_at") or ""))
                date_key = (
                    created.astimezone().strftime("%Y-%m-%d")
                    if created
                    else self._parse_run_date(record["run_id"])[1]
                )
                if date_key != legacy_date:
                    continue
            if not self._period_match(record):
                continue
            if (
                self.project_filter.currentText() != "Vše"
                and record.get("project") != self.project_filter.currentText()
            ):
                continue
            if (
                self.mode_filter.currentText() != "Vše"
                and record.get("mode") != self.mode_filter.currentText()
            ):
                continue
            if (
                self.status_filter.currentText() != "Vše"
                and record.get("status") != self.status_filter.currentText()
            ):
                continue
            if (
                self.model_filter.currentText() != "Vše"
                and self.model_filter.currentText() not in record.get("models", [])
            ):
                continue
            if self.chk_error.isChecked() and not record.get("has_error"):
                continue
            if self.chk_batch.isChecked() and not record.get("has_batch"):
                continue
            if self.chk_checkpoint.isChecked() and not record.get("checkpoints"):
                continue
            if self.chk_output.isChecked() and not record.get("has_output"):
                continue
            if self.chk_lineage.isChecked() and not record.get("has_lineage"):
                continue
            run_id = str(record["run_id"])
            state = read_state(Path(self.log_dir) / run_id)
            project = record.get("project") or project_name(state)
            title = (
                f"{_group_label(str(record.get('created_at') or ''))} · {run_id}\n"
                f"{project or UNKNOWN} · {record.get('mode') or UNKNOWN} · {record.get('status') or UNKNOWN}"
                f" · kroky {record.get('steps', 0)} · odpovědi {record.get('responses', 0)}"
                f" · soubory {record.get('artifacts', 0)}"
            )
            detail = history_batch_detail(state, snapshots)
            if detail:
                title += "\n" + detail
            if record.get("legacy"):
                title += " · LEGACY"
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, record)
            item.setToolTip(
                "Kliknutím otevřete Run Explorer; stará evidence se při prohlížení nemění."
            )
            self.lst_runs.addItem(item)
            if pending_batch_ids(state):
                action = button(
                    "Dokončit",
                    lambda checked=False, rid=run_id: self.complete_batch.emit(rid),
                    "Primary",
                )
                action.setEnabled(
                    not self.batch_busy and run_id not in self.active_runs
                )
                action.setToolTip(
                    "Převezme výsledky již odeslané dávky. Nevytváří nový generativní požadavek."
                )
                title_label = QLabel(title)
                title_label.setWordWrap(True)
                title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
                widget = row(title_label, action)
                widget.setObjectName("HistoryRunRow")
                widget.setAutoFillBackground(True)
                widget.layout().setStretch(0, 1)
                item.setSizeHint(widget.sizeHint())
                self.lst_runs.setItemWidget(item, widget)
            if run_id == current_id:
                self.lst_runs.setCurrentItem(item)
        if self.lst_runs.currentItem() is None and self.lst_runs.count():
            self.lst_runs.setCurrentRow(0)
        self._update_actions()

    def set_batch_busy(self, busy):
        self.batch_busy = bool(busy)
        self.apply_filters()

    def _selected_summary(self):
        item = self.lst_runs.currentItem()
        value = item.data(Qt.UserRole) if item else None
        return value if isinstance(value, dict) else None

    def _selected_checkpoint_id(self) -> str:
        return str(self.checkpoint_combo.currentData() or "")

    def _update_actions(self):
        summary = self._selected_summary()
        has_run = bool(summary)
        legacy = bool(summary and summary.get("legacy"))
        checkpoint = bool(self._selected_checkpoint_id())
        blocked = bool(
            summary
            and (
                self.batch_busy
                or summary.get("run_id") in self.active_runs
            )
        )
        state = (
            read_state(Path(self.log_dir) / summary["run_id"])
            if summary
            else {}
        )
        has_sent_batch = bool(batch_ids(state))
        can_continue = (
            has_run
            and not legacy
            and checkpoint
            and not blocked
            and not has_sent_batch
        )
        can_clone = has_run and not blocked
        self.btn_continue.setEnabled(can_continue)
        self.btn_overview_continue.setEnabled(can_continue)
        self.btn_clone.setEnabled(can_clone)
        self.btn_overview_clone.setEnabled(can_clone)
        self.btn_rerun.setEnabled(can_continue)
        self.btn_overview_rerun.setEnabled(can_continue)
        self.btn_overview_repair.setEnabled(can_continue)
        if legacy:
            tooltip = (
                "Legacy běh nemá explicitní bezpečný checkpoint; lze jej pouze klonovat, "
                "pokud má přesně uložené zadání."
            )
        elif has_sent_batch:
            tooltip = (
                "Již odeslaný BATCH se dokončuje v původním běhu; nevytváří se duplicitní submit."
            )
        elif not checkpoint:
            tooltip = "Běh nemá zvolený bezpečný checkpoint."
        else:
            tooltip = (
                "Vznikne nový navázaný běh; původní evidence zůstane neměnná."
            )
        self.btn_rerun.setToolTip(tooltip)
        self.btn_continue.setToolTip(tooltip)

    def load_entries_for_selected_run(self):
        summary = self._selected_summary()
        if not summary:
            self._adapter = None
            self._selected_run_id = ""
            return
        self._selected_run_id = str(summary["run_id"])
        self._adapter = LegacyRunAdapter(
            Path(self.log_dir) / self._selected_run_id
        )
        for index in range(self.lst_runs.count()):
            item = self.lst_runs.item(index)
            widget = self.lst_runs.itemWidget(item)
            if widget:
                color = "#e8f0f8" if item.isSelected() else "#ffffff"
                widget.setStyleSheet(
                    f"QWidget#HistoryRunRow {{ background: {color}; }}"
                )
        self.render_overview()
        self.render_timeline()
        self.render_responses()
        self.render_artifacts()
        self.render_events()
        self.render_lineage()
        self.render_technical()
        self._update_actions()

    def render_overview(self):
        if not self._adapter:
            return
        run = self._adapter.run_record()
        state = self._adapter.state()
        responses = self._adapter.responses()
        artifacts = self._adapter.artifacts()
        events = self._adapter.events()
        checkpoints = self._adapter.checkpoints()
        integrity = self._adapter.integrity()
        self.overview_title.setText(
            f"{run.get('project') or UNKNOWN} · {run.get('run_id') or self._selected_run_id}"
        )
        if integrity.get("valid"):
            self.integrity_label.setText(
                "Integrita ověřena · " + str(integrity.get("bundle_hash") or "")
            )
            self.integrity_label.setStyleSheet(
                "color:#176b3a;font-weight:600;"
            )
        elif integrity.get("status") == "legacy":
            self.integrity_label.setText(
                "Legacy evidence · integrita celého Run Bundle není evidována"
            )
            self.integrity_label.setStyleSheet(
                "color:#7a5c00;font-weight:600;"
            )
        else:
            self.integrity_label.setText(
                "Evidence je neúplná / změněná · "
                + "; ".join(integrity.get("errors") or [])
            )
            self.integrity_label.setStyleSheet(
                "color:#9f2d20;font-weight:600;"
            )
        inputs = [
            item.get("display_name")
            for item in artifacts
            if item.get("role")
            in {"user_input", "attached_file", "in_project_file"}
        ]
        outputs = [
            item.get("display_name")
            for item in artifacts
            if item.get("role")
            in {"generated_file", "modified_file", "batch_output", "log_export"}
        ]
        errors = sum(
            1
            for item in events
            if str(item.get("severity") or "").lower() == "error"
        )
        batches = run.get("related_batch_ids") or batch_ids(state)
        values = {
            "project": run.get("project") or UNKNOWN,
            "run_id": run.get("run_id") or self._selected_run_id,
            "mode": run.get("mode") or UNKNOWN,
            "status": run.get("status") or UNKNOWN,
            "started": _human_time(
                str(run.get("started_at") or run.get("created_at") or "")
            ),
            "finished": _human_time(str(run.get("finished_at") or "")),
            "duration": _duration(
                str(run.get("started_at") or run.get("created_at") or ""),
                str(run.get("finished_at") or ""),
            ),
            "models": ", ".join(run.get("model_summary") or []) or UNKNOWN,
            "requests": str(len(self._adapter.requests())),
            "responses": str(len(responses)),
            "errors": str(errors),
            "batch": ", ".join(batches) if batches else UNKNOWN,
            "inputs": ", ".join(str(value) for value in inputs)
            if inputs
            else UNKNOWN,
            "outputs": ", ".join(str(value) for value in outputs)
            if outputs
            else UNKNOWN,
            "parent": run.get("parent_run_id") or UNKNOWN,
        }
        for key, value in values.items():
            self.overview_fields[key].setText(str(value))
        summary = str(
            run.get("output_summary") or run.get("input_summary") or ""
        )
        if run.get("legacy"):
            summary = (
                summary
                + "\n\nLegacy běh: chybějící informace nejsou doplněny odhadem."
            ).strip()
        self.overview_summary.setPlainText(summary or UNKNOWN)
        self.checkpoint_combo.blockSignals(True)
        self.checkpoint_combo.clear()
        for checkpoint in checkpoints:
            safe = bool(checkpoint.get("safe_to_continue"))
            title = ("✓ " if safe else "○ ") + str(
                checkpoint.get("checkpoint_type")
                or checkpoint.get("checkpoint_id")
                or UNKNOWN
            )
            self.checkpoint_combo.addItem(
                title, checkpoint.get("checkpoint_id") if safe else ""
            )
        self.checkpoint_combo.blockSignals(False)

    def render_timeline(self):
        self.timeline.clear()
        if not self._adapter:
            return
        steps = self._adapter.steps()
        if not steps and self._adapter.legacy:
            root = QTreeWidgetItem(
                [
                    "",
                    "Legacy běh",
                    UNKNOWN,
                    UNKNOWN,
                    UNKNOWN,
                    UNKNOWN,
                    "0",
                    "0",
                    "0",
                    UNKNOWN,
                ]
            )
            root.setToolTip(
                1,
                "Starý běh nemá explicitní StepRecord; UI nevytváří kroky odhadem.",
            )
            self.timeline.addTopLevelItem(root)
            return
        for step in steps:
            item = QTreeWidgetItem(
                [
                    str(step.get("sequence") or ""),
                    str(step.get("title") or step.get("stage") or UNKNOWN),
                    str(step.get("status") or UNKNOWN),
                    _duration(
                        str(step.get("started_at") or ""),
                        str(step.get("finished_at") or ""),
                    ),
                    str(step.get("model") or UNKNOWN),
                    str(step.get("reasoning_effort") or UNKNOWN),
                    str(len(step.get("request_ids") or [])),
                    str(len(step.get("response_ids") or [])),
                    str(len(step.get("artifact_ids") or [])),
                    str(step.get("checkpoint_id") or ""),
                ]
            )
            item.setData(0, Qt.UserRole, step)
            self.timeline.addTopLevelItem(item)
        for index in range(self.timeline.columnCount()):
            self.timeline.resizeColumnToContents(index)

    def open_step_detail(self, item, _column=0):
        if not self._adapter:
            return
        step = item.data(0, Qt.UserRole)
        if isinstance(step, dict):
            StepDetailDialog(self._adapter, step, self).exec()

    def render_responses(self):
        self.response_list.clear()
        self.response_human.clear()
        self.response_technical.clear()
        if not self._adapter:
            return
        status_filter = self.response_status_filter.currentText()
        query = self.response_id_filter.text().strip().casefold()
        for record in self._adapter.responses():
            status = str(record.get("status") or "unknown")
            response_id = str(record.get("response_id") or "")
            if status_filter != "Vše" and status_filter not in status:
                continue
            if query and query not in response_id.casefold():
                continue
            title = f"{response_id or UNKNOWN} · {status}"
            incomplete = str(record.get("incomplete_reason") or "")
            if incomplete:
                title += " · " + incomplete
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, record)
            self.response_list.addItem(item)
        if self.response_list.count():
            self.response_list.setCurrentRow(0)

    def load_selected_entry(self):
        item = self.response_list.currentItem()
        if not item:
            self._selected_response = None
            return
        record = item.data(Qt.UserRole)
        if not isinstance(record, dict):
            return
        self._selected_response = record
        human = str(record.get("output_text") or "")
        if not human and record.get("full_response") is not None:
            human = _pretty(record.get("full_response"))
        self.response_human.setPlainText(human or UNKNOWN)
        self.response_technical.set_value(record)

    def save_selected_entry(self):
        if not self._selected_response:
            return
        path, _ = dialog_save_file(
            self, "Uložit detail", "response.txt", "Text (*.txt)"
        )
        if path:
            try:
                Path(path).write_text(
                    self.response_human.toPlainText(), encoding="utf-8"
                )
            except OSError as exc:
                msg_warning(self, "Uložení detailu", str(exc))

    def print_selected_entry(self):
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.Accepted:
            self.response_human.document().print_(printer)

    def render_artifacts(self):
        self.artifact_table.setRowCount(0)
        self.artifact_preview.clear()
        self._selected_artifact = None
        if not self._adapter:
            return
        for artifact in self._adapter.artifacts():
            row_index = self.artifact_table.rowCount()
            self.artifact_table.insertRow(row_index)
            values = [
                artifact.get("display_name") or UNKNOWN,
                artifact.get("role") or UNKNOWN,
                artifact.get("step_id") or UNKNOWN,
                str(
                    artifact.get("size_bytes")
                    if artifact.get("size_bytes") is not None
                    else UNKNOWN
                ),
                artifact.get("sha256") or UNKNOWN,
                _human_time(str(artifact.get("created_at") or "")),
                artifact.get("source") or UNKNOWN,
                "Ano" if artifact.get("reusable") else "Ne",
            ]
            for index, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.UserRole, artifact)
                self.artifact_table.setItem(row_index, index, cell)
        self.artifact_table.resizeColumnsToContents()

    def _artifact_selected(self):
        selected = (
            self.artifact_table.selectionModel().selectedRows()
            if self.artifact_table.selectionModel()
            else []
        )
        if not selected:
            self._selected_artifact = None
            return
        item = self.artifact_table.item(selected[0].row(), 0)
        self._selected_artifact = item.data(Qt.UserRole) if item else None

    def _artifact_path(self, artifact: dict) -> Path | None:
        if not self._adapter:
            return None
        relative = str(artifact.get("path_in_bundle") or "")
        if relative:
            candidate = self._adapter.root / relative
            if candidate.is_file():
                return candidate
        original = str(artifact.get("original_path") or "")
        if original and Path(original).is_file():
            return Path(original)
        return None

    def open_artifact(self):
        if not self._selected_artifact:
            return
        path = self._artifact_path(self._selected_artifact)
        if not path:
            msg_warning(self, "Artefakt", "Soubor již není lokálně dostupný.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def preview_artifact(self):
        if not self._selected_artifact:
            return
        path = self._artifact_path(self._selected_artifact)
        if not path:
            self.artifact_preview.set_value(
                {
                    "stav": "Soubor již není lokálně dostupný.",
                    "artefakt": self._selected_artifact,
                }
            )
            return
        try:
            raw = path.read_bytes()[: MAX_PREVIEW_BYTES + 1]
            truncated = len(raw) > MAX_PREVIEW_BYTES
            raw = raw[:MAX_PREVIEW_BYTES]
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                content = (
                    f"Binární soubor · {path.stat().st_size} B · SHA-256 "
                    f"{self._selected_artifact.get('sha256') or UNKNOWN}"
                )
            if truncated:
                content += (
                    "\n\n[UI náhled končí po 1 MiB. Kanonický artefakt zůstává celý a nezměněný.]"
                )
            self.artifact_preview.set_value(content)
        except OSError as exc:
            msg_warning(self, "Náhled artefaktu", str(exc))

    def save_artifact_as(self):
        if not self._selected_artifact:
            return
        source = self._artifact_path(self._selected_artifact)
        if not source:
            msg_warning(self, "Artefakt", "Soubor již není lokálně dostupný.")
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "Uložit artefakt", source.name
        )
        if target:
            try:
                shutil.copy2(source, target)
            except OSError as exc:
                msg_warning(self, "Uložení artefaktu", str(exc))

    def show_artifact_origin(self):
        if self._selected_artifact:
            self.artifact_preview.set_value(self._selected_artifact)

    def reuse_selected_artifacts(self):
        if not self._adapter:
            return
        rows = (
            self.artifact_table.selectionModel().selectedRows()
            if self.artifact_table.selectionModel()
            else []
        )
        identifiers = []
        for index in rows:
            item = self.artifact_table.item(index.row(), 0)
            artifact = item.data(Qt.UserRole) if item else None
            if (
                isinstance(artifact, dict)
                and artifact.get("reusable")
                and artifact.get("artifact_id")
            ):
                identifiers.append(artifact["artifact_id"])
        if not identifiers:
            msg_info(
                self,
                "Použít soubory",
                "Vyberte alespoň jeden znovupoužitelný artefakt nového Run Bundle.",
            )
            return
        self.reuse_artifacts.emit(self._selected_run_id, identifiers)

    def render_events(self):
        self.event_table.setRowCount(0)
        self.event_detail.clear()
        if not self._adapter:
            return
        severity = self.event_severity.currentText()
        query = self.event_type.text().strip().casefold()
        for event in self._adapter.events():
            event_severity = str(event.get("severity") or "unknown")
            searchable = " ".join(
                str(event.get(key) or "")
                for key in (
                    "event_type",
                    "type",
                    "step_id",
                    "related_request_id",
                    "related_response_id",
                    "related_artifact_ids",
                )
            ).casefold()
            if severity != "Vše" and event_severity != severity:
                continue
            if query and query not in searchable:
                continue
            row_index = self.event_table.rowCount()
            self.event_table.insertRow(row_index)
            values = [
                event.get("sequence") or "",
                _human_time(str(event.get("timestamp") or "")),
                event.get("step_id") or UNKNOWN,
                event.get("event_type") or event.get("type") or UNKNOWN,
                event_severity,
                event.get("human_message")
                or _short(str((event.get("data") or {}).get("msg") or ""))
                or UNKNOWN,
            ]
            for index, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setData(Qt.UserRole, event)
                self.event_table.setItem(row_index, index, cell)
        self.event_table.resizeColumnsToContents()

    def _event_selected(self):
        rows = (
            self.event_table.selectionModel().selectedRows()
            if self.event_table.selectionModel()
            else []
        )
        if not rows:
            return
        item = self.event_table.item(rows[0].row(), 0)
        if item:
            self.event_detail.set_value(item.data(Qt.UserRole))

    def render_lineage(self):
        self.lineage_tree.clear()
        self.lineage_detail.clear()
        if not self._adapter:
            return
        run = self._adapter.run_record()
        parent = str(run.get("parent_run_id") or "")
        if parent:
            item = QTreeWidgetItem(
                [
                    "Rodič",
                    parent,
                    str(run.get("continued_from_checkpoint_id") or ""),
                    "",
                    str(run.get("lineage_reason") or ""),
                ]
            )
            item.setData(0, Qt.UserRole, {"run_id": parent})
            self.lineage_tree.addTopLevelItem(item)
        for record in self._adapter.lineage():
            source = str(record.get("source_run_id") or "")
            item = QTreeWidgetItem(
                [
                    str(record.get("relation_type") or UNKNOWN),
                    source or UNKNOWN,
                    str(record.get("source_checkpoint_id") or ""),
                    _human_time(str(record.get("created_at") or "")),
                    str(record.get("notes") or ""),
                ]
            )
            item.setData(
                0, Qt.UserRole, {"run_id": source, "record": record}
            )
            self.lineage_tree.addTopLevelItem(item)
        for child in self._reverse_lineage.get(self._selected_run_id, []):
            target = str(child.get("target_run_id") or "")
            item = QTreeWidgetItem(
                [
                    "Potomek · " + str(child.get("relation_type") or UNKNOWN),
                    target or UNKNOWN,
                    str(child.get("source_checkpoint_id") or ""),
                    _human_time(str(child.get("created_at") or "")),
                    str(child.get("notes") or ""),
                ]
            )
            item.setData(
                0, Qt.UserRole, {"run_id": target, "record": child}
            )
            self.lineage_tree.addTopLevelItem(item)

    def _lineage_selected(self):
        item = self.lineage_tree.currentItem()
        if item:
            self.lineage_detail.set_value(item.data(0, Qt.UserRole))

    def _lineage_open(self, item, _column=0):
        data = item.data(0, Qt.UserRole)
        run_id = (
            str(data.get("run_id") or "") if isinstance(data, dict) else ""
        )
        if not run_id:
            return
        for index in range(self.lst_runs.count()):
            candidate = self.lst_runs.item(index)
            summary = candidate.data(Qt.UserRole)
            if isinstance(summary, dict) and summary.get("run_id") == run_id:
                self.lst_runs.setCurrentItem(candidate)
                return
        msg_info(
            self,
            "Návaznost",
            f"Běh {run_id} je mimo aktuální filtr. Resetujte filtry pro jeho otevření.",
        )

    def render_technical(self):
        if not self._adapter:
            return
        root = self._adapter.root
        if (root / "bundle.json").exists():
            self.raw_bundle.set_value(
                json.loads((root / "bundle.json").read_text(encoding="utf-8"))
            )
        else:
            self.raw_bundle.set_value(
                {"stav": "Legacy běh", "bundle.json": UNKNOWN}
            )
        self.raw_run.set_value(self._adapter.run_record())
        self.raw_state.set_value(self._adapter.state() or {"stav": UNKNOWN})
        self.raw_index.set_value(
            {
                "integrita": self._adapter.integrity(),
                "checkpointy": self._adapter.checkpoints(),
                "lineage": self._adapter.lineage(),
                "počty": {
                    "steps": len(self._adapter.steps()),
                    "events": len(self._adapter.events()),
                    "requests": len(self._adapter.requests()),
                    "responses": len(self._adapter.responses()),
                    "validations": len(self._adapter.validations()),
                    "artifacts": len(self._adapter.artifacts()),
                },
            }
        )

    def _rerun(self):
        if self._selected_run_id and self.btn_rerun.isEnabled():
            self.rerun.emit(self._selected_run_id)

    def _continue(self):
        checkpoint_id = self._selected_checkpoint_id()
        if (
            self._selected_run_id
            and checkpoint_id
            and self.btn_continue.isEnabled()
        ):
            self.continue_run.emit(self._selected_run_id, checkpoint_id)

    def _clone(self):
        if self._selected_run_id and self.btn_clone.isEnabled():
            self.clone_run.emit(self._selected_run_id)

    def _repair(self):
        checkpoint_id = self._selected_checkpoint_id()
        if (
            self._selected_run_id
            and checkpoint_id
            and self.btn_overview_repair.isEnabled()
        ):
            self.repair_run.emit(self._selected_run_id, checkpoint_id)

    def open_bundle(self):
        if not self._selected_run_id:
            return
        path = Path(self.log_dir) / self._selected_run_id
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

    def open_outputs(self):
        if not self._adapter:
            return
        state = self._adapter.state()
        out_dir = str(state.get("out_dir") or "")
        if out_dir and Path(out_dir).exists():
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(out_dir).resolve()))
            )
        else:
            msg_info(
                self,
                "Výstupy",
                "Výstupní adresář není evidován nebo již neexistuje. Archivované výstupy najdete na kartě Soubory.",
            )

    def export_selected_run(self):
        if not self._selected_run_id:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            "Exportovat Run Bundle",
            self._selected_run_id + ".zip",
            "ZIP (*.zip)",
        )
        if not target:
            return
        source = Path(self.log_dir) / self._selected_run_id
        try:
            target_path = Path(target)
            if target_path.suffix.lower() == ".zip":
                target_path = target_path.with_suffix("")
            archive = shutil.make_archive(
                str(target_path), "zip", root_dir=str(source)
            )
            msg_info(self, "Export Run Bundle", f"Exportováno: {archive}")
        except OSError as exc:
            msg_warning(self, "Export Run Bundle", str(exc))
