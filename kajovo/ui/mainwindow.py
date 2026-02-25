from __future__ import annotations
from .widgets import (
    msg_info,
    msg_warning,
    msg_critical,
    msg_question,
    StyledMessageDialog,
    dialog_open_file,
    dialog_save_file,
    dialog_select_dir,
)

import os
import subprocess
import json
import time
import shutil
import glob
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QSpinBox,
)

from ..core.config import AppSettings, SMTPSettings, save_settings, load_settings, DEFAULT_SETTINGS_FILE
from ..core.openai_client import OpenAIClient
from ..core.pipeline import RunWorker, UiRunConfig
from ..core.cascade_pipeline import CascadeRunWorker, CascadeRunConfig
from ..core.cascade_types import CascadeDefinition
from ..core.pricing import PriceTable
from ..core.receipt import ReceiptDB
from ..core.retry import CircuitBreaker, with_retry
from ..core.runlog import RunLogger, find_last_incomplete_run
from ..core.notifications import send_smtp_notification
from ..core.utils import ensure_dir, new_run_id
from ..core.secret_store import get_secret

from ..core.model_capabilities import ModelCapabilitiesCache, ModelProbeWorker, ModelCapabilities
from ..core.contracts import parse_json_strict, extract_text_from_response

from .filepanel import FilesPanel
from .vectorstores_panel import VectorStoresPanel
from .batch_panel import BatchPanel
from .github_panel import GitHubPanel
from .pricing_panel import PricingPanel
from .cascade_panel import CascadePanel
from .response_request_panel import ResponseRequestPanel
from .progress_dialog import ProgressDialog
from .theme import DARK_STYLESHEET
from .widgets import BusyPopup, style_progress_bar

EXPECTED_REPAIR_README = "readmerepair.txt"


def _caps_prev_id_explicitly_unsupported(caps: Optional[ModelCapabilities]) -> bool:
    if not caps:
        return False
    if caps.supports_previous_response_id:
        return False
    # Only treat as explicit if probe captured schema-style rejection.
    err = (caps.errors or {}).get("previous_response_id_param", "")
    return bool(err)


class MainWindow(QMainWindow):
    LOG_TABLE_MAX_ROWS = 600
    def __init__(self, settings: AppSettings):
        super().__init__()
        self.app_title = "Kájovo NG v2.0"
        self.setWindowTitle(self.app_title)
        self.setWindowFlags(Qt.Window)
        self.resize(1280, 860)

        # normalize paths to keep LOG/cache inside repo root
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if not settings.log_dir:
            settings.log_dir = "LOG"
        if not os.path.isabs(settings.log_dir):
            settings.log_dir = os.path.join(base_dir, settings.log_dir)
        if not settings.cache_dir:
            settings.cache_dir = "cache"
        if not os.path.isabs(settings.cache_dir):
            settings.cache_dir = os.path.join(base_dir, settings.cache_dir)
        self.s = settings
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.breaker = CircuitBreaker(self.s.retry.circuit_breaker_failures, self.s.retry.circuit_breaker_cooldown_s)

        ensure_dir(self.s.log_dir)
        ensure_dir(self.s.cache_dir)
        self.session_log_path = os.path.join(self.s.log_dir, "ui_session.log")

        self.db = ReceiptDB(self.s.db_path)
        self.price_table = PriceTable(os.path.join(self.s.cache_dir, "price_table.json"))
        self.price_table.load_cache()
        if not self.price_table.rows:
            self.price_table = PriceTable.builtin_fallback()

        self._relocate_legacy_logs_and_milestones()

        # model capabilities cache + probe worker
        self.caps_cache = ModelCapabilitiesCache(os.path.join(self.s.cache_dir, "model_capabilities.json"))
        self.caps_cache.load()
        self.probe_worker: Optional[ModelProbeWorker] = None
        self.all_models: List[str] = []
        self.skip_paths_current: List[str] = []
        self.skip_exts_default: List[str] = [".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".avi", ".mov"]
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(800)
        self._progress_timer.timeout.connect(self._pulse_progress)
        self._progress_last_ts = time.time()
        self.pricing_audit_timer: Optional[QTimer] = None

        self.txt_response_view: Optional[QPlainTextEdit] = None

        self.worker: Optional[RunWorker] = None
        self.run_logger: Optional[RunLogger] = None
        self.progress_dialog: Optional[ProgressDialog] = None
        self._bzz_default = False
        self._last_run_send_as_c = False

        root = QWidget()
        root.setStyleSheet(DARK_STYLESHEET)
        v = QVBoxLayout(root)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        self.tab_run = QWidget()
        self.tab_files = QWidget()
        self.tab_cascade = QWidget()
        self.tab_vector = QWidget()
        self.tab_settings = QWidget()
        self.tab_smtp = QWidget()
        self.tab_models = QWidget()
        self.tab_batch = QWidget()
        self.tab_git = QWidget()
        self.tab_pricing = QWidget()
        self.tab_reqresp = QWidget()
        self.tab_help = QWidget()
        self.tabs.addTab(self._scroll_tab(self.tab_run), "RUN")
        self.tabs.addTab(self._scroll_tab(self.tab_files), "FILES API")
        self.tabs.addTab(self._scroll_tab(self.tab_cascade), "KASKÁDA")
        self.tabs.addTab(self._scroll_tab(self.tab_vector), "VECTOR STORES")
        self.tabs.addTab(self._scroll_tab(self.tab_settings), "SETTINGS")
        self.tabs.addTab(self._scroll_tab(self.tab_smtp), "SMTP")
        self.tabs.addTab(self._scroll_tab(self.tab_models), "MODELS")
        self.tabs.addTab(self._scroll_tab(self.tab_batch), "BATCH")
        self.tabs.addTab(self._scroll_tab(self.tab_git), "GITHUB")
        self.tabs.addTab(self._scroll_tab(self.tab_pricing), "PRICING")
        self.tabs.addTab(self._scroll_tab(self.tab_reqresp), "REQUEST/RESPONSE")
        self.tabs.addTab(self._scroll_tab(self.tab_help), "HELP")

        self._build_run_tab()
        self._build_files_tab()
        self._build_cascade_tab()
        self._build_vector_tab()
        self._build_settings_tab()
        self._build_smtp_tab()
        self._build_model_tab()
        self._build_batch_tab()
        self._build_git_tab()
        self._build_pricing_tab()
        self._build_response_request_tab()
        self._build_help_tab()

        self.setCentralWidget(root)
        # propagate OUT dir to batch panel for downloads
        try:
            self.batch_panel.set_out_dir(self.ed_out.text())
            self.ed_out.textChanged.connect(lambda txt: self.batch_panel.set_out_dir(txt))
        except Exception:
            pass
        try:
            self.pricing_panel.set_api_key(self.api_key)
            self._auto_refresh_pricing()
        except Exception:
            pass

        self._maybe_resume_hint()
        self._refresh_models_best_effort()
        self.refresh_run_cascades()
        self._auto_probe_models_on_start()
        self._auto_refresh_pricing()
        self._start_pricing_audit_loop()

    def _scroll_tab(self, widget: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(widget)
        return scroll

    # ---------- build tabs ----------
    def _build_run_tab(self):
        w = self.tab_run
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        top = QGridLayout()
        row = 0

        top.addWidget(QLabel("Project"), row, 0)
        self.ed_project = QLineEdit()
        self.ed_project.setPlaceholderText("Název projektu (log + účtenky)")
        self.ed_project.setMaximumWidth(400)
        top.addWidget(self.ed_project, row, 1, 1, 2)
        top.addWidget(QLabel("Připojeno (Files/VS)"), row, 5)

        row += 1
        top.addWidget(QLabel("Mode"), row, 0)
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["GENERATE", "MODIFY", "QA", "QFILE", "KASKADA"])
        self.cb_mode.setMaximumWidth(160)
        self.cb_mode.currentTextChanged.connect(self.on_mode_changed)
        top.addWidget(self.cb_mode, row, 1)

        self.chk_send_as_c = QCheckBox("SEND AS BATCH")
        top.addWidget(self.chk_send_as_c, row, 2)

        top.addWidget(QLabel("Response ID"), row, 3)
        self.ed_response_id = QLineEdit()
        self.ed_response_id.setPlaceholderText("Volitelné: navázat na existující response")
        self.ed_response_id.setMaximumWidth(260)
        top.addWidget(self.ed_response_id, row, 4)

        self.row_cascade_selector = QWidget()
        row_c_l = QHBoxLayout(self.row_cascade_selector)
        row_c_l.setContentsMargins(0, 0, 0, 0)
        row_c_l.addWidget(QLabel("Vybraná kaskáda"))
        self.cb_run_cascade = QComboBox()
        self.cb_run_cascade.setMinimumWidth(280)
        self.btn_run_cascade_refresh = QPushButton("Refresh")
        row_c_l.addWidget(self.cb_run_cascade)
        row_c_l.addWidget(self.btn_run_cascade_refresh)
        row_c_l.addStretch(1)
        top.addWidget(self.row_cascade_selector, row, 0, 1, 3)
        self.row_cascade_selector.setVisible(False)
        self.btn_run_cascade_refresh.clicked.connect(self.refresh_run_cascades)
        self.txt_attached_summary = QPlainTextEdit()
        self.txt_attached_summary.setReadOnly(True)
        self.txt_attached_summary.setPlaceholderText("Files/VS připojené k RUN")
        top.addWidget(self.txt_attached_summary, row, 5, 3, 1)

        row += 1
        top.addWidget(QLabel("Model"), row, 0)
        self.cb_model = QComboBox()
        top.addWidget(self.cb_model, row, 1)

        self.btn_models = QPushButton("Refresh models")
        top.addWidget(self.btn_models, row, 2)
        if not hasattr(self, "_refresh_models_best_effort"):
            self._refresh_models_best_effort = lambda: None  # type: ignore
        self.btn_models.clicked.connect(self._refresh_models_best_effort)

        row += 1
        top.addWidget(QLabel("Model filter"), row, 0)
        self.ed_model_filter = QLineEdit()
        self.ed_model_filter.setPlaceholderText("Fulltext filter modelů")
        self.ed_model_filter.textChanged.connect(self._apply_model_filter)
        top.addWidget(self.ed_model_filter, row, 1, 1, 4)

        row += 1
        top.addWidget(QLabel("Temperature"), row, 0)
        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.1)
        self.sp_temp.setValue(float(getattr(self.s, "default_temperature", 0.2)))
        top.addWidget(self.sp_temp, row, 1)

        self.lbl_caps = QLabel("CAPS")
        self.lbl_caps.setTextFormat(Qt.RichText)
        top.addWidget(self.lbl_caps, row, 3, 1, 2)
        top.setColumnStretch(0, 0)
        top.setColumnStretch(1, 1)
        top.setColumnStretch(2, 0)
        top.setColumnStretch(3, 0)
        top.setColumnStretch(4, 1)
        top.setColumnStretch(5, 2)

        row += 1
        outer.addLayout(top)

        mid = QHBoxLayout()

        left = QVBoxLayout()
        lbl_prompt = QLabel("Zadání / Prompt (může být i >150 000 znaků; použije se ingest A0 + kaskáda přes previous_response_id)")
        lbl_prompt.setWordWrap(True)
        left.addWidget(lbl_prompt)
        self.txt_prompt = QPlainTextEdit()
        self.txt_prompt.setPlaceholderText("Sem vlož zadání / prompt.")
        self.txt_response_view = QPlainTextEdit()
        self.txt_response_view.setReadOnly(True)
        self.txt_response_view.setPlaceholderText("Textová odpověď (response) se zobrazí zde, pokud je dostupná.")

        prompt_split = QSplitter(Qt.Orientation.Horizontal)
        prompt_split.addWidget(self.txt_prompt)
        prompt_split.addWidget(self.txt_response_view)
        prompt_split.setStretchFactor(0, 2)
        prompt_split.setStretchFactor(1, 1)
        left.addWidget(prompt_split, 2)

        g_dirs = QGroupBox("IN/OUT")
        gd = QGridLayout(g_dirs)
        gd.addWidget(QLabel("IN"), 0, 0)
        self.ed_in = QLineEdit()
        gd.addWidget(self.ed_in, 0, 1)
        self.btn_in = QPushButton("Browse")
        gd.addWidget(self.btn_in, 0, 2)
        self.btn_in.clicked.connect(lambda: self._browse_dir(self.ed_in))

        gd.addWidget(QLabel("OUT"), 1, 0)
        self.ed_out = QLineEdit()
        gd.addWidget(self.ed_out, 1, 1)
        self.btn_out = QPushButton("Browse")
        gd.addWidget(self.btn_out, 1, 2)
        self.btn_out.clicked.connect(lambda: self._browse_dir(self.ed_out))

        self.chk_in_eq_out = QCheckBox("IN = OUT")
        self.chk_versing = QCheckBox("VERSING snapshot (before first write)")
        self.chk_in_eq_out.stateChanged.connect(self.on_in_eq_out_changed)
        self.chk_versing.setChecked(False)

        gd.addWidget(self.chk_in_eq_out, 2, 1)
        gd.addWidget(self.chk_versing, 2, 2)

        left.addWidget(g_dirs)

        runrow = QHBoxLayout()
        self.btn_go = QPushButton("KÁJO GO'")
        self.btn_stop = QPushButton("STOP")
        self.btn_new = QPushButton("NEW")
        self.btn_save_state = QPushButton("SAVE")
        self.btn_load_state = QPushButton("LOAD")
        self.btn_exit = QPushButton("EXIT")
        self.ed_rerun = QLineEdit()
        self.ed_rerun.setPlaceholderText("RUN_ID pro ReRun")
        self.ed_rerun.setMinimumWidth(200)
        self.btn_rerun = QPushButton("KÁJO ReRun")
        runrow.addWidget(self.btn_go)
        runrow.addWidget(self.btn_stop)
        runrow.addWidget(self.btn_new)
        runrow.addWidget(self.btn_save_state)
        runrow.addWidget(self.btn_load_state)
        runrow.addWidget(self.btn_exit)
        runrow.addWidget(self.ed_rerun)
        runrow.addWidget(self.btn_rerun)
        runrow.addStretch(1)
        left.addLayout(runrow)

        self.btn_go.clicked.connect(self.on_go)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_new.clicked.connect(self.on_new)
        self.btn_save_state.clicked.connect(self.on_save_state)
        self.btn_load_state.clicked.connect(self.on_load_state)
        self.btn_exit.clicked.connect(self.on_exit)
        self.btn_rerun.clicked.connect(self.on_rerun)
        # pricing and batch tabs are available via their own tabs; toolbar buttons removed per request

        self.pb = QProgressBar()
        self.pb_sub = QProgressBar()
        style_progress_bar(self.pb)
        style_progress_bar(self.pb_sub)
        left.addWidget(self.pb)
        left.addWidget(self.pb_sub)

        mid.addLayout(left, 2)

        right = QVBoxLayout()

        g_diag = QGroupBox("Diagnostics")
        dg = QGridLayout(g_diag)

        self.chk_diag_win_in = QCheckBox("Windows IN (collect)")
        self.chk_diag_win_out = QCheckBox("Windows OUT (execute repair script if present)")
        self.chk_diag_ssh_in = QCheckBox("SSH IN (collect)")
        self.chk_diag_ssh_out = QCheckBox("SSH OUT (execute repair script if present)")
        self.chk_ssh_pin_required = QCheckBox("SSH pin required (KAJOVO_SSH_HOSTKEY_SHA256)")

        dg.addWidget(self.chk_diag_win_in, 0, 0, 1, 2)
        dg.addWidget(self.chk_diag_win_out, 1, 0, 1, 2)
        dg.addWidget(self.chk_diag_ssh_in, 2, 0, 1, 2)
        dg.addWidget(self.chk_diag_ssh_out, 3, 0, 1, 2)
        dg.addWidget(self.chk_ssh_pin_required, 4, 0, 1, 3)

        dg.addWidget(QLabel("SSH user"), 5, 0)
        self.ed_ssh_user = QLineEdit()
        self.ed_ssh_user.setPlaceholderText("root")
        dg.addWidget(self.ed_ssh_user, 5, 1)

        dg.addWidget(QLabel("SSH host"), 6, 0)
        self.ed_ssh_host = QLineEdit()
        self.ed_ssh_host.setPlaceholderText("10.0.0.1")
        dg.addWidget(self.ed_ssh_host, 6, 1)

        dg.addWidget(QLabel("SSH key"), 7, 0)
        self.ed_ssh_key = QLineEdit()
        dg.addWidget(self.ed_ssh_key, 7, 1)
        self.btn_ssh_key = QPushButton("Browse")
        dg.addWidget(self.btn_ssh_key, 7, 2)
        self.btn_ssh_key.clicked.connect(lambda: self._browse_file(self.ed_ssh_key))

        dg.addWidget(QLabel("SSH key password"), 8, 0)
        self.ed_ssh_pwd = QLineEdit()
        self.ed_ssh_pwd.setEchoMode(QLineEdit.Password)
        dg.addWidget(self.ed_ssh_pwd, 8, 1)
        self.btn_ssh_save = QPushButton("Save SSH")
        dg.addWidget(self.btn_ssh_save, 8, 2)
        self.btn_ssh_save.clicked.connect(self._save_ssh_settings)

        right.addWidget(g_diag)

        right.addWidget(QLabel("Log"))
        splitter_log = QSplitter(Qt.Orientation.Vertical)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        splitter_log.addWidget(self.txt_log)
        self.tbl_log = QTableWidget(0, 4)
        self.tbl_log.setHorizontalHeaderLabels(["Time", "Stage", "Action", "Details"])
        self.tbl_log.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_log.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_log.setSelectionMode(QTableWidget.NoSelection)
        header = self.tbl_log.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.resizeSection(0, 110)
        header.resizeSection(1, 120)
        header.resizeSection(2, 140)
        splitter_log.addWidget(self.tbl_log)
        splitter_log.setStretchFactor(0, 2)
        splitter_log.setStretchFactor(1, 1)
        right.addWidget(splitter_log, 1)

        mid.addLayout(right, 1)
        outer.addLayout(mid, 1)

        # reactions
        self.cb_model.currentTextChanged.connect(self.on_model_changed)
        self._apply_saved_ssh()

    def _relocate_legacy_logs_and_milestones(self):
        """Move stray RUN_* directories and milestone-*.zip from repo root to LOG/milestones."""
        try:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            log_dir = self.s.log_dir
            ensure_dir(log_dir)
            # move RUN_* from base_dir
            for path in glob.glob(os.path.join(base_dir, "RUN_*")):
                name = os.path.basename(path)
                dest = os.path.join(log_dir, name)
                if os.path.exists(dest):
                    # pokud je zdroj prázdný duplikát, prostě smaž
                    try:
                        if not any(os.scandir(path)):
                            shutil.rmtree(path, ignore_errors=True)
                            self.log(f"Smazán prázdný duplicitní {name} v rootu.")
                    except Exception:
                        pass
                    continue
                try:
                    shutil.move(path, dest)
                    self.log(f"Moved legacy {name} to LOG/")
                except Exception:
                    try:
                        # fallback: když move selže a je to prázdné, smaž duplicitní složku
                        if not any(os.scandir(path)):
                            shutil.rmtree(path, ignore_errors=True)
                            self.log(f"Smazán prázdný duplicitní {name} po selhání přesunu.")
                    except Exception:
                        pass
            # move milestone zips
            ms_dir = os.path.join(log_dir, "milestones")
            ensure_dir(ms_dir)
            for path in glob.glob(os.path.join(base_dir, "milestone-*.zip")):
                name = os.path.basename(path)
                dest = os.path.join(ms_dir, name)
                if os.path.exists(dest):
                    continue
                try:
                    shutil.move(path, dest)
                    self.log(f"Moved milestone {name} to LOG/milestones")
                except Exception:
                    pass
        except Exception:
            pass

    def _build_files_tab(self):
        v = QVBoxLayout(self.tab_files)
        v.setContentsMargins(10, 10, 10, 10)
        self.files_panel = FilesPanel(self.s, self.api_key)
        self.files_panel.attached_changed.connect(self.on_attached_changed)
        self.files_panel.logline.connect(self.log)
        v.addWidget(self.files_panel, 1)

    def _build_vector_tab(self):
        v = QVBoxLayout(self.tab_vector)
        v.setContentsMargins(10, 10, 10, 10)
        self.vector_panel = VectorStoresPanel(self.s, self.api_key)
        self.vector_panel.logline.connect(self.log)
        self.vector_panel.attached_changed.connect(self.on_vs_attached_changed)
        v.addWidget(self.vector_panel, 1)

    def _build_cascade_tab(self):
        v = QVBoxLayout(self.tab_cascade)
        v.setContentsMargins(10, 10, 10, 10)
        self.cascade_panel = CascadePanel(self.s, self._current_model_list)
        v.addWidget(self.cascade_panel, 1)

    def _current_model_list(self) -> List[str]:
        if self.all_models:
            return list(self.all_models)
        return [self.cb_model.itemText(i) for i in range(self.cb_model.count())]

    def refresh_run_cascades(self):
        try:
            self.cascade_panel.refresh_saved_list()
        except Exception:
            pass
        self.cb_run_cascade.clear()
        paths = []
        if hasattr(self, "cascade_panel"):
            base = self.cascade_panel.cascade_dir
            for n in self.cascade_panel.available_cascades():
                paths.append((n, os.path.join(base, n)))
        for label, full in paths:
            self.cb_run_cascade.addItem(label, full)

    def _build_model_tab(self):
        v = QVBoxLayout(self.tab_models)
        v.setContentsMargins(10, 10, 10, 10)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search models"))
        self.ed_model_search_tab = QLineEdit()
        self.ed_model_search_tab.setPlaceholderText("Filter by model id")
        search_row.addWidget(self.ed_model_search_tab, 1)
        v.addLayout(search_row)

        actions_row = QHBoxLayout()
        self.btn_probe = QPushButton("Probe models")
        actions_row.addStretch(1)
        actions_row.addWidget(self.btn_probe)
        v.addLayout(actions_row)

        feats = QHBoxLayout()
        self.chk_model_prev = QCheckBox("needs cascade (previous_response_id)")
        self.chk_model_temp = QCheckBox("needs temperature")
        self.chk_model_fs = QCheckBox("needs file_search")
        self.chk_model_vs = QCheckBox("needs vector store")
        self.chk_model_include_untested = QCheckBox("include untested")
        self.chk_model_include_untested.setChecked(False)
        feats.addWidget(self.chk_model_prev)
        feats.addWidget(self.chk_model_temp)
        feats.addWidget(self.chk_model_fs)
        feats.addWidget(self.chk_model_vs)
        feats.addStretch(1)
        feats.addWidget(self.chk_model_include_untested)
        v.addLayout(feats)

        self.lst_models = QListWidget()
        v.addWidget(self.lst_models, 1)

        info_row = QHBoxLayout()
        self.lbl_model_info = QLabel("No model selected")
        self.lbl_default_model = QLabel("")
        info_row.addWidget(self.lbl_model_info)
        info_row.addStretch(1)
        info_row.addWidget(self.lbl_default_model)
        self.btn_set_default_model = QPushButton("Nastavit jako výchozí")
        self.btn_apply_model = QPushButton("Use selected in RUN")
        info_row.addWidget(self.btn_set_default_model)
        info_row.addWidget(self.btn_apply_model)
        v.addLayout(info_row)

        self.ed_model_search_tab.textChanged.connect(self._refresh_model_tab)
        self.chk_model_prev.stateChanged.connect(self._refresh_model_tab)
        self.chk_model_temp.stateChanged.connect(self._refresh_model_tab)
        self.chk_model_fs.stateChanged.connect(self._refresh_model_tab)
        self.chk_model_vs.stateChanged.connect(self._refresh_model_tab)
        self.chk_model_include_untested.stateChanged.connect(self._refresh_model_tab)

        self.lst_models.itemSelectionChanged.connect(self._update_model_info)
        self.lst_models.itemDoubleClicked.connect(lambda _: self._apply_selected_model())
        self.btn_apply_model.clicked.connect(self._apply_selected_model)
        self.btn_set_default_model.clicked.connect(self._set_default_model)
        self.btn_probe.clicked.connect(self.on_probe_models)

        self._refresh_model_tab()

    def _build_settings_tab(self):
        v = QVBoxLayout(self.tab_settings)
        v.setContentsMargins(10, 10, 10, 10)

        api_box = QGroupBox("API-KEY management")
        api_layout = QHBoxLayout(api_box)
        self.ed_settings_apikey = QLineEdit()
        self.ed_settings_apikey.setEchoMode(QLineEdit.Password)
        self.ed_settings_apikey.setPlaceholderText("sk-...")
        current_key = os.environ.get("OPENAI_API_KEY", "")
        if current_key:
            self.ed_settings_apikey.setText(current_key)
        self.btn_settings_show = QPushButton("Zobraz")
        self.btn_settings_save = QPushButton("Ulož")
        self.btn_settings_delete = QPushButton("Smaž")
        api_layout.addWidget(self.ed_settings_apikey, 1)
        api_layout.addWidget(self.btn_settings_show)
        api_layout.addWidget(self.btn_settings_save)
        api_layout.addWidget(self.btn_settings_delete)
        v.addWidget(api_box)

        self.btn_settings_show.clicked.connect(self._api_show)
        self.btn_settings_save.clicked.connect(self._api_save)
        self.btn_settings_delete.clicked.connect(self._api_delete)

        adv_group = QGroupBox("Rozšířená nastavení")
        adv_layout = QVBoxLayout(adv_group)
        form = QGridLayout()
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(12)
        self.chk_mask = QCheckBox("Mask secrets in logs")
        self.chk_encrypt = QCheckBox("Encrypt logs (basic)")
        self.chk_allow_sensitive = QCheckBox("Allow upload of sensitive files")
        self.txt_deny_ext = QPlainTextEdit()
        self.txt_deny_ext.setPlaceholderText("One extension per line (e.g. .exe)")
        self.txt_deny_ext.setFixedHeight(80)
        self.txt_deny_glob = QPlainTextEdit()
        self.txt_deny_glob.setPlaceholderText("One glob mask per line (e.g. **/.git/**)")
        self.txt_deny_glob.setFixedHeight(80)
        self.sp_batch_poll = QDoubleSpinBox()
        self.sp_batch_poll.setRange(0.5, 60.0)
        self.sp_batch_poll.setSingleStep(0.5)
        self.sp_batch_timeout = QDoubleSpinBox()
        self.sp_batch_timeout.setRange(60, 24 * 60 * 60)
        self.sp_batch_timeout.setSingleStep(10)
        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.1)
        self.ed_price_url = QLineEdit()
        self.chk_price_refresh = QCheckBox("Auto refresh pricing on start")
        form.addWidget(self.chk_mask, 0, 0, 1, 2)
        form.addWidget(self.chk_encrypt, 1, 0, 1, 2)
        form.addWidget(self.chk_allow_sensitive, 2, 0, 1, 2)
        form.addWidget(QLabel("Deny extensions (IN mirror)"), 3, 0)
        form.addWidget(self.txt_deny_ext, 3, 1)
        form.addWidget(QLabel("Deny globs (IN mirror)"), 4, 0)
        form.addWidget(self.txt_deny_glob, 4, 1)
        form.addWidget(QLabel("Batch poll interval (s)"), 5, 0)
        form.addWidget(self.sp_batch_poll, 5, 1)
        form.addWidget(QLabel("Batch timeout (s)"), 6, 0)
        form.addWidget(self.sp_batch_timeout, 6, 1)
        form.addWidget(QLabel("Default temperature"), 7, 0)
        form.addWidget(self.sp_temp, 7, 1)
        form.addWidget(QLabel("Pricing source URL"), 8, 0)
        form.addWidget(self.ed_price_url, 8, 1)
        form.addWidget(self.chk_price_refresh, 9, 0, 1, 2)
        adv_layout.addLayout(form)
        self.lbl_settings_status = QLabel("")
        self.lbl_settings_status.setStyleSheet("color: #7aa7c7;")
        adv_layout.addWidget(self.lbl_settings_status)
        self.btn_save_settings = QPushButton("Uložit nastavení")
        adv_layout.addWidget(self.btn_save_settings, alignment=Qt.AlignmentFlag.AlignRight)
        v.addWidget(adv_group)

        self.btn_save_settings.clicked.connect(self.on_settings_save)

        self._load_settings_tab()

    def _build_smtp_tab(self):
        v = QVBoxLayout(self.tab_smtp)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(10)

        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.ed_smtp_host = QLineEdit()
        self.ed_smtp_host.setPlaceholderText("smtp.server.tld")
        self.sp_smtp_port = QSpinBox()
        self.sp_smtp_port.setRange(1, 65535)
        self.sp_smtp_port.setValue(587)
        self.ed_smtp_user = QLineEdit()
        self.ed_smtp_pwd = QLineEdit()
        self.ed_smtp_pwd.setEchoMode(QLineEdit.Password)
        self.chk_smtp_tls = QCheckBox("STARTTLS")
        self.chk_smtp_ssl = QCheckBox("SSL")
        self.ed_smtp_from = QLineEdit()
        self.ed_smtp_from.setPlaceholderText("From e-mail (optional)")
        self.ed_smtp_to = QLineEdit()
        self.ed_smtp_to.setPlaceholderText("Kam poslat upozornění (To)")

        form.addWidget(QLabel("SMTP server"), 0, 0)
        form.addWidget(self.ed_smtp_host, 0, 1)
        form.addWidget(QLabel("Port"), 0, 2)
        form.addWidget(self.sp_smtp_port, 0, 3)
        form.addWidget(QLabel("Uživatel"), 1, 0)
        form.addWidget(self.ed_smtp_user, 1, 1, 1, 3)
        form.addWidget(QLabel("Heslo"), 2, 0)
        form.addWidget(self.ed_smtp_pwd, 2, 1, 1, 3)
        form.addWidget(self.chk_smtp_tls, 3, 1)
        form.addWidget(self.chk_smtp_ssl, 3, 2)
        form.addWidget(QLabel("From"), 4, 0)
        form.addWidget(self.ed_smtp_from, 4, 1, 1, 3)
        form.addWidget(QLabel("Upozornění na e-mail"), 5, 0)
        form.addWidget(self.ed_smtp_to, 5, 1, 1, 3)

        v.addLayout(form)
        self.lbl_smtp_status = QLabel("")
        self.lbl_smtp_status.setStyleSheet("color: #7aa7c7;")
        v.addWidget(self.lbl_smtp_status)
        self.btn_save_smtp = QPushButton("Uložit SMTP")
        self.btn_test_smtp = QPushButton("Test SMTP")
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_test_smtp)
        btn_row.addWidget(self.btn_save_smtp)
        v.addLayout(btn_row)

        self.chk_smtp_ssl.toggled.connect(
            lambda checked: self.chk_smtp_tls.setChecked(False) if checked else None
        )
        self.btn_save_smtp.clicked.connect(self.on_smtp_save)
        self.btn_test_smtp.clicked.connect(self.on_smtp_test)
        self._load_smtp_tab()

    def _load_smtp_tab(self):
        smtp = getattr(self.s, "smtp", None)
        if not smtp:
            return
        self.ed_smtp_host.setText(getattr(smtp, "host", ""))
        try:
            self.sp_smtp_port.setValue(int(getattr(smtp, "port", 587)))
        except Exception:
            self.sp_smtp_port.setValue(587)
        self.ed_smtp_user.setText(getattr(smtp, "username", ""))
        self.ed_smtp_pwd.setText(get_secret("smtp_password") or "")
        self.chk_smtp_tls.setChecked(bool(getattr(smtp, "use_tls", True)))
        self.chk_smtp_ssl.setChecked(bool(getattr(smtp, "use_ssl", False)))
        self.ed_smtp_from.setText(getattr(smtp, "from_email", ""))
        self.ed_smtp_to.setText(getattr(smtp, "to_email", ""))
        self.lbl_smtp_status.setText("SMTP nastavení načteno.")

    def _collect_smtp_inputs(self) -> SMTPSettings:
        smtp = SMTPSettings()
        smtp.host = self.ed_smtp_host.text().strip()
        smtp.port = int(self.sp_smtp_port.value())
        smtp.username = self.ed_smtp_user.text().strip()
        smtp.password = self.ed_smtp_pwd.text()
        smtp.use_ssl = bool(self.chk_smtp_ssl.isChecked())
        smtp.use_tls = bool(self.chk_smtp_tls.isChecked() and not smtp.use_ssl)
        smtp.from_email = self.ed_smtp_from.text().strip()
        smtp.to_email = self.ed_smtp_to.text().strip()
        return smtp

    def on_smtp_save(self):
        smtp = getattr(self.s, "smtp", None)
        if not smtp:
            return
        updated = self._collect_smtp_inputs()
        smtp.host = updated.host
        smtp.port = updated.port
        smtp.username = updated.username
        smtp.password = updated.password
        smtp.use_ssl = updated.use_ssl
        smtp.use_tls = updated.use_tls
        smtp.from_email = updated.from_email
        smtp.to_email = updated.to_email
        try:
            save_settings(self.s, DEFAULT_SETTINGS_FILE)
            self.lbl_smtp_status.setText("SMTP uloženo.")
        except Exception as e:
            msg_critical(self, "SMTP", f"Chyba při ukládání SMTP: {e}")
            self.lbl_smtp_status.setText("SMTP chyba při ukládání.")
        self._load_smtp_tab()

    def on_smtp_test(self):
        smtp = self._collect_smtp_inputs()
        if not smtp.host or not smtp.to_email:
            msg_warning(self, "SMTP", "Vyplň hostitele SMTP a cílový e-mail (To) pro test.")
            return
        self.lbl_smtp_status.setText("Testuji SMTP…")
        subject = f"KAJOVO SMTP TEST {time.strftime('%Y-%m-%d %H:%M:%S')}"
        body = "\n".join(
            [
                "Toto je testovací e-mail z KAJOVO SMTP karty.",
                f"Server: {smtp.host}:{smtp.port}",
                f"TLS: {int(smtp.use_tls)} | SSL: {int(smtp.use_ssl)}",
                f"Uživatel: {smtp.username or '(nezadán)'}",
            ]
        )
        with BusyPopup(self, "Odesílám SMTP test..."):
            ok, msg = send_smtp_notification(smtp, subject, body)
        if ok:
            self.lbl_smtp_status.setText("SMTP test úspěšný — e-mail odeslán.")
            self.log("SMTP test odeslán.")
        else:
            self.lbl_smtp_status.setText(f"SMTP test selhal: {msg}")
            self.log(f"SMTP test selhal: {msg}")
            msg_warning(self, "SMTP", f"Test se nepodařil: {msg}")

    def _load_settings_tab(self):
        self.chk_mask.setChecked(bool(self.s.logging.mask_secrets))
        self.chk_encrypt.setChecked(bool(self.s.logging.encrypt_logs))
        self.chk_allow_sensitive.setChecked(bool(self.s.security.allow_upload_sensitive))
        self.txt_deny_ext.setPlainText("\n".join(self.s.security.deny_extensions_in or []))
        self.txt_deny_glob.setPlainText("\n".join(self.s.security.deny_globs_in or []))
        self.sp_batch_poll.setValue(float(getattr(self.s, "batch_poll_interval_s", 4.0)))
        self.sp_batch_timeout.setValue(float(getattr(self.s, "batch_timeout_s", 3600.0)))
        self.sp_temp.setValue(float(getattr(self.s, "default_temperature", 0.2)))
        self.ed_price_url.setText(getattr(self.s.pricing, "source_url", ""))
        self.chk_price_refresh.setChecked(bool(getattr(self.s.pricing, "auto_refresh_on_start", True)))
        self.lbl_settings_status.setText("Loaded settings")

    def on_settings_save(self):
        self.s.logging.mask_secrets = bool(self.chk_mask.isChecked())
        self.s.logging.encrypt_logs = bool(self.chk_encrypt.isChecked())
        self.s.security.allow_upload_sensitive = bool(self.chk_allow_sensitive.isChecked())
        deny_ext = [ln.strip() for ln in self.txt_deny_ext.toPlainText().splitlines() if ln.strip()]
        deny_glob = [ln.strip() for ln in self.txt_deny_glob.toPlainText().splitlines() if ln.strip()]
        self.s.security.deny_extensions_in = deny_ext
        self.s.security.deny_globs_in = deny_glob
        self.s.batch_poll_interval_s = float(self.sp_batch_poll.value())
        self.s.batch_timeout_s = float(self.sp_batch_timeout.value())
        self.s.default_temperature = float(self.sp_temp.value())
        self.s.pricing.source_url = self.ed_price_url.text().strip()
        self.s.pricing.auto_refresh_on_start = bool(self.chk_price_refresh.isChecked())
        try:
            save_settings(self.s, DEFAULT_SETTINGS_FILE)
            self.lbl_settings_status.setText(f"Settings saved at {time.strftime('%H:%M:%S')}")
        except Exception as e:
            msg_critical(self, "SETTINGS", f"Chyba při ukládání: {e}")
        self._load_settings_tab()

    def _build_git_tab(self):
        v = QVBoxLayout(self.tab_git)
        v.setContentsMargins(10, 10, 10, 10)
        self.git_panel = GitHubPanel(self.s)
        self.git_panel.logline.connect(self.log)
        v.addWidget(self.git_panel, 1)

    def _build_batch_tab(self):
        v = QVBoxLayout(self.tab_batch)
        v.setContentsMargins(10, 10, 10, 10)
        self.batch_panel = BatchPanel(self.s, self.api_key)
        self.batch_panel.logline.connect(self.log)
        v.addWidget(self.batch_panel, 1)

    def _build_pricing_tab(self):
        v = QVBoxLayout(self.tab_pricing)
        v.setContentsMargins(10, 10, 10, 10)
        self.pricing_panel = PricingPanel(self.s, self.price_table, self.db)
        self.pricing_panel.logline.connect(self.log)
        v.addWidget(self.pricing_panel, 1)

    def _build_response_request_tab(self):
        v = QVBoxLayout(self.tab_reqresp)
        v.setContentsMargins(10, 10, 10, 10)
        self.response_request_panel = ResponseRequestPanel(self.s.log_dir)
        v.addWidget(self.response_request_panel, 1)

    def _build_help_tab(self):
        v = QVBoxLayout(self.tab_help)
        v.setContentsMargins(10, 10, 10, 10)
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(
            "v2 funkce:\n"
            "- Načte dostupné modely (po API-KEY) a probuje kompatibility (previous_response_id, temperature, tools/file_search).\n"
            "- Cache: cache/model_capabilities.json (TTL 7 dní; Probe models = force).\n"
            "- Find model: vyhledá model podle požadovaných funkcí.\n"
            "- Long prompt >150k: ingest A0 + navazující A1/A2/A3 přes previous_response_id.\n\n"
            "Pozn.: Response ID dostává každý úspěšný request na Responses API.\n"
            "Probe pro previous_response_id nyní značí 'unsupported' jen když server explicitně odmítne parametr.\n"
        )
        v.addWidget(txt, 1)

    # ---------- helpers ----------
    def _ts(self) -> str:
        return time.strftime("%Y%m%d %H%M%S")

    def _mark_progress_activity(self):
        self._progress_last_ts = time.time()

    def _pulse_progress(self):
        # If worker is running and no update recently, pulse subprogress to show liveness.
        if self.worker and self.worker.isRunning():
            if time.time() - self._progress_last_ts > 1.0:
                val = (self.pb_sub.value() + 5) % 100
                self.pb_sub.setValue(val)

    def _send_bzz_notification(self, rid: str):
        smtp = getattr(self.s, "smtp", None)
        if not smtp:
            self.log("BZZonEND: SMTP nastavení není k dispozici.")
            return
        end_ts = time.strftime("%Y-%m-%d %H:%M:%S")
        project = self.run_logger.project_name if self.run_logger else ""
        run_label = rid or "RUN"
        if project:
            run_label = f"{project} ({run_label})"
        subject = f"KAJOVO HLÁSÍ HOTOVO — {run_label} {end_ts}"
        body_lines = [
            f"RUN: {rid or 'neznámý'}",
            f"Projekt: {project or 'NO_PROJECT'}",
            f"Dokončeno: {end_ts}",
            f"OUT: {self.ed_out.text().strip() or '(nenastaveno)'}",
        ]
        ok, msg = send_smtp_notification(smtp, subject, "\n".join(body_lines))
        if ok:
            self.log("BZZonEND: e-mail odeslán.")
        else:
            self.log(f"BZZonEND: e-mail se nepodařilo odeslat ({msg}).")

    def log(self, msg: str):
        prefix = f"{self._ts()} | "
        if isinstance(msg, str) and len(msg) >= 15 and msg[:8].isdigit() and msg[9:15].isdigit():
            line = msg
        else:
            line = prefix + str(msg)
        self.txt_log.appendPlainText(line)
        self._add_log_row(line)
        try:
            ensure_dir(os.path.dirname(os.path.abspath(self.session_log_path)) or ".")
            with open(self.session_log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def _add_log_row(self, line: str):
        if not hasattr(self, "tbl_log"):
            return
        if not line:
            return
        if self.tbl_log.rowCount() >= self.LOG_TABLE_MAX_ROWS:
            self.tbl_log.removeRow(0)
        parts = [part.strip() for part in line.split("|")]
        ts = parts[0] if parts else ""
        stage_action = parts[1] if len(parts) > 1 else ""
        details = " | ".join(parts[2:]) if len(parts) > 2 else ""
        stage = ""
        action = ""
        if stage_action:
            if ":" in stage_action:
                stage_part, action_part = stage_action.split(":", 1)
                stage = stage_part.strip()
                action = action_part.strip()
            else:
                stage = stage_action
        row = self.tbl_log.rowCount()
        self.tbl_log.insertRow(row)
        self.tbl_log.setItem(row, 0, QTableWidgetItem(ts))
        self.tbl_log.setItem(row, 1, QTableWidgetItem(stage))
        self.tbl_log.setItem(row, 2, QTableWidgetItem(action))
        self.tbl_log.setItem(row, 3, QTableWidgetItem(details))
        self.tbl_log.scrollToBottom()

    def _gather_state(self) -> dict:
        return {
            "project": self.ed_project.text(),
            "mode": self.cb_mode.currentText(),
            "send_as_c": bool(self.chk_send_as_c.isChecked()),
            "model": self.cb_model.currentText(),
            "model_filter": self.ed_model_filter.text(),
            "response_id": self.ed_response_id.text(),
            "prompt": self.txt_prompt.toPlainText(),
            "in_dir": self.ed_in.text(),
            "out_dir": self.ed_out.text(),
            "in_equals_out": bool(self.chk_in_eq_out.isChecked()),
            "versing": bool(self.chk_versing.isChecked()),
            "temperature": float(self.sp_temp.value()),
            "diag": {
                "win_in": bool(self.chk_diag_win_in.isChecked()),
                "win_out": bool(self.chk_diag_win_out.isChecked()),
                "ssh_in": bool(self.chk_diag_ssh_in.isChecked()),
                "ssh_out": bool(self.chk_diag_ssh_out.isChecked()),
            },
            "ssh": {
                "user": self.ed_ssh_user.text(),
                "host": self.ed_ssh_host.text(),
                "key": self.ed_ssh_key.text(),
                "password": "",
                "pin_required": bool(self.chk_ssh_pin_required.isChecked()),
            },
            "attached_file_ids": self.files_panel.attached_ids(),
            "attached_vector_store_ids": self.vector_panel.attached_ids() if hasattr(self, "vector_panel") else [],
            "tab_index": self.tabs.currentIndex(),
            "log_text": self.txt_log.toPlainText(),
            "git": self.git_panel.get_state() if hasattr(self, "git_panel") and hasattr(self.git_panel, "get_state") else {},
            "settings": {
                "mask": bool(self.chk_mask.isChecked()),
                "encrypt": bool(self.chk_encrypt.isChecked()),
                "allow_sensitive": bool(self.chk_allow_sensitive.isChecked()),
                "deny_ext": self.txt_deny_ext.toPlainText(),
                "deny_glob": self.txt_deny_glob.toPlainText(),
                "batch_poll": float(self.sp_batch_poll.value()),
                "batch_timeout": float(self.sp_batch_timeout.value()),
                "temperature": float(self.sp_temp.value()),
                "price_url": self.ed_price_url.text(),
                "auto_price": bool(self.chk_price_refresh.isChecked()),
                "smtp": {
                    "host": self.ed_smtp_host.text(),
                    "port": int(self.sp_smtp_port.value()),
                    "user": self.ed_smtp_user.text(),
                    "password": "",
                    "tls": bool(self.chk_smtp_tls.isChecked()),
                    "ssl": bool(self.chk_smtp_ssl.isChecked()),
                    "from": self.ed_smtp_from.text(),
                    "to": self.ed_smtp_to.text(),
                },
            },
        }

    def _apply_state(self, state: dict):
        if not state:
            return
        self.ed_project.setText(state.get("project", ""))
        mode = state.get("mode", "")
        mi = self.cb_mode.findText(mode) if mode else -1
        if mi >= 0:
            self.cb_mode.setCurrentIndex(mi)
        self.chk_send_as_c.setChecked(bool(state.get("send_as_c", False)))
        # model filter + list
        mf = state.get("model_filter", "")
        self.ed_model_filter.blockSignals(True)
        self.ed_model_filter.setText(mf)
        self.ed_model_filter.blockSignals(False)
        # reapply filter with preserved model selection
        self._apply_model_filter(preserve=state.get("model", None))
        model = state.get("model", "")
        if model:
            idx = self.cb_model.findText(model)
            if idx >= 0:
                self.cb_model.setCurrentIndex(idx)
        self.ed_response_id.setText(state.get("response_id", ""))
        self.txt_prompt.setPlainText(state.get("prompt", ""))
        self.ed_in.setText(state.get("in_dir", ""))
        self.ed_out.setText(state.get("out_dir", ""))
        self.chk_in_eq_out.setChecked(bool(state.get("in_equals_out", False)))
        self.chk_versing.setChecked(bool(state.get("versing", True)))
        self.on_in_eq_out_changed()
        self.sp_temp.setValue(float(state.get("temperature", getattr(self.s, "default_temperature", 0.2))))
        diag = state.get("diag", {}) or {}
        self.chk_diag_win_in.setChecked(bool(diag.get("win_in", False)))
        self.chk_diag_win_out.setChecked(bool(diag.get("win_out", False)))
        self.chk_diag_ssh_in.setChecked(bool(diag.get("ssh_in", False)))
        self.chk_diag_ssh_out.setChecked(bool(diag.get("ssh_out", False)))
        ssh = state.get("ssh", {}) or {}
        self.ed_ssh_user.setText(ssh.get("user", ""))
        self.ed_ssh_host.setText(ssh.get("host", ""))
        self.ed_ssh_key.setText(ssh.get("key", ""))
        self.chk_ssh_pin_required.setChecked(bool(ssh.get("pin_required", False)))
        self.ed_ssh_pwd.setText(get_secret("ssh_password") or "")
        settings = state.get("settings", {}) or {}
        self.chk_mask.setChecked(bool(settings.get("mask", self.chk_mask.isChecked())))
        self.chk_encrypt.setChecked(bool(settings.get("encrypt", self.chk_encrypt.isChecked())))
        self.chk_allow_sensitive.setChecked(
            bool(settings.get("allow_sensitive", self.chk_allow_sensitive.isChecked()))
        )
        self.txt_deny_ext.setPlainText(settings.get("deny_ext", self.txt_deny_ext.toPlainText()))
        self.txt_deny_glob.setPlainText(settings.get("deny_glob", self.txt_deny_glob.toPlainText()))
        self.sp_batch_poll.setValue(float(settings.get("batch_poll", self.sp_batch_poll.value())))
        self.sp_batch_timeout.setValue(float(settings.get("batch_timeout", self.sp_batch_timeout.value())))
        self.sp_temp.setValue(float(settings.get("temperature", self.sp_temp.value())))
        self.ed_price_url.setText(settings.get("price_url", self.ed_price_url.text()))
        self.chk_price_refresh.setChecked(bool(settings.get("auto_price", self.chk_price_refresh.isChecked())))
        smtp_state = settings.get("smtp", {}) or {}
        self.ed_smtp_host.setText(smtp_state.get("host", self.ed_smtp_host.text()))
        try:
            self.sp_smtp_port.setValue(int(smtp_state.get("port", self.sp_smtp_port.value())))
        except Exception:
            pass
        self.ed_smtp_user.setText(smtp_state.get("user", self.ed_smtp_user.text()))
        self.ed_smtp_pwd.setText(get_secret("smtp_password") or self.ed_smtp_pwd.text())
        self.chk_smtp_tls.setChecked(bool(smtp_state.get("tls", self.chk_smtp_tls.isChecked())))
        self.chk_smtp_ssl.setChecked(bool(smtp_state.get("ssl", self.chk_smtp_ssl.isChecked())))
        self.ed_smtp_from.setText(smtp_state.get("from", self.ed_smtp_from.text()))
        self.ed_smtp_to.setText(smtp_state.get("to", self.ed_smtp_to.text()))
        try:
            self.files_panel.set_attached(state.get("attached_file_ids", []))
        except Exception:
            pass
        try:
            self._update_attached_summary()
        except Exception:
            pass
        try:
            if hasattr(self, "vector_panel"):
                self.vector_panel.set_attached(state.get("attached_vector_store_ids", []))
        except Exception:
            pass
        log_text = state.get("log_text", "")
        if isinstance(log_text, str):
            self.txt_log.setPlainText(log_text)
        tab_index = int(state.get("tab_index", 0) or 0)
        if 0 <= tab_index < self.tabs.count():
            self.tabs.setCurrentIndex(tab_index)
        git_state = state.get("git") or {}
        if git_state and hasattr(self, "git_panel") and hasattr(self.git_panel, "apply_state"):
            try:
                self.git_panel.apply_state(git_state)
            except Exception:
                pass

    def _reset_ui_state(self):
        self.ed_project.clear()
        self.cb_mode.setCurrentIndex(0)
        self.chk_send_as_c.setChecked(False)
        if self.cb_model.count() > 0:
            dm = getattr(self.s, "default_model", "")
            idx = self.cb_model.findText(dm) if dm else -1
            if idx >= 0:
                self.cb_model.setCurrentIndex(idx)
            else:
                self.cb_model.setCurrentIndex(0)
        self.ed_model_filter.clear()
        self.ed_response_id.clear()
        self.txt_prompt.clear()
        self.txt_response_view.clear()
        self.ed_in.clear()
        self.ed_out.clear()
        self.chk_in_eq_out.setChecked(False)
        self.chk_versing.setChecked(False)
        self.sp_temp.setValue(float(getattr(self.s, "default_temperature", 0.2)))
        self.chk_diag_win_in.setChecked(False)
        self.chk_diag_win_out.setChecked(False)
        self.chk_diag_ssh_in.setChecked(False)
        self.chk_diag_ssh_out.setChecked(False)
        self._apply_saved_ssh()
        try:
            self.files_panel.clear_attached()
        except Exception:
            pass
        try:
            self.vector_panel.clear_attached()
        except Exception:
            pass
        try:
            self._update_attached_summary()
        except Exception:
            pass
        self._resume_files = []
        self._resume_prev_id = None
        self.txt_log.clear()
        self.pb.setValue(0)
        self.pb_sub.setValue(0)
        self._last_run_send_as_c = False
        self.skip_paths_current = []
        # default skip extensions always
        self.skip_exts_default = [".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".avi", ".mov"]
        try:
            self.tabs.setCurrentWidget(self.tab_run)
        except Exception:
            pass
        try:
            if self.cb_model.count() > 0:
                self.on_model_changed(self.cb_model.currentText())
        except Exception:
            pass

    def _apply_saved_ssh(self) -> None:
        ssh = getattr(self.s, "ssh", None)
        if not ssh:
            self.ed_ssh_user.clear()
            self.ed_ssh_host.clear()
            self.ed_ssh_key.clear()
            self.chk_ssh_pin_required.setChecked(False)
            self.ed_ssh_pwd.clear()
            return
        self.ed_ssh_user.setText(ssh.user or "")
        self.ed_ssh_host.setText(ssh.host or "")
        self.ed_ssh_key.setText(ssh.key or "")
        self.chk_ssh_pin_required.setChecked(bool(getattr(ssh, "pin_required", False)))
        self.ed_ssh_pwd.setText(get_secret("ssh_password") or "")

    def _save_ssh_settings(self) -> None:
        user = self.ed_ssh_user.text().strip()
        host = self.ed_ssh_host.text().strip()
        if not user or not host:
            msg_warning(self, "SSH", "Vyplň SSH user a host.")
            return
        try:
            self.s.ssh.user = user
            self.s.ssh.host = host
            self.s.ssh.key = self.ed_ssh_key.text().strip()
            self.s.ssh.password = self.ed_ssh_pwd.text()
            self.s.ssh.pin_required = bool(self.chk_ssh_pin_required.isChecked())
            save_settings(self.s, DEFAULT_SETTINGS_FILE)
            self.log("SSH settings saved.")
            msg_info(self, "SSH", "SSH nastavení uloženo.")
        except Exception as e:
            msg_warning(self, "SSH", f"Uložení selhalo: {e}")

    def on_new(self):
        if self.worker and self.worker.isRunning():
            if msg_question(self, "RUN", "Běží RUN. Zastavit a vyčistit stav?") != QMessageBox.Yes:
                return
            self.on_stop(force=True)
        self._reset_ui_state()
        self.log("State reset (NEW).")

    def on_save_state(self):
        state = self._gather_state()
        fp, _ = dialog_save_file(self, "Save state", "kajovo_state.json", "JSON (*.json)")
        if not fp:
            return
        try:
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            self.log(f"State saved to {fp}")
        except Exception as e:
            msg_critical(self, "Save", str(e))

    def on_load_state(self):
        if self.worker and self.worker.isRunning():
            if msg_question(self, "RUN", "Běží RUN. Načtení stavu zastaví aktuální postup. Pokračovat?") != QMessageBox.Yes:
                return
            self.on_stop(force=True)
        fp, _ = dialog_open_file(self, "Load state", "", "JSON (*.json)")
        if not fp:
            return
        try:
            with open(fp, "r", encoding="utf-8") as f:
                state = json.load(f)
            self._apply_state(state)
            self.log(f"State loaded from {fp}")
        except Exception as e:
            msg_critical(self, "Load", str(e))

    def on_exit(self):
        if msg_question(self, "Exit", "Ukončit aplikaci? Neuložená práce se ztratí.") != QMessageBox.Yes:
            return
        self.close()

    def _load_run_ui_state(self, run_id: str) -> Optional[dict]:
        if not run_id:
            return None
        run_dir = os.path.join(self.s.log_dir, run_id)
        req_dir = os.path.join(run_dir, "requests")
        if not os.path.isdir(req_dir):
            return None
        try:
            req_files = sorted(
                [os.path.join(req_dir, f) for f in os.listdir(req_dir) if f.lower().endswith(".json") or f.lower().endswith(".jsonl")],
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )
            for fp in req_files:
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict) and raw.get("ui_state"):
                        return raw["ui_state"]
                except Exception:
                    continue
        except Exception:
            return None
        return None

    def _load_last_response_id(self, run_id: str) -> Optional[str]:
        if not run_id:
            return None
        # prefer stored last response
        candidate = self._load_last_response_id_from_state(run_id)
        if candidate:
            return candidate
        candidate = self._load_last_response_id_from_events(run_id)
        if candidate:
            return candidate
        return self._load_last_response_id_from_responses(run_id)

    def _load_last_response_id_from_events(self, run_id: str) -> Optional[str]:
        run_dir = os.path.join(self.s.log_dir, run_id)
        events_path = os.path.join(run_dir, "events.jsonl")
        if not os.path.isfile(events_path):
            return None
        try:
            with open(events_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            return None
        for line in reversed(lines):
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") != "api.trace":
                continue
            data = item.get("data") or {}
            if data.get("action") != "complete":
                continue
            resp_id = data.get("response_id")
            if resp_id:
                return str(resp_id)
        return None

    def _load_last_response_id_from_state(self, run_id: str) -> Optional[str]:
        run_state = os.path.join(self.s.log_dir, run_id, "run_state.json")
        if not os.path.isfile(run_state):
            return None
        try:
            with open(run_state, "r", encoding="utf-8", errors="ignore") as f:
                state = json.load(f)
        except Exception:
            return None
        resp = state.get("last_structure_response_id")
        if resp:
            return str(resp)
        resp = state.get("last_response_id")
        if resp:
            return str(resp)
        return None

    def _load_last_response_id_from_responses(self, run_id: str) -> Optional[str]:
        run_dir = os.path.join(self.s.log_dir, run_id)
        resp_dir = os.path.join(run_dir, "responses")
        if not os.path.isdir(resp_dir):
            return None

        def newest_by_pattern(substrs):
            files = []
            for f in os.listdir(resp_dir):
                if not f.lower().endswith(".json"):
                    continue
                if all(s.lower() in f.lower() for s in substrs):
                    files.append(os.path.join(resp_dir, f))
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for fp in files:
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        raw = json.load(f)
                    if isinstance(raw, dict) and raw.get("id"):
                        return str(raw.get("id"))
                except Exception:
                    continue
            return None

        # Prefer structure response (A2/B2) to allow cascading continuation.
        candidate = newest_by_pattern(["A2_response"])
        if candidate:
            return candidate
        candidate = newest_by_pattern(["B2_response"])
        if candidate:
            return candidate
        candidate = newest_by_pattern(["A1_response"])
        if candidate:
            return candidate
        candidate = newest_by_pattern(["B1_response"])
        if candidate:
            return candidate

        # Fallback: newest any response id
        return newest_by_pattern([])

    def _find_related_runs_by_out_dir(self, run_id: str) -> List[str]:
        """Find other run_ids that wrote to the same out_dir (for multi ReRun chains)."""
        out_dir = None
        state_path = os.path.join(self.s.log_dir, run_id, "run_state.json")
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8", errors="ignore") as f:
                    state = json.load(f)
                out_dir = state.get("out_dir")
            except Exception:
                out_dir = None
        if not out_dir:
            return []
        target = os.path.abspath(str(out_dir))
        related: List[tuple[float, str]] = []
        try:
            for rid in os.listdir(self.s.log_dir):
                if rid == run_id:
                    continue
                rsp = os.path.join(self.s.log_dir, rid, "run_state.json")
                if not os.path.isfile(rsp):
                    continue
                try:
                    with open(rsp, "r", encoding="utf-8", errors="ignore") as f:
                        st = json.load(f)
                    od = st.get("out_dir")
                except Exception:
                    continue
                if not od:
                    continue
                if os.path.abspath(str(od)) != target:
                    continue
                related.append((os.path.getmtime(rsp), rid))
        except Exception:
            return []
        related.sort(key=lambda t: t[0], reverse=True)
        return [rid for _, rid in related]

    def _load_structure_from_run(self, run_id: str) -> tuple[List[dict], Optional[str]]:
        """Load last A2 structure (files list, response_id) from run logs, with related-run fallback."""

        def _load_single(rid: str) -> tuple[List[dict], Optional[str]]:
            resp_dir = os.path.join(self.s.log_dir, rid, "responses")
            if not os.path.isdir(resp_dir):
                return self._load_structure_from_manifest(rid)
            latest = None
            for f in sorted(os.listdir(resp_dir), key=lambda p: os.path.getmtime(os.path.join(resp_dir, p)), reverse=True):
                if "A2_response" in f and f.lower().endswith(".json"):
                    latest = os.path.join(resp_dir, f)
                    break
            if not latest:
                return self._load_structure_from_manifest(rid)
            try:
                with open(latest, "r", encoding="utf-8", errors="ignore") as fp:
                    raw = json.load(fp)
                text = extract_text_from_response(raw)
                struct = parse_json_strict(text)
                files = struct.get("files", []) or []
                resp_id = str(raw.get("id") or "")
                return files, resp_id
            except Exception:
                return self._load_structure_from_manifest(rid)

        files, resp_id = _load_single(run_id)
        if files:
            return files, resp_id
        for rid in self._find_related_runs_by_out_dir(run_id):
            files, resp_id = _load_single(rid)
            if files:
                return files, resp_id
        return [], resp_id

    def _load_structure_from_manifest(self, run_id: str) -> tuple[List[dict], Optional[str]]:
        """Fallback: read persisted resume manifest (created during ReRun skip of A1/A2)."""
        mani_dir = os.path.join(self.s.log_dir, run_id, "manifests")
        if not os.path.isdir(mani_dir):
            return [], None
        for f in sorted(os.listdir(mani_dir), key=lambda p: os.path.getmtime(os.path.join(mani_dir, p)), reverse=True):
            if "resume_structure" in f and f.lower().endswith(".json"):
                try:
                    with open(os.path.join(mani_dir, f), "r", encoding="utf-8", errors="ignore") as fp:
                        data = json.load(fp)
                    return data.get("resume_files", []) or [], data.get("resume_prev_id")
                except Exception:
                    continue
        # fallback: derive minimal structure from any saved_map if available
        try:
            mani_files = sorted(
                [
                    os.path.join(mani_dir, f)
                    for f in os.listdir(mani_dir)
                    if f.lower().endswith("_out_saved_map.json")
                ],
                key=os.path.getmtime,
                reverse=True,
            )
            for smap in mani_files:
                try:
                    with open(smap, "r", encoding="utf-8", errors="ignore") as fp:
                        data = json.load(fp)
                    saved_entries = data.get("saved", [])
                    files: List[dict] = []
                    if isinstance(saved_entries, dict):
                        saved_entries = [{"path": k, "dst": v} for k, v in saved_entries.items()]
                    if isinstance(saved_entries, list):
                        for entry in saved_entries:
                            if not isinstance(entry, dict):
                                continue
                            pth = entry.get("path") or entry.get("dst_rel") or entry.get("dst")
                            if isinstance(pth, str) and pth:
                                files.append({"path": pth, "purpose": entry.get("purpose", "")})
                    if not files and isinstance(data, dict):
                        for k in data.keys():
                            if k in ("saved", "out_dir"):
                                continue
                            if isinstance(k, str) and k:
                                files.append({"path": k, "purpose": ""})
                    if files:
                        fallback_resp = self._load_last_response_id(run_id) if hasattr(self, "_load_last_response_id") else None
                        return files, fallback_resp
                except Exception:
                    continue
        except Exception:
            pass
        return [], None

    def _gather_completed_paths(self, run_id: str, out_dir: str) -> List[str]:
        paths: List[str] = []
        resp_dir = os.path.join(self.s.log_dir, run_id, "responses")
        if os.path.isdir(resp_dir):
            for fn in os.listdir(resp_dir):
                if "A3_FILE" not in fn and "B3_FILE" not in fn:
                    continue
                fp = os.path.join(resp_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        raw = json.load(f)
                    out_arr = raw.get("output", []) or []
                    if not out_arr:
                        continue
                    content = out_arr[0].get("content") or []
                    if not content:
                        continue
                    txt = content[0].get("text") or ""
                    j = json.loads(txt)
                    path = j.get("path")
                    if isinstance(path, str):
                        paths.append(path)
                except Exception:
                    continue
        mani_dir = os.path.join(self.s.log_dir, run_id, "manifests")
        if os.path.isdir(mani_dir):
            for fn in os.listdir(mani_dir):
                if not fn.lower().endswith("_out_saved_map.json"):
                    continue
                fp = os.path.join(mani_dir, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                    saved_entries = data.get("saved", [])
                    if isinstance(saved_entries, dict):
                        saved_entries = [{"path": k} for k in saved_entries.keys()]
                    if isinstance(saved_entries, list):
                        for entry in saved_entries:
                            if not isinstance(entry, dict):
                                continue
                            pth = entry.get("path") or entry.get("dst_rel") or entry.get("dst")
                            if isinstance(pth, str):
                                paths.append(pth)
                except Exception:
                    continue
        # dedupe
        out: List[str] = []
        for p in paths:
            if p not in out:
                out.append(p)
        return out

    def on_rerun(self):
        rid = self.ed_rerun.text().strip()
        if not rid:
            msg_info(self, "ReRun", "Zadej RUN_ID.")
            return
        if self.worker is not None:
            msg_warning(self, "ReRun", "Probíhá jiný RUN. Nejprve ho ukonči.")
            return
        state = self._load_run_ui_state(rid)
        if not state:
            msg_warning(self, "ReRun", f"Nepodařilo se načíst stav pro {rid}.")
            return
        last_resp = self._load_last_response_id(rid) or ""
        self._apply_state(state)
        self.skip_paths_current = self._gather_completed_paths(rid, state.get("out_dir", ""))
        if last_resp:
            self.ed_response_id.setText(last_resp)
            self.log(f"ReRun {rid}: navazuji na response_id={last_resp}")
        else:
            self.log(f"ReRun {rid}: response_id nenalezen, běžný restart.")
        # preload structure/files for resume (skip A1/A2)
        try:
            files, resp_id = self._load_structure_from_run(rid)
            self._resume_files = files
            self._resume_prev_id = last_resp or resp_id
            if files:
                self.log(f"ReRun {rid}: nalezena struktura A2 ({len(files)} souborů), přeskočím A1/A2.")
        except Exception:
            self._resume_files = []
            self._resume_prev_id = last_resp or ""
        self.on_go()

    def _kill_worker_if_running(self):
        if not (self.worker and self.worker.isRunning()):
            return

        self.log("STOP: worker still running after cooperative request, waiting extra grace period...")
        try:
            if self.worker.wait(2000):
                self.log("STOP: worker finished cooperatively during grace period.")
                return
        except Exception as e:
            self.log(f"STOP: wait before force-stop failed: {e}")

        self.log("Force stopping RUN thread (terminate fallback).")
        try:
            self.worker.terminate()
            self.worker.wait(2000)
        except Exception as e:
            self.log(f"STOP: terminate fallback failed: {e}")

        if self.run_logger:
            try:
                self.run_logger.update_state(
                    {
                        "status": "force_killed",
                        "force_killed_at": time.time(),
                        "reason": "ui.terminate_fallback",
                    }
                )
                self.run_logger.event("ui.worker.force_kill", {"reason": "terminate_fallback"})
            except Exception as e:
                self.log(f"STOP: failed to write force-kill marker: {e}")

        self._dispose_worker()
        if self.progress_dialog:
            try:
                self.progress_dialog.close()
            except Exception as e:
                self.log(f"STOP: failed to close progress dialog after force-stop: {e}")
            self.progress_dialog = None
        try:
            self._progress_timer.stop()
        except Exception as e:
            self.log(f"STOP: failed to stop progress timer after force-stop: {e}")

    def _browse_dir(self, target: QLineEdit):
        d = dialog_select_dir(self, "Select directory", target.text() or os.getcwd())
        if d:
            target.setText(d)
            if self.chk_in_eq_out.isChecked() and target is self.ed_in:
                self.ed_out.setText(d)

    def _browse_file(self, target: QLineEdit):
        fp, _ = dialog_open_file(self, "Select file", target.text() or os.getcwd(), "All (*.*)")
        if fp:
            target.setText(fp)

    def on_in_eq_out_changed(self):
        if self.chk_in_eq_out.isChecked():
            self.ed_out.setText(self.ed_in.text())
            self.ed_out.setEnabled(False)
            self.btn_out.setEnabled(False)
        else:
            self.ed_out.setEnabled(True)
            self.btn_out.setEnabled(True)

    def on_attached_changed(self, ids: List[str]):
        self.log(f"Attached file_ids: {', '.join(ids) if ids else '(none)'}")
        self._update_attached_summary()

    def on_vs_attached_changed(self, ids: List[str]):
        self.log(f"Attached vector stores: {', '.join(ids) if ids else '(none)'}")
        self._update_attached_summary()

    def _update_attached_summary(self):
        files = []
        try:
            files = self.files_panel.attached_ids()
        except Exception:
            files = []
        vs = []
        try:
            vs = self.vector_panel.attached_ids()
        except Exception:
            vs = []
        txt = []
        txt.append("Files:")
        txt.append(", ".join(files) if files else "(none)")
        txt.append("\nVector stores:")
        txt.append(", ".join(vs) if vs else "(none)")
        try:
            self.txt_attached_summary.setPlainText("\n".join(txt))
        except Exception:
            pass

    def on_settings(self):
        dlg = SettingsDialog(self.s, self)
        dlg.exec()
        try:
            from ..core.config import load_settings
            self.s = load_settings()
        except Exception:
            pass

    def on_pricing(self):
        try:
            self.tabs.setCurrentWidget(self.tab_pricing)
        except Exception:
            pass

    def _api_show(self):
        val = os.environ.get("OPENAI_API_KEY", "")
        if not val:
            msg_info(self, "API-KEY", "OPENAI_API_KEY není nastaven.")
            return
        self.ed_settings_apikey.setText(val)
        self.ed_settings_apikey.setEchoMode(QLineEdit.Normal)

    def _apply_api_key_change(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.files_panel.set_api_key(self.api_key)
        try:
            self.vector_panel.set_api_key(self.api_key)
        except Exception:
            pass
        try:
            self.batch_panel.set_api_key(self.api_key)
        except Exception:
            pass
        try:
            self.pricing_panel.set_api_key(self.api_key)
        except Exception:
            pass
        self._refresh_models_best_effort()
        self._auto_probe_models_on_start()

    def _set_env_api_key(self, value: str) -> bool:
        try:
            if os.name == "nt":
                subprocess.run(["setx", "OPENAI_API_KEY", value], capture_output=True, text=True, check=True, shell=False)
                return True
        except Exception:
            return False
        return False

    def _api_save(self):
        val = (self.ed_settings_apikey.text() or "").strip()
        if not val:
            msg_warning(self, "API-KEY", "Prázdný klíč nelze uložit.")
            return
        os.environ["OPENAI_API_KEY"] = val
        ok = self._set_env_api_key(val)
        self._apply_api_key_change()
        self.log("API key saved (env updated)." + ("" if ok else " (jen aktuální sezení)"))
        msg_info(self, "API-KEY", "Uloženo." + ("" if ok else " (Jen pro aktuální běh.)"))
        self.ed_settings_apikey.setEchoMode(QLineEdit.Password)

    def _api_delete(self):
        os.environ["OPENAI_API_KEY"] = ""
        ok = self._set_env_api_key("")
        self._apply_api_key_change()
        self.log("API key deleted from env." + ("" if ok else " (jen aktuální sezení)"))
        msg_info(self, "API-KEY", "Smazáno." + ("" if ok else " (Jen pro aktuální běh.)"))
        self.ed_settings_apikey.clear()
        self.ed_settings_apikey.setEchoMode(QLineEdit.Password)

    def _refresh_models_best_effort(self):
        current = self.cb_model.currentText()
        self.all_models = []
        if self.api_key:
            with BusyPopup(self, "Načítám modely..."):
                try:
                    client = OpenAIClient(self.api_key)
                    models = with_retry(lambda: client.list_models(), self.s.retry, self.breaker)
                    names = sorted({m.get("id", "") for m in models if m.get("id")})
                    if names:
                        self.all_models = names
                except Exception as e:
                    self.log(f"Model refresh failed: {e}")
        if not self.all_models:
            self.all_models = ["gpt-4o-mini", "gpt-4o"]

        preferred = getattr(self.s, "default_model", "") or current       
        self._apply_model_filter(preserve=preferred if preferred else None)

    def _auto_refresh_pricing(self):
        try:
            if not self.price_table.rows:
                ok, _ = self.price_table.refresh_from_url(self.s.pricing.source_url)
                if not ok and hasattr(self, "pricing_panel"):
                    self.pricing_panel.on_refresh_via_model()
            self.pricing_panel.load_prices()
        except Exception:
            pass

    def _start_pricing_audit_loop(self):
        try:
            if hasattr(self, "pricing_panel"):
                self.pricing_panel.start_audit(quiet=True)
        except Exception:
            pass
        try:
            if self.pricing_audit_timer is None:
                self.pricing_audit_timer = QTimer(self)
                self.pricing_audit_timer.setInterval(int(15 * 60 * 1000))  # every 15 minutes
                self.pricing_audit_timer.timeout.connect(lambda: self.pricing_panel.start_audit(quiet=True))
                self.pricing_audit_timer.start()
        except Exception:
            pass

    def _maybe_resume_hint(self):
        rid = find_last_incomplete_run(self.s.log_dir)
        if rid:
            self.log(f"Pozn.: nalezen nedokončený RUN: {rid} (viz {os.path.join(self.s.log_dir, rid)})")

    def _apply_model_filter(self, preserve: Optional[str] = None, *_unused):
        filt = (self.ed_model_filter.text() or "").strip().lower()
        models = list(self.all_models)
        filtered = [m for m in models if filt in m.lower()] if filt else models
        if not filtered and models:
            filtered = models
        sel = preserve or self.cb_model.currentText()
        if sel == self.ed_model_filter.text():
            sel = self.cb_model.currentText()
        self.cb_model.blockSignals(True)
        self.cb_model.clear()
        if filtered:
            self.cb_model.addItems(filtered)
        if sel and sel in filtered:
            self.cb_model.setCurrentText(sel)
        elif filtered:
            self.cb_model.setCurrentIndex(0)
        self.cb_model.blockSignals(False)
        if self.cb_model.count() > 0:
            self.on_model_changed(self.cb_model.currentText())
        try:
            if hasattr(self, "cascade_panel"):
                self.cascade_panel.refresh_models()
        except Exception:
            pass
        self._refresh_model_tab()

    def _refresh_model_tab(self):
        q = (self.ed_model_search_tab.text() or "").strip().lower()
        need_prev = self.chk_model_prev.isChecked()
        need_temp = self.chk_model_temp.isChecked()
        need_fs = self.chk_model_fs.isChecked()
        need_vs = self.chk_model_vs.isChecked()
        include_untested = self.chk_model_include_untested.isChecked()

        self.lst_models.clear()
        models = sorted(set(self.all_models))
        for m in models:
            if q and q not in m.lower():
                continue
            caps = self.caps_cache.get(m)
            if not self._caps_match(caps, need_prev, need_temp, need_fs, need_vs, include_untested):
                continue
            status = self._format_caps_status(caps)
            it = QListWidgetItem(f"{m}   [{status}]")
            it.setData(Qt.UserRole, m)
            self.lst_models.addItem(it)
        self._update_model_info()
        self._render_default_model_label()

    def _format_caps_status(self, caps: Optional[ModelCapabilities]) -> str:
        if caps is None:
            return "untested"
        def bool_flag(value: Optional[bool]) -> str:
            if value is None:
                return "?"
            return "1" if value else "0"
        return (
            f"prev={bool_flag(caps.supports_previous_response_id)} "
            f"temp={bool_flag(caps.supports_temperature)} "
            f"fs={bool_flag(caps.supports_file_search)} "
            f"vs={bool_flag(caps.supports_vector_store)}"
        )

    def _default_model_text(self) -> str:
        val = getattr(self.s, "default_model", "") or ""
        return f"Výchozí model: {val or '(nenastaven)'}"

    def _render_default_model_label(self):
        try:
            self.lbl_default_model.setText(self._default_model_text())
        except Exception:
            pass

    def _set_default_model(self):
        item = self.lst_models.currentItem()
        model_id = item.data(Qt.UserRole) if item else None
        if not model_id:
            model_id = self.cb_model.currentText().strip()
        if not model_id:
            msg_info(self, "Models", "Vyber model.")
            return
        self.s.default_model = model_id
        try:
            save_settings(self.s, DEFAULT_SETTINGS_FILE)
        except Exception:
            pass
        self._apply_model_filter(preserve=model_id)
        self._render_default_model_label()
        self.log(f"Default model set to {model_id}")
        msg_info(self, "Models", f"Výchozí model nastaven: {model_id}")

    def _caps_match(self,
                    caps: Optional[ModelCapabilities],
                    need_prev: bool,
                    need_temp: bool,
                    need_fs: bool,
                    need_vs: bool,
                    include_untested: bool) -> bool:
        if caps is None:
            return include_untested
        if need_prev and not caps.supports_previous_response_id:
            return False
        if need_temp and not caps.supports_temperature:
            return False
        if need_fs and not caps.supports_file_search:
            return False
        if need_vs and not caps.supports_vector_store:
            return False
        return True

    def _update_model_info(self):
        item = self.lst_models.currentItem()
        if not item:
            self.lbl_model_info.setText("No model selected")
            return
        model_id = item.data(Qt.UserRole)
        caps = self.caps_cache.get(model_id)
        info = []
        if caps:
            info.append(f"last tested {time.strftime('%Y-%m-%d', time.localtime(caps.tested_at or 0))}")
            info.append(f"prev={int(caps.supports_previous_response_id)}")
            info.append(f"temp={int(caps.supports_temperature)}")
            info.append(f"fs={int(caps.supports_file_search)}")
            info.append(f"vs={int(caps.supports_vector_store)}")
        else:
            info.append("untested")
        self.lbl_model_info.setText(f"{model_id} — {' | '.join(info)}")

    def _apply_selected_model(self):
        item = self.lst_models.currentItem()
        if not item:
            msg_info(self, "Models", "Select a model first.")
            return
        model_id = item.data(Qt.UserRole)
        self._set_active_model(model_id)
        self.tabs.setCurrentWidget(self.tab_run)
        self.log(f"Model selected from MODELS tab: {model_id}")

    def _set_active_model(self, model_id: str):
        idx = self.cb_model.findText(model_id)
        if idx < 0:
            self.cb_model.addItem(model_id)
            idx = self.cb_model.findText(model_id)
        if idx >= 0:
            self.cb_model.setCurrentIndex(idx)

    # ---------- model caps UX ----------
    def on_model_changed(self, model_id: str):
        caps = self.caps_cache.get(model_id)
        self._render_caps_label(caps)

        supports_temp = True if caps is None else bool(caps.supports_temperature)
        self.sp_temp.setEnabled(supports_temp)
        if not supports_temp:
            self.sp_temp.setValue(0.0)


    def _render_caps_label(self, caps: Optional[ModelCapabilities]):
        def color(val: Optional[bool]) -> str:
            if val is None:
                return "white"
            return "#2FA0FF" if val else "#6b7b8c"

        fs = None if caps is None else bool(caps.supports_file_search)
        vs = fs
        temp = None if caps is None else bool(caps.supports_temperature)
        self.lbl_caps.setText(
            f"<span style='color:{color(fs)};'>FILE_SEARCH</span> - "
            f"<span style='color:{color(vs)};'>VECTOR STORE</span> - "
            f"<span style='color:{color(temp)};'>TEMPERATURE</span>"
        )

    def _auto_probe_models_on_start(self):
        if not self.api_key:
            return
        models = set(self.all_models) if self.all_models else set()
        # include anything already v combo box (uživatel mohl dopsat ručně)
        for i in range(self.cb_model.count()):
            txt = self.cb_model.itemText(i)
            if txt:
                models.add(txt)
        current = self.cb_model.currentText()
        if current:
            models.add(current)
        missing = [m for m in models if self.caps_cache.get(m) is None]
        if not missing:
            return
        self._start_probe(missing, ttl_hours=0.0)

    def on_probe_models(self):
        if not self.api_key:
            msg_warning(self, "Probe", "Nejdřív nastav OPENAI_API_KEY.")
            return
        models = list(self.all_models) if self.all_models else [self.cb_model.itemText(i) for i in range(self.cb_model.count())]
        self._start_probe(models, ttl_hours=0.0)  # force probe all

    def _start_probe(self, models: List[str], ttl_hours: float):
        if self.probe_worker is not None:
            msg_info(self, "Probe", "Probe už běží.")
            return
        self.log(f"Starting model probe for {len(models)} model(s)...")
        self._probe_busy = BusyPopup(self, "Probe models...").start()
        self.probe_worker = ModelProbeWorker(self.s, self.api_key, self.caps_cache, models_to_probe=models, ttl_hours=ttl_hours)
        self.probe_worker.progress.connect(self.pb_sub.setValue)
        self.probe_worker.logline.connect(self.log)
        self.probe_worker.model_status.connect(lambda mid, st: self.log(f"Probe {mid}: {st}"))
        self.probe_worker.finished.connect(self._probe_finished)
        self.probe_worker.start()

    def _probe_finished(self):
        self.log("Model probe finished.")
        self.probe_worker = None
        self.caps_cache.load()
        self.on_model_changed(self.cb_model.currentText())
        try:
            if hasattr(self, "_probe_busy") and self._probe_busy:
                self._probe_busy.close()
        except Exception:
            pass
        self._probe_busy = None

    # ---------- run ----------
    def _validate_paths(self, mode: str, send_as_c: bool) -> bool:
        in_dir = self.ed_in.text().strip()
        out_dir = self.ed_out.text().strip()
        if mode == "MODIFY" and not send_as_c:
            if not in_dir or not os.path.isdir(in_dir):
                msg_warning(self, "Paths", "IN musí existovat (pro MODIFY).")
                return False
        if mode in ("GENERATE", "MODIFY", "QFILE"):
            if not out_dir:
                msg_warning(self, "Paths", "OUT není nastaven.")
                return False
            if not os.path.isdir(out_dir):
                try:
                    os.makedirs(out_dir, exist_ok=True)
                except Exception as e:
                    msg_critical(self, "Paths", f"Nelze vytvořit OUT: {e}")
                    return False
        return True

    def on_mode_changed(self, mode: str):
        is_qfile = mode == "QFILE"
        is_cascade = mode == "KASKADA"
        self.chk_send_as_c.setEnabled((not is_qfile) and (not is_cascade))
        if is_qfile:
            self.chk_send_as_c.setChecked(False)
        if is_cascade:
            self.chk_send_as_c.setChecked(False)
        self.ed_response_id.setEnabled(not is_cascade)
        self.row_cascade_selector.setVisible(is_cascade)
        if is_cascade:
            self.refresh_run_cascades()

    def on_go(self):
        if self.worker is not None:
            msg_info(self, "Run", "Běží jiný RUN.")
            return
        if not self.api_key:
            msg_warning(self, "API-KEY", "Nejdřív nastav OPENAI_API_KEY.")
            return

        mode = self.cb_mode.currentText()
        send_as_c = bool(self.chk_send_as_c.isChecked())
        if mode == "KASKADA":
            send_as_c = False
            self.chk_send_as_c.setChecked(False)
        if not self._validate_paths(mode, send_as_c):
            return

        if mode == "QFILE" and send_as_c:
            msg_warning(self, "Mode", "QFILE nepodporuje SEND AS BATCH.")
            return

        if self.txt_response_view is not None:
            self.txt_response_view.clear()

        model_id = self.cb_model.currentText().strip()
        caps = self.caps_cache.get(model_id)
        caps_dict = caps.to_dict() if caps else {
            "model": model_id,
            "supports_previous_response_id": True,
            "supports_temperature": True,
            "supports_file_search": False,
            "errors": {},
        }

        # Hard gate only if explicit param rejection was detected.
        if (not send_as_c) and mode in ("GENERATE", "MODIFY"):
            if caps and _caps_prev_id_explicitly_unsupported(caps):
                msg_critical(
                    self,
                    "Model",
                    "Probe zjistil, že server explicitně odmítá previous_response_id pro tento model. "
                    "Kaskádu nelze spustit s tímto modelem.",
                )
                return

        if mode == "KASKADA":
            if self.cb_run_cascade.count() == 0:
                msg_warning(self, "Kaskáda", "Není vybraná uložená kaskáda.")
                return
            cpath = str(self.cb_run_cascade.currentData() or "").strip()
            if not cpath or not os.path.isfile(cpath):
                msg_warning(self, "Kaskáda", "Vybraná kaskáda neexistuje.")
                return
            try:
                with open(cpath, "r", encoding="utf-8") as f:
                    cdef = CascadeDefinition.from_dict(json.load(f))
            except Exception as e:
                msg_critical(self, "Kaskáda", f"Načtení kaskády selhalo: {e}")
                return
            cfg_c = CascadeRunConfig(
                project=self.ed_project.text().strip(),
                cascade=cdef,
                in_dir=self.ed_in.text().strip(),
                out_dir=self.ed_out.text().strip(),
            )
            self._last_run_send_as_c = False
            self.run_logger = None
            self.log(f"KASKÁDA started: {os.path.basename(cpath)}")
            self.worker = CascadeRunWorker(cfg_c, self.s, self.api_key, self.db, self.price_table)
            self._progress_last_ts = time.time()
            self._progress_timer.start()
            self.progress_dialog = ProgressDialog(self)
            self.progress_dialog.btn_stop.clicked.connect(self.on_stop)
            self.progress_dialog.show()
            self.worker.progress.connect(self.pb.setValue)
            self.worker.progress.connect(lambda v: self.progress_dialog.set_progress(v) if self.progress_dialog else None)
            self.worker.progress.connect(lambda _: self._mark_progress_activity())
            self.worker.subprogress.connect(self.pb_sub.setValue)
            self.worker.subprogress.connect(lambda v: self.progress_dialog.set_subprogress(v) if self.progress_dialog else None)
            self.worker.subprogress.connect(lambda _: self._mark_progress_activity())
            self.worker.status.connect(lambda s: self.progress_dialog.set_status(s) if self.progress_dialog else None)
            self.worker.status.connect(lambda _: self._mark_progress_activity())
            self.worker.logline.connect(self.log)
            self.worker.logline.connect(lambda s: self.progress_dialog.add_log(s) if self.progress_dialog else None)
            self.worker.finished_ok.connect(self.on_run_ok)
            self.worker.finished_err.connect(self.on_run_err)
            self.worker.start()
            return

        self._last_run_send_as_c = bool(send_as_c)
        run_id = new_run_id()
        self.run_logger = RunLogger(self.s.log_dir, run_id, project_name=self.ed_project.text().strip())
        self.log(f"RUN started: {run_id}")

        attached_file_ids = self.files_panel.attached_ids()
        attached_vector_store_ids = self.vector_panel.attached_ids()
        input_file_ids = list(attached_file_ids)
        oversize_ids = []
        if attached_file_ids:
            with BusyPopup(self, "Kontroluji velikost souborů..."):
                try:
                    client = OpenAIClient(self.api_key)
                    for fid in attached_file_ids:
                        try:
                            meta = with_retry(lambda f=fid: client.retrieve_file(f), self.s.retry, self.breaker)
                            size = int(meta.get("bytes") or 0)
                        except Exception:
                            size = 0
                        if size > 32 * 1024 * 1024:
                            oversize_ids.append(fid)
                except Exception:
                    oversize_ids = []
        if oversize_ids:
            input_file_ids = [fid for fid in attached_file_ids if fid not in oversize_ids]
            if not attached_vector_store_ids:
                dlg = StyledMessageDialog(
                    self,
                    "Files API",
                    "Soubor(y) nad 32MB nelze odeslat jako input_file.\n"
                    "Pokud chceš použít data, založ Vector Store a připoj jej.\n\n"
                    "Mám pokračovat jen s textovou referencí (bez input_file)?"
                    ,
                    buttons=[("Jen text", QMessageBox.AcceptRole), ("Zrušit", QMessageBox.RejectRole)],
                    default_code=QMessageBox.AcceptRole,
                )
                if dlg.exec() != QMessageBox.AcceptRole:
                    self.log(f"Oversize file_ids blocked (>32MB): {', '.join(oversize_ids)}")
                    return
                self.log(f"Oversize file_ids allowed as text-only (>32MB): {', '.join(oversize_ids)}")
            self.log(f"Oversize file_ids filtered (>{32}MB): {', '.join(oversize_ids)}")

        pin_required = bool(self.chk_ssh_pin_required.isChecked())
        os.environ["KAJOVO_SSH_PIN_REQUIRED"] = "1" if pin_required else "0"
        if pin_required:
            self.log("SSH diagnostics policy: pin required is enabled.")

        cfg = UiRunConfig(
            project=self.ed_project.text().strip(),
            prompt=self.txt_prompt.toPlainText(),
            mode=mode,
            send_as_c=send_as_c,
            model=model_id,
            response_id=self.ed_response_id.text().strip(),
            attached_file_ids=attached_file_ids,
            input_file_ids=input_file_ids,
            attached_vector_store_ids=attached_vector_store_ids,
            in_dir=self.ed_in.text().strip(),
            out_dir=self.ed_out.text().strip(),
            in_equals_out=bool(self.chk_in_eq_out.isChecked()),
            versing=bool(self.chk_versing.isChecked()),
            temperature=float(self.sp_temp.value()),
            use_file_search=True,
            diag_windows_in=bool(self.chk_diag_win_in.isChecked()),
            diag_windows_out=bool(self.chk_diag_win_out.isChecked()),
            diag_ssh_in=bool(self.chk_diag_ssh_in.isChecked()),
            diag_ssh_out=bool(self.chk_diag_ssh_out.isChecked()),
            ssh_user=self.ed_ssh_user.text().strip(),
            ssh_host=self.ed_ssh_host.text().strip(),
            ssh_key=self.ed_ssh_key.text().strip(),
            ssh_password=self.ed_ssh_pwd.text(),
            skip_paths=list(self.skip_paths_current),
            skip_exts=list(self.skip_exts_default),
            model_caps=caps_dict,
            resume_files=getattr(self, "_resume_files", []),
            resume_prev_id=getattr(self, "_resume_prev_id", None),
        )

        self.worker = RunWorker(cfg, self.s, self.api_key, self.run_logger, self.db, self.price_table)

        # start heartbeat before thread launch
        self._progress_last_ts = time.time()
        self._progress_timer.start()

        self.progress_dialog = ProgressDialog(self)
        self.progress_dialog.btn_stop.clicked.connect(self.on_stop)
        try:
            self.progress_dialog.chk_bzz.setChecked(bool(self._bzz_default))
        except Exception as e:
            self.log(f"Progress dialog init warning (chk_bzz): {e}")
        self.progress_dialog.show()

        self.worker.progress.connect(self.pb.setValue)
        self.worker.progress.connect(lambda v: self.progress_dialog.set_progress(v) if self.progress_dialog else None)
        self.worker.progress.connect(lambda _: self._mark_progress_activity())

        self.worker.subprogress.connect(self.pb_sub.setValue)
        self.worker.subprogress.connect(lambda v: self.progress_dialog.set_subprogress(v) if self.progress_dialog else None)
        self.worker.subprogress.connect(lambda _: self._mark_progress_activity())

        # status už nemění titulkový řádek, zůstává jen statický název
        self.worker.status.connect(lambda s: self.progress_dialog.set_status(s) if self.progress_dialog else None)
        self.worker.status.connect(lambda _: self._mark_progress_activity())

        self.worker.logline.connect(self.log)
        self.worker.logline.connect(lambda s: self.progress_dialog.add_log(s) if self.progress_dialog else None)

        self.worker.finished_ok.connect(self.on_run_ok)
        self.worker.finished_err.connect(self.on_run_err)
        self.worker.start()
        # clear resume hints after start
        self._resume_files = []
        self._resume_prev_id = None

    def on_stop(self, force: bool = False):
        if self.worker is None:
            return
        if not force:
            if msg_question(self, "STOP", "Zastavit aktuální RUN? Rozdělaná práce se ztratí.") != QMessageBox.Yes:
                return
        try:
            self.worker.request_stop()
        except Exception as e:
            self.log(f"STOP: request_stop failed: {e}")
        self.log("Stop requested (cooperative).")
        QTimer.singleShot(1500, self._kill_worker_if_running)

    def on_run_ok(self, result: dict):
        rid = self.run_logger.run_id if self.run_logger else str((result or {}).get("run_id") or "")
        is_batch = bool(self._last_run_send_as_c or (result.get("mode") == "C"))
        batch_id = str(result.get("batch_id") or "") if isinstance(result, dict) else ""
        notify_on_end = False
        if self.progress_dialog and hasattr(self.progress_dialog, "chk_bzz"):
            notify_on_end = bool(self.progress_dialog.chk_bzz.isChecked())
            self._bzz_default = notify_on_end
        self.log(f"RUN completed: {rid}")
        self._dispose_worker()
        self._progress_timer.stop()
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        self.pb.setValue(100)
        self.pb_sub.setValue(100)

        last_resp_id = str(result.get("response_id") or "")
        if last_resp_id and self.ed_response_id.isEnabled():
            self.ed_response_id.setText(last_resp_id)

        resp_text = ""
        if "text" in result:
            resp_text = str(result.get("text") or "")
        elif "contract" in result:
            try:
                resp_text = json.dumps(result.get("contract"), ensure_ascii=False, indent=2)
            except Exception:
                resp_text = str(result.get("contract"))
        if self.txt_response_view is not None:
            self.txt_response_view.setPlainText(resp_text)

        if self.chk_diag_win_out.isChecked() or self.chk_diag_ssh_out.isChecked():
            if not is_batch:
                self._maybe_execute_repair(self.ed_out.text().strip())

        out_dir = self.ed_out.text().strip()
        if (not is_batch) and out_dir and os.path.isdir(out_dir):
            if msg_question(self, "Open OUT", "Otevřít OUT složku?") == QMessageBox.Yes:
                try:
                    if os.name == "nt":
                        os.startfile(out_dir)  # type: ignore
                    else:
                        subprocess.Popen(["xdg-open", out_dir])
                except Exception:
                    pass
        if is_batch:
            msg = f"BATCH request odeslán (batch_id={batch_id or 'neznámý'}). Sleduj záložku BATCH a stáhni výstup do OUT."
            self.log(msg)

        if notify_on_end:
            self._send_bzz_notification(rid)

    def on_run_err(self, err: str):
        rid = self.run_logger.run_id if self.run_logger else ""
        self.log(f"RUN failed: {rid} -> {err}")
        self._dispose_worker()
        self._progress_timer.stop()
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        msg_critical(self, "RUN failed", err)

    def _dispose_worker(self, timeout_ms: int = 10000):
        if not self.worker:
            return
        try:
            if self.worker.isRunning():
                self.worker.wait(timeout_ms)
        except Exception:
            pass
        try:
            self.worker.deleteLater()
        except Exception:
            pass
        self.worker = None
        try:
            self._progress_timer.stop()
        except Exception:
            pass

    def _dispose_probe_worker(self, timeout_ms: int = 3000):
        if not self.probe_worker:
            return
        try:
            if self.probe_worker.isRunning():
                self.probe_worker.wait(timeout_ms)
        except Exception:
            pass
        try:
            self.probe_worker.deleteLater()
        except Exception:
            pass
        self.probe_worker = None

    def closeEvent(self, event):
        # Ensure background threads stop cleanly to avoid QThread destruction errors.
        if self.worker and self.worker.isRunning():
            self.log("Stopping active RUN before exit...")
            try:
                self.worker.request_stop()
            except Exception:
                pass
            if not self.worker.wait(5000):
                msg_warning(self, "Exit", "Probíhá RUN, počkej na dokončení nebo stiskni STOP.")
                event.ignore()
                return
        self._dispose_worker()

        if self.probe_worker and self.probe_worker.isRunning():
            if msg_question(self, "Exit", "Model probe ještě běží. Zastavit a ukončit?") != QMessageBox.Yes:
                event.ignore()
                return
            self.log("Stopping model probe before exit...")
            try:
                self.probe_worker.request_stop()
            except Exception as e:
                self.log(f"Probe stop request failed: {e}")
            if not self.probe_worker.wait(2000):
                self.log("Model probe still running, applying terminate fallback on exit.")
                try:
                    self.probe_worker.terminate()
                except Exception as e:
                    self.log(f"Probe terminate fallback failed: {e}")
                self.probe_worker.wait(1000)
            try:
                if hasattr(self, "_probe_busy") and self._probe_busy:
                    self._probe_busy.close()
            except Exception as e:
                self.log(f"Failed to close probe busy popup: {e}")
        self._dispose_probe_worker()

        if self.progress_dialog:
            try:
                self.progress_dialog.close()
            except Exception:
                pass
            self.progress_dialog = None

        super().closeEvent(event)

    def _maybe_execute_repair(self, out_dir: str):
        if not out_dir or not os.path.isdir(out_dir):
            return
        readme = os.path.join(out_dir, EXPECTED_REPAIR_README)
        if not os.path.exists(readme):
            self.log("Diagnostics OUT: readmerepair.txt not found.")
            return

        try:
            with open(readme, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            content = "(nelze načíst readmerepair.txt)"

        self.log("Diagnostics OUT: readmerepair.txt detected.")
        if msg_question(self, "Repair", f"Nalezen {EXPECTED_REPAIR_README}. Automatické spuštění je defaultně vypnuté. Chcete pokračovat ručně?") != QMessageBox.Yes:
            return

        candidates = [
            os.path.join(out_dir, "RUN_THIS_SCRIPT_REPAIRME_KAJOVO_WINDOWS.bat"),
            os.path.join(out_dir, "run_this_script_repairme_kajovo_windows.bat"),
            os.path.join(out_dir, "run_this_script_repairme_kajovo.sh"),
            os.path.join(out_dir, "RUN_THIS_SCRIPT_REPAIRME_KAJOVO.sh"),
        ]
        script = next((p for p in candidates if os.path.exists(p)), None)
        if not script:
            msg_warning(self, "Repair", "Repair script nebyl nalezen.")
            return

        log_path = os.path.join(out_dir, "_repair_exec_log.txt")
        try:
            import hashlib
            with open(script, "rb") as sf:
                sha256 = hashlib.sha256(sf.read()).hexdigest()
            preview = (content[:2000] + "\n..." ) if len(content) > 2000 else content
            warn = (
                "POZOR: spouštíte nedůvěryhodný repair script.\n\n"
                f"Script: {os.path.basename(script)}\n"
                f"SHA256: {sha256}\n\n"
                f"Obsah {EXPECTED_REPAIR_README}:\n{preview}"
            )
            if msg_question(self, "Repair warning", warn) != QMessageBox.Yes:
                return
            if os.name == "nt" and script.lower().endswith(".bat"):
                p = subprocess.run(["cmd", "/c", script], cwd=out_dir, capture_output=True, text=True, shell=False)
            else:
                p = subprocess.run(["bash", script], cwd=out_dir, capture_output=True, text=True, shell=False)
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(
                    "READMEREPAIR:\n"
                    + content
                    + "\n\nRETURN_CODE:\n"
                    + str(getattr(p, "returncode", ""))
                    + "\n\nSTDOUT:\n"
                    + (p.stdout or "")
                    + "\n\nSTDERR:\n"
                    + (p.stderr or "")
                )
            msg_info(self, "Repair", f"Hotovo. Log: {log_path}")
        except Exception as e:
            msg_critical(self, "Repair", f"Nelze spustit: {e}")
