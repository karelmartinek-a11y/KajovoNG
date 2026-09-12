"""Pracovní plocha: navigace, snímek zadání a životní cyklus nezávislých běhů."""

import json
import os
import time
from pathlib import Path
from dataclasses import asdict
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QFrame,
    QHBoxLayout,
    QTabWidget,
    QCheckBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
)
from ..core.config import save_settings, DEFAULT_SETTINGS_FILE
from ..core.secret_store import load_api_key, get_secret
from ..core.model_capabilities import ModelCapabilitiesCache
from ..core.model_registry import model_spec, selectable, matrix_version
from ..core.request_rules import validate_run_options, uses_reasoning_defaults
from ..core.pipeline import UiRunConfig, RunWorker
from ..core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from ..core.cascade_types import CascadeDefinition
from ..core.openai_client import OpenAIClient
from ..core.runlog import RunLogger, find_last_incomplete_run, verified_output_evidence
from ..core.progress import ProgressEvent
from ..core.utils import new_run_id, safe_join_under_root, atomic_write_text, RUN_ID_RE
from .recovery import recover_run
from ..core.batch_completion import read_state, batch_ids
from .design import (
    column,
    row,
    label,
    button,
    card,
    form,
    text,
    combo,
    number,
    editor,
    scroll,
    install_ui_style,
    PageStack,
)
from .dialogs import (
    msg_info,
    msg_warning,
    msg_critical,
    msg_question,
    dialog_open_file,
    dialog_save_file,
    dialog_select_dir,
    ProgressDialog,
)
from .jobs import Jobs
from .resources import FilesPanel, VectorStoresPanel
from .cascades import CascadePanel
from .history import ResponseRequestPanel
from .settings import SettingsPage
from .batches import BatchPanel
from .versions import GitHubPanel


class MainWindow(QMainWindow):
    GENERATE_MODEL_MAIN_OPTION = "Stejný jako hlavní model"

    def __init__(self, settings):
        super().__init__()
        install_ui_style()
        self.setWindowTitle("Kájovo NG · Pracovní studio")
        self.resize(1366, 920)
        self.s = settings
        for key in ("log_dir", "cache_dir"):
            value = str(
                Path(getattr(settings, key) or ("LOG" if key == "log_dir" else "cache")).resolve()
            )
            setattr(settings, key, value)
            Path(value).mkdir(parents=True, exist_ok=True)
        try:
            self.api_key = load_api_key()
        except Exception as exc:
            self.api_key = ""
            msg_warning(self, "API klíč nelze načíst", str(exc))
        self.caps_cache = ModelCapabilitiesCache(
            str(Path(settings.cache_dir) / "model_capabilities.json")
        )
        self.caps_cache.bind(self.api_key)
        self.all_models = []
        self.jobs = Jobs(self)
        self._run_contexts = {}
        self._pending_replacement = None
        self._active_run_key = None
        self._resume_files = []
        self._resume_prev_id = None
        self.skip_paths_current = []
        self.skip_exts_default = [
            ".mp3",
            ".wav",
            ".flac",
            ".aac",
            ".ogg",
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
        ]
        self.worker = self.progress_dialog = self.run_logger = None
        self.navigation = {}
        self.pages = {}
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setMinimumWidth(160)
        nav = column(sidebar, 14)
        nav.addWidget(label("KÁJOVO NG", "Brand"))
        nav.addWidget(label("PRACOVNÍ STUDIO", "Hint"))
        sidebar_scroll = scroll(sidebar)
        sidebar_scroll.setObjectName("SidebarScroll")
        sidebar_scroll.setFixedWidth(194)
        sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(sidebar_scroll)
        content = QWidget()
        body = column(content, 24)
        self.heading = label("Zadání", "Heading")
        self.subtitle = label("Připravte zadání, zkontrolujte model a spusťte práci.", "Subtitle")
        body.addWidget(self.heading)
        body.addWidget(self.subtitle)
        self.stack = PageStack()
        body.addWidget(self.stack, 1)
        self.runs_button = button("Aktivní běhy: 0", self.show_runs)
        body.addWidget(self.runs_button)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)
        self.files_panel = FilesPanel(settings, self.api_key)
        self.vector_panel = VectorStoresPanel(settings, self.api_key)
        self.cascade_panel = CascadePanel(settings, self._current_model_list)
        self.batch_panel = BatchPanel(settings, self.api_key)
        self.git_panel = GitHubPanel(settings)
        self.history_panel = ResponseRequestPanel(settings.log_dir)
        self.settings_page = SettingsPage(settings, self.api_key)
        self.txt_log = editor(readonly=True)
        self.txt_log.setMaximumBlockCount(2000)
        self.tab_run = self._workbench()
        resources = QTabWidget()
        resources.addTab(self.files_panel, "Soubory API")
        resources.addTab(self.vector_panel, "Vektorová úložiště")
        history = QTabWidget()
        history.addTab(self.history_panel, "Historie požadavků")
        history.addTab(self.txt_log, "Provozní log")
        self.models_page = self._models_page()
        help_page = QWidget()
        help_layout = column(help_page, 12)
        help_layout.addWidget(label("Jak pracovat", "Heading"))
        help_layout.addWidget(
            editor(
                "1. Nastavte API klíč a obnovte katalog modelů.\n2. V Zadání vyberte cíl, model a vstupy.\n3. Spusťte připravené zadání.\n4. Průběh lze skrýt a otevřít přes Aktivní běhy.\n5. Odeslaný BATCH převezměte tlačítkem Dokončit v Historii nebo Dávkách.\n\nGENERATE vytvoří plán, strukturu a soubory. MODIFY upravuje existující IN. QA vrací textovou odpověď. QFILE zapisuje souborový výstup. KASKADA spouští uloženou posloupnost kroků.\n\nVýchozí model nastavíte u volby modelu nebo v Nastavení. Použije se pro nové zadání; rozpracované zadání zachová svůj model.\n\nReRun v Historii obnoví uložené zadání, naváže na poslední odpověď a přeskočí pouze doložené soubory, které skutečně existují v OUT.\n\nPevná matice a lokální validace ověřují parametry podle modelu a LIVE/BATCH. Před skutečným pracovním požadavkem se neposílá žádná samostatná placená zkouška ani zkušební BATCH.",
                True,
            )
        )
        for key, title, description, page in (
            (
                "run",
                "Zadání",
                "Od zadání k výsledku.",
                self.tab_run,
            ),
            (
                "cascade",
                "Kaskády",
                "Posloupnosti kroků s vlastním modelem, vstupem a výstupem.",
                self.cascade_panel,
            ),
            ("resources", "Zdroje", "Spravujte soubory a úložiště připojené k zadání.", resources),
            (
                "batch",
                "Dávky",
                "Sledujte BATCH, stáhněte výsledky a řešte neúplné soubory.",
                self.batch_panel,
            ),
            (
                "history",
                "Historie",
                "Požadavky, odpovědi, dokončení BATCH a pokračování běhů.",
                history,
            ),
            (
                "versions",
                "Verze projektu",
                "Git, milníky, synchronizace a soubory projektu.",
                self.git_panel,
            ),
            ("models", "Modely", "Katalog účtu a pevná validační matice.", self.models_page),
            (
                "settings",
                "Nastavení",
                "Přístup, provoz, bezpečnost a oznámení.",
                self.settings_page,
            ),
            ("help", "Nápověda", "Postup práce a vysvětlení pojmů.", help_page),
        ):
            displayed = page if key == "run" else scroll(page)
            self.pages[key] = (displayed, title, description)
            self.stack.addWidget(displayed)
            nav_button = button(title, lambda checked=False, target=key: self.select_page(target))
            nav_button.setCheckable(True)
            nav.addWidget(nav_button)
            self.navigation[key] = nav_button
        self.batch_panel.layout().addWidget(
            button("Samostatné okno dávek", lambda: self.open_section("batch"))
        )
        self.settings_page.layout().addWidget(
            button("Nastavení v samostatném okně", lambda: self.open_section("settings"))
        )
        nav.addStretch()
        nav.addWidget(button("Zavřít aplikaci", self.on_exit))
        nav.addWidget(label("LIVE / BATCH\nPevná matice API"))
        self.files_panel.attached_changed.connect(self._update_attached_summary)
        self.vector_panel.attached_changed.connect(self._update_attached_summary)
        self.ed_out.textChanged.connect(self.batch_panel.set_out_dir)
        self.history_panel.rerun.connect(self.rerun)
        self.history_panel.complete_batch.connect(self.batch_panel.complete_run)
        self.batch_panel.run_guard = self._guard_batch_run
        self.batch_panel.runs_changed.connect(self.history_panel.refresh_runs)
        self.batch_panel.operation_changed.connect(
            lambda: self.history_panel.set_batch_busy(bool(self.batch_panel._operation_task))
        )
        self.settings_page.key_changed.connect(self._apply_api_key_change)
        self.settings_page.saved.connect(self.on_settings_saved)
        for panel in (
            self.files_panel,
            self.vector_panel,
            self.batch_panel,
            self.git_panel,
        ):
            panel.logline.connect(self.log)
        self.select_page("run")
        self._apply_saved_ssh()
        self._update_attached_summary()
        self.on_mode_changed()
        if settings.default_model:
            self._set_active_model(settings.default_model)
        self._update_model_info()
        if self.api_key:
            QTimer.singleShot(0, self._refresh_models_best_effort)
        incomplete = find_last_incomplete_run(settings.log_dir)
        if incomplete:
            state = read_state(Path(settings.log_dir) / incomplete)
            self.log(("Dávku lze dokončit tlačítkem Dokončit v Historii: " if batch_ids(state)
                      else "Nedokončený běh lze obnovit v Historii: ") + incomplete)

    def select_page(self, key):
        page, title, description = self.pages[key]
        self.stack.setCurrentWidget(page)
        self.heading.setText(title)
        self.subtitle.setText(description)
        for name, nav in self.navigation.items():
            nav.setChecked(name == key)

    def choose_model(self):
        from .windows import ModelPicker

        picker = ModelPicker(self.all_models, self.caps_cache, self)
        if picker.exec() == picker.Accepted:
            self._set_active_model(picker.selected_model)

    def open_section(self, key):
        from .windows import DetachedPageDialog

        owner, title, _ = self.pages[key]
        if owner.widget() is None:
            return
        dialog = DetachedPageDialog(title, owner, self)
        launchers = [
            action
            for action in dialog.page.findChildren(QPushButton)
            if "samostatné" in action.text()
        ]
        for action in launchers:
            action.hide()
        dialog.finished.connect(lambda: [action.show() for action in launchers])
        dialog.show()

    def _workbench(self):
        page = QWidget()
        layout = column(page)
        self.run_sections = QTabWidget()
        layout.addWidget(self.run_sections, 1)
        primary = QWidget()
        pl = column(primary, 6)
        main, ml = card("Co chcete vytvořit?")
        fields = form(ml)
        self.ed_project = text(placeholder="Název projektu")
        self.cb_mode = combo(["GENERATE", "MODIFY", "QA", "QFILE", "KASKADA"])
        self.cb_model = combo()
        self.cb_model.setPlaceholderText("Obnovte katalog modelů")
        self.chk_send_as_c = QCheckBox("Odeslat jako BATCH")
        fields.addRow("Projekt", self.ed_project)
        fields.addRow("Cíl práce", row(self.cb_mode, self.chk_send_as_c))
        fields.addRow("Model", row(self.cb_model, button("Vybrat model…", self.choose_model)))
        self.btn_default_model = button("Nastavit jako výchozí", self._set_current_default_model)
        self.lbl_default_model = label("", "Hint")
        fields.addRow("Nová zadání", row(self.btn_default_model, self.lbl_default_model))
        self.cb_run_cascade = combo()
        self.cb_run_cascade.setPlaceholderText("Vyberte uloženou kaskádu")
        self.row_cascade_selector = row(
            label("Uložená kaskáda"),
            self.cb_run_cascade,
            button("Obnovit", self.refresh_run_cascades),
        )
        ml.addWidget(self.row_cascade_selector)
        self.txt_prompt = editor(height=170)
        self.txt_prompt.setPlaceholderText("Popište požadovaný výsledek, vstupy a podmínky…")
        ml.addWidget(self.txt_prompt)
        self.lbl_caps = label("", "Hint")
        ml.addWidget(self.lbl_caps)
        pl.addWidget(main)
        summary, sl = card("Před spuštěním")
        self.attachment_summary = label()
        sl.addWidget(self.attachment_summary)
        sl.addWidget(
            row(
                button("Spravovat zdroje", lambda: self.select_page("resources")),
            )
        )
        pl.addWidget(summary)
        pl.addStretch()
        self.run_sections.addTab(scroll(primary), "Zadání")
        params = QWidget()
        ql = column(params, 16)
        fields = form(ql)
        self.ed_in, self.ed_out = text(), text()
        fields.addRow(
            "Vstupní složka IN",
            row(self.ed_in, button("Vybrat", lambda: self._browse_dir(self.ed_in))),
        )
        fields.addRow(
            "Výstupní složka OUT",
            row(self.ed_out, button("Vybrat", lambda: self._browse_dir(self.ed_out))),
        )
        self.chk_in_eq_out = QCheckBox("IN = OUT")
        self.chk_versing = QCheckBox("Snapshot před prvním zápisem")
        fields.addRow(row(self.chk_in_eq_out, self.chk_versing))
        self.sp_temp = number(self.s.default_temperature, maximum=2, decimal=True)
        fields.addRow("Teplota (temperature)", self.sp_temp)
        self.ed_response_id = text(placeholder="resp_…")
        fields.addRow("Navázat na odpověď", self.ed_response_id)
        self.cb_model_a1, self.cb_model_a2, self.cb_model_a3 = combo(), combo(), combo()
        overrides, ol = card(
            "Modely přípravy a generování",
            "Prázdná volba znamená hlavní model. GENERATE BATCH: A1/A2 jsou LIVE, A3 je dávka.",
        )
        of = form(ol)
        for name, field in (
            ("A1 · Plán", self.cb_model_a1),
            ("A2 · Struktura", self.cb_model_a2),
            ("A3 · Soubory", self.cb_model_a3),
        ):
            field.addItem(self.GENERATE_MODEL_MAIN_OPTION)
            of.addRow(name, field)
        self.row_generate_models = overrides
        ql.addWidget(overrides)
        self.ed_rerun = text(placeholder="RUN_ID pro pokračování")
        ql.addWidget(row(self.ed_rerun, button("Pokračovat (ReRun)", self.on_rerun)))
        ql.addStretch()
        self.run_sections.addTab(scroll(params), "Složky a parametry")
        diagnostic = QWidget()
        dl = column(diagnostic, 16)
        self.chk_diag_win_in = QCheckBox("Shromáždit diagnostiku Windows ze vstupu")
        self.chk_diag_win_out = QCheckBox("Nabídnout spuštění opravného skriptu Windows")
        self.chk_diag_ssh_in = QCheckBox("Shromáždit diagnostiku přes SSH")
        self.chk_diag_ssh_out = QCheckBox("Nabídnout spuštění opravného skriptu SSH")
        for field in (
            self.chk_diag_win_in,
            self.chk_diag_win_out,
            self.chk_diag_ssh_in,
            self.chk_diag_ssh_out,
        ):
            dl.addWidget(field)
        df = form(dl)
        self.ed_ssh_user, self.ed_ssh_host, self.ed_ssh_key, self.ed_ssh_pwd = (
            text(),
            text(),
            text(),
            text(),
        )
        self.ed_ssh_pwd.setEchoMode(QLineEdit.Password)
        self.chk_ssh_pin_required = QCheckBox("Vyžadovat pin KAJOVO_SSH_HOSTKEY_SHA256")
        for name, field in (
            ("SSH uživatel", self.ed_ssh_user),
            ("SSH host", self.ed_ssh_host),
            ("Privátní klíč", row(self.ed_ssh_key, button("Vybrat", self._browse_ssh_key))),
            ("Heslo klíče", self.ed_ssh_pwd),
        ):
            df.addRow(name, field)
        dl.addWidget(self.chk_ssh_pin_required)
        dl.addWidget(button("Uložit SSH", self._save_ssh_settings))
        dl.addStretch()
        self.run_sections.addTab(scroll(diagnostic), "Diagnostika")
        self.txt_response_view = editor(readonly=True)
        self.txt_response_view.setPlaceholderText("Výsledek dokončeného běhu se zobrazí zde.")
        self.run_sections.addTab(self.txt_response_view, "Výsledek")
        self.btn_go = button("Spustit práci", self.on_go, "Primary")
        self.btn_stop = button("Zastavit", self.on_stop, "Danger")
        self.btn_stop.setEnabled(False)
        self.btn_new = button("Nové", self.on_new)
        self.btn_save_state = button("Uložit zadání", self.on_save_state)
        self.btn_load_state = button("Načíst zadání", self.on_load_state)
        layout.addWidget(
            row(self.btn_go, self.btn_stop, self.btn_new, self.btn_save_state, self.btn_load_state)
        )
        self.cb_mode.currentTextChanged.connect(self.on_mode_changed)
        self.chk_send_as_c.toggled.connect(self.on_mode_changed)
        self.cb_model.currentTextChanged.connect(self.on_model_changed)
        self.chk_in_eq_out.toggled.connect(self.on_in_eq_out_changed)
        self.ed_in.textChanged.connect(self.on_in_eq_out_changed)
        return page

    def _models_page(self):
        page = QWidget()
        layout = column(page)
        self.ed_model_search_tab = text(placeholder="Hledat model podle názvu")
        layout.addWidget(
            row(
                self.ed_model_search_tab,
                button("Obnovit katalog účtu", self._refresh_models_best_effort),
            )
        )
        self.model_filters = {}
        filters = []
        for key, title in (
            ("supports_previous_response_id", "Návaznost"),
            ("supports_temperature", "Teplota"),
            ("supports_file_search", "Hledání"),
            ("supports_vector_store", "Úložiště"),
        ):
            field = QCheckBox(title)
            self.model_filters[key] = field
            field.toggled.connect(self._refresh_model_tab)
            filters.append(field)
        layout.addWidget(row(*filters))
        self.lst_models = QListWidget()
        layout.addWidget(self.lst_models, 1)
        self.lbl_model_info = label("", "Hint")
        layout.addWidget(self.lbl_model_info)
        layout.addWidget(
            row(
                button("Použít v zadání", self._apply_selected_model, "Primary"),
                button("Nastavit jako výchozí", self._set_default_model),
                button("Podrobná matice", self.on_model_matrix),
            )
        )
        self.ed_model_search_tab.textChanged.connect(self._refresh_model_tab)
        self.lst_models.currentItemChanged.connect(self._update_model_info)
        return page

    def _current_model_list(self):
        return [model for model in self.all_models if selectable(model)]

    def _refresh_models_best_effort(self):
        if not self.api_key:
            self.log(
                "Katalog účtu vyžaduje API klíč. Pevná matice je dostupná bez něj."
            )
            return
        key = self.api_key

        def done(records):
            if key != self.api_key:
                return
            self.all_models = sorted({record["id"] for record in records if record.get("id")})
            selected = self.cb_model.currentText() or self.s.default_model
            self.cb_model.clear()
            self.cb_model.addItems(self._current_model_list())
            if selected:
                self._set_active_model(selected)
            self._refresh_generate_model_overrides()
            self.cascade_panel.refresh_models()
            self.settings_page.refresh_models(self.all_models)
            self._refresh_model_tab()

        self.jobs.start(
            "Načítání katalogu modelů", lambda job: OpenAIClient(key).list_models(), done
        )

    def _set_active_model(self, model):
        if model and self.cb_model.findText(model) < 0:
            self.cb_model.addItem(model)
            item = self.cb_model.model().item(self.cb_model.count() - 1)
            item.setEnabled(False)
            item.setToolTip("Model nebyl nalezen v katalogu účtu.")
        self.cb_model.setCurrentText(model)
        self.on_model_changed()

    def _get_generate_model_override(self, field):
        return "" if field.currentText() == self.GENERATE_MODEL_MAIN_OPTION else field.currentText()

    def _set_generate_model_override(self, field, model):
        target = model or self.GENERATE_MODEL_MAIN_OPTION
        if field.findText(target) < 0:
            field.addItem(target)
            field.model().item(field.count() - 1).setEnabled(False)
        field.setCurrentText(target)

    def _refresh_generate_model_overrides(self):
        for field in (self.cb_model_a1, self.cb_model_a2, self.cb_model_a3):
            selected = self._get_generate_model_override(field)
            field.clear()
            field.addItems([self.GENERATE_MODEL_MAIN_OPTION, *self._current_model_list()])
            self._set_generate_model_override(field, selected)

    def on_model_changed(self, *_):
        if hasattr(self, "lbl_model_info"):
            self._update_model_info()
        model = self.cb_model.currentText()
        caps = self.caps_cache.get(model)
        allowed = bool(caps and caps.supports_temperature and not uses_reasoning_defaults(model))
        self.sp_temp.setEnabled(allowed)
        self.sp_temp.setToolTip("Teplota se odešle pouze při podpoře modelu.")
        self.lbl_caps.setText(
            (
                "Model podporuje: "
                + ", ".join(
                    title
                    for field, title in (
                        ("supports_previous_response_id", "návaznost"),
                        ("supports_file_search", "hledání v souborech"),
                        ("supports_temperature", "teplotu"),
                    )
                    if getattr(caps, field, False)
                )
            )
            if caps
            else "Model není v pevné matici; spuštění bude zablokováno."
        )

    def on_mode_changed(self, *_):
        mode = self.cb_mode.currentText()
        self.chk_send_as_c.setEnabled(mode in ("GENERATE", "MODIFY"))
        if mode not in ("GENERATE", "MODIFY"):
            self.chk_send_as_c.setChecked(False)
        batch = self.chk_send_as_c.isChecked()
        self.row_cascade_selector.setVisible(mode == "KASKADA")
        self.row_generate_models.setVisible(mode == "GENERATE")
        self.row_generate_models.setEnabled(mode == "GENERATE")
        self.ed_response_id.setEnabled(mode != "KASKADA" and (not batch or mode == "GENERATE"))
        for field in (
            self.chk_diag_win_in,
            self.chk_diag_win_out,
            self.chk_diag_ssh_in,
            self.chk_diag_ssh_out,
        ):
            allowed = mode != "KASKADA" and (
                not batch
                or mode == "GENERATE"
                and field in (self.chk_diag_win_in, self.chk_diag_ssh_in)
            )
            field.setEnabled(allowed)
            if not allowed:
                field.setChecked(False)
        if mode == "KASKADA":
            self.refresh_run_cascades()
        self.on_model_changed()

    def refresh_run_cascades(self):
        selected = self.cb_run_cascade.currentText()
        self.cb_run_cascade.clear()
        self.cb_run_cascade.addItems(self.cascade_panel.available_cascades())
        self.cb_run_cascade.setCurrentText(selected)

    def _refresh_model_tab(self):
        selected = self.lst_models.currentItem()
        selected_id = selected.data(Qt.UserRole) if selected else None
        self.lst_models.clear()
        for model in self.all_models:
            if self.ed_model_search_tab.text().lower() not in model.lower():
                continue
            caps = self.caps_cache.get(model)
            if any(
                field.isChecked() and not getattr(caps, key, False)
                for key, field in self.model_filters.items()
            ):
                continue
            item = QListWidgetItem(
                model + (" · výchozí" if model == self.s.default_model else "")
                + ("" if selectable(model) else " · není určen pro tento program")
            )
            item.setData(Qt.UserRole, model)
            self.lst_models.addItem(item)
            if model == selected_id:
                self.lst_models.setCurrentItem(item)
        self._update_model_info()

    def _update_model_info(self, *_):
        item = self.lst_models.currentItem()
        model = item.data(Qt.UserRole) if item else ""
        self.lbl_default_model.setText("Výchozí: " + (self.s.default_model or "nenastaven"))
        self.btn_default_model.setEnabled(self.cb_model.currentText() in self.all_models
                                         and selectable(self.cb_model.currentText()))
        self.lbl_model_info.setText(
            f"Vybraný model: {model or 'žádný'} · Výchozí: {self.s.default_model or 'nenastaven'}"
        )

    def _apply_selected_model(self):
        item = self.lst_models.currentItem()
        if item:
            self._set_active_model(item.data(Qt.UserRole))
            self.select_page("run")

    def _set_default_model(self):
        item = self.lst_models.currentItem()
        if item:
            self.settings_page.save_default_model(item.data(Qt.UserRole))

    def _set_current_default_model(self):
        self.settings_page.save_default_model(self.cb_model.currentText())

    def on_model_matrix(self):
        item = self.lst_models.currentItem()
        model = item.data(Qt.UserRole) if item else self.cb_model.currentText()
        try:
            spec = model_spec(model)
            msg_info(
                self,
                "Validační matice " + matrix_version(),
                f"Model {model}\nLIVE Responses: {spec['responses']}\nBATCH: {spec['batch']}\nSampling: {spec['sampling']}\nReasoning: {spec['reasoning']}\nMaximum výstupu: {spec['max_output_tokens']}\nZdroj: {spec['source']}",
                spec,
            )
        except ValueError as exc:
            msg_warning(self, "Validační matice", str(exc))

    def _update_attached_summary(self, *_):
        files, stores = self.files_panel.attached_ids(), self.vector_panel.attached_ids()
        self.attachment_summary.setText(
            f"Připojeno: {len(files)} souborů · {len(stores)} úložišť\n"
            + (", ".join(files + stores) or "Žádné přílohy.")
        )

    def _browse_dir(self, field):
        path = dialog_select_dir(self, "Vybrat adresář", field.text())
        if path:
            field.setText(path)

    def _browse_ssh_key(self):
        path, _ = dialog_open_file(self, "Privátní SSH klíč")
        if path:
            self.ed_ssh_key.setText(path)

    def on_in_eq_out_changed(self, *_):
        linked = self.chk_in_eq_out.isChecked()
        self.ed_out.setReadOnly(linked)
        if linked:
            self.ed_out.setText(self.ed_in.text())

    def _apply_saved_ssh(self):
        for field, key in (
            (self.ed_ssh_user, "user"),
            (self.ed_ssh_host, "host"),
            (self.ed_ssh_key, "key"),
        ):
            field.setText(getattr(self.s.ssh, key))
        self.ed_ssh_pwd.setText(get_secret("ssh_password") or "")
        self.chk_ssh_pin_required.setChecked(self.s.ssh.pin_required)

    def _save_ssh_settings(self):
        if not self.ed_ssh_user.text().strip() or not self.ed_ssh_host.text().strip():
            msg_warning(self, "SSH", "Vyplňte uživatele a host.")
            return
        for field, key in (
            (self.ed_ssh_user, "user"),
            (self.ed_ssh_host, "host"),
            (self.ed_ssh_key, "key"),
            (self.ed_ssh_pwd, "password"),
        ):
            setattr(self.s.ssh, key, field.text())
        self.s.ssh.pin_required = self.chk_ssh_pin_required.isChecked()
        try:
            save_settings(self.s, DEFAULT_SETTINGS_FILE)
            msg_info(self, "SSH", "Nastavení uloženo.")
        except Exception as exc:
            msg_warning(self, "SSH", str(exc))

    def _apply_api_key_change(self, key):
        self.api_key = key
        self.settings_page.api_key.setText(key)
        os.environ["OPENAI_API_KEY"] = key
        self.caps_cache.bind(key)
        self.all_models = []
        self.settings_page.refresh_models([])
        self._refresh_model_tab()
        for panel in (self.files_panel, self.vector_panel, self.batch_panel):
            panel.set_api_key(key)
        self._refresh_models_best_effort()

    def on_settings_saved(self):
        self.sp_temp.setValue(self.s.default_temperature)
        self._refresh_model_tab()

    def log(self, message):
        line = time.strftime("%H:%M:%S") + " · " + str(message)
        self.txt_log.appendPlainText(line)
        with open(Path(self.s.log_dir) / "ui_session.log", "a", encoding="utf-8") as stream:
            stream.write(line + "\n")

    def _config(self):
        mode = self.cb_mode.currentText()
        model = self.cb_model.currentText()
        caps = self.caps_cache.get(model)
        files = self.files_panel.attached_ids()
        return UiRunConfig(
            project=self.ed_project.text().strip(),
            prompt=self.txt_prompt.toPlainText(),
            mode=mode,
            send_as_c=self.chk_send_as_c.isChecked(),
            model=model,
            model_a1=self._get_generate_model_override(self.cb_model_a1)
            if mode == "GENERATE"
            else "",
            model_a2=self._get_generate_model_override(self.cb_model_a2)
            if mode == "GENERATE"
            else "",
            model_a3=self._get_generate_model_override(self.cb_model_a3)
            if mode == "GENERATE"
            else "",
            response_id=self.ed_response_id.text().strip()
            if self.ed_response_id.isEnabled()
            else "",
            attached_file_ids=files,
            input_file_ids=files,
            attached_vector_store_ids=self.vector_panel.attached_ids(),
            in_dir=self.ed_in.text().strip(),
            out_dir=self.ed_out.text().strip(),
            in_equals_out=self.chk_in_eq_out.isChecked(),
            versing=self.chk_versing.isChecked(),
            temperature=self.sp_temp.value() if self.sp_temp.isEnabled() else 0.0,
            use_file_search=True,
            diag_windows_in=self.chk_diag_win_in.isChecked(),
            diag_windows_out=self.chk_diag_win_out.isChecked(),
            diag_ssh_in=self.chk_diag_ssh_in.isChecked(),
            diag_ssh_out=self.chk_diag_ssh_out.isChecked(),
            ssh_user=self.ed_ssh_user.text().strip(),
            ssh_host=self.ed_ssh_host.text().strip(),
            ssh_key=self.ed_ssh_key.text().strip(),
            ssh_password=self.ed_ssh_pwd.text(),
            skip_paths=list(self.skip_paths_current),
            skip_exts=list(self.skip_exts_default),
            model_caps=caps.to_dict() if caps else {},
            resume_files=list(self._resume_files),
            resume_prev_id=self._resume_prev_id,
            ssh_pin=os.environ.get("KAJOVO_SSH_HOSTKEY_SHA256", ""),
            ssh_pin_required=self.chk_ssh_pin_required.isChecked(),
            caps_by_model={
                value: self.caps_cache.get(value).to_dict()
                for value in self.all_models
                if self.caps_cache.get(value)
            },
            available_models=list(self.all_models),
        )

    def _validate_paths(self, mode, batch):
        if (
            mode == "MODIFY"
            and not batch
            and (not self.ed_in.text().strip() or not Path(self.ed_in.text()).is_dir())
        ):
            raise ValueError("MODIFY vyžaduje existující vstupní adresář IN.")
        if mode in ("GENERATE", "MODIFY", "QFILE") and not self.ed_out.text().strip():
            raise ValueError("Vyberte výstupní adresář OUT.")

    @staticmethod
    def _out_overlaps(target, other):
        if not target or not other:
            return False
        paths = [os.path.normcase(os.path.realpath(value)) for value in (target, other)]
        try:
            return os.path.commonpath(paths) in paths
        except ValueError:
            return False

    def _guard_batch_run(self, run_id, target):
        for rid, context in self._run_contexts.items():
            cfg = context["worker"].cfg
            if rid == run_id or self._out_overlaps(target, cfg.out_dir):
                raise ValueError("Běh nebo jeho OUT právě používá aktivní práce. Vyčkejte na dokončení.")

    def on_go(self):
        try:
            if len(self._run_contexts) >= 4:
                raise ValueError("Mohou běžet nejvýše čtyři souběžné běhy.")
            if not self.api_key:
                raise ValueError("Nejdříve uložte API klíč v Nastavení.")
            cfg = self._config()
            self._validate_paths(cfg.mode, cfg.send_as_c)
            run_id = new_run_id()
            resume = getattr(self, "_resume_batch_state", None)
            if resume:
                run_id = resume["run_id"]
                if run_id in self._run_contexts:
                    raise ValueError("Tento běh již pokračuje.")
            logger = None
            if cfg.mode == "KASKADA":
                path = safe_join_under_root(
                    self.cascade_panel.cascade_dir, self.cb_run_cascade.currentText()
                )
                definition = CascadeDefinition.from_dict(
                    json.loads(Path(path).read_text(encoding="utf-8"))
                )
                if not definition.steps:
                    raise ValueError("Kaskáda nemá kroky.")
                if any(step.model not in self.all_models for step in definition.steps):
                    raise ValueError("Kaskáda obsahuje model nedostupný v katalogu účtu.")
                cfg = CascadeRunConfig(
                    cfg.project,
                    definition,
                    cfg.in_dir,
                    cfg.out_dir or definition.default_out_dir,
                    run_id,
                )
            else:
                validate_run_options(cfg)
            target = cfg.out_dir
            busy = self.batch_panel.busy_run_id
            if busy:
                state = read_state(Path(self.s.log_dir) / busy)
                if run_id == busy or self._out_overlaps(target, state.get("out_dir", "")):
                    raise ValueError("V tomto OUT právě probíhá operace BATCH. Vyčkejte na dokončení.")
            writes = (
                bool(target)
                and getattr(cfg, "mode", "") != "QA"
                and not getattr(cfg, "send_as_c", False)
            )
            if writes:
                destination = os.path.normcase(os.path.realpath(target))
                for context in self._run_contexts.values():
                    other = context["worker"].cfg
                    if (
                        not other.out_dir
                        or getattr(other, "send_as_c", False)
                        or getattr(other, "mode", "") == "QA"
                    ):
                        continue
                    old = os.path.normcase(os.path.realpath(other.out_dir))
                    try:
                        overlap = os.path.commonpath([destination, old]) in (destination, old)
                    except ValueError:
                        overlap = False
                    if overlap:
                        raise ValueError("Jiný běh již zapisuje do stejného nebo vnořeného OUT.")
            if isinstance(cfg, CascadeRunConfig):
                worker = CascadeRunWorker(cfg, self.s, self.api_key)
            else:
                logger = RunLogger(self.s.log_dir, run_id, project_name=cfg.project, resume=bool(resume))
                worker = RunWorker(cfg, self.s, self.api_key, logger)
                if resume:
                    worker.resume_generate_batch = resume["generate_batch"]
            dialog = ProgressDialog(self)
            dialog.setWindowTitle("Průběh · " + run_id)
            if getattr(cfg, "send_as_c", False):
                dialog.chk_bzz.setText("Upozornit po odeslání dávky")
            self._run_contexts[run_id] = dict(
                worker=worker, dialog=dialog, run_logger=logger, run_id=run_id
            )
            self._active_run_key = run_id
            self.worker, self.progress_dialog, self.run_logger = worker, dialog, logger
            worker.progress_event.connect(dialog.on_progress_event)
            worker.status.connect(dialog.set_status)
            worker.logline.connect(dialog.add_log)
            worker.logline.connect(self.log)
            dialog.btn_stop.clicked.connect(lambda: self.on_stop(run_key=run_id))
            worker.finished_ok.connect(lambda result: self.on_run_ok(run_id, result))
            worker.finished_err.connect(lambda error: self.on_run_err(run_id, error))
            worker.finished.connect(lambda: self._release_run(run_id))
            dialog.show()
            self.runs_button.setText(f"Aktivní běhy: {len(self._run_contexts)} · otevřít průběh")
            self.btn_stop.setEnabled(True)
            self._refresh_batch_links()
            worker.start()
            self._resume_files, self._resume_prev_id = [], None
        except Exception as exc:
            msg_warning(self, "Běh nelze spustit", str(exc))

    def on_stop(self, checked=False, force=False, run_key=None):
        key = run_key or self._active_run_key
        context = self._run_contexts.get(key)
        if context and (
            force
            or msg_question(
                self,
                "Zastavit běh?",
                "Rozpracovaný krok může zůstat nedokončený. Již provedená volání se účtují.",
            )
            == QMessageBox.Yes
        ):
            context["worker"].request_stop()
            context["dialog"].set_status("Ruším; čekám na dokončení probíhající operace.")
            context["dialog"].btn_stop.setEnabled(False)

    def _release_run(self, key):
        context = self._run_contexts.pop(key, None)
        if context:
            context["worker"].deleteLater()
        self._active_run_key = next(iter(self._run_contexts), None)
        self.worker = self._run_contexts.get(self._active_run_key, {}).get("worker")
        self.runs_button.setText(f"Aktivní běhy: {len(self._run_contexts)}")
        self.btn_stop.setEnabled(bool(self._run_contexts))
        self._refresh_batch_links()
        if not self._run_contexts and self._pending_replacement is not None:
            action, self._pending_replacement = self._pending_replacement, None
            action()

    def _refresh_batch_links(self):
        busy = set(self._run_contexts)
        self.batch_panel.active_runs = busy
        self.history_panel.active_runs = busy
        self.history_panel.refresh_runs()
        self.batch_panel.render_records()

    def show_runs(self):
        if not self._run_contexts:
            msg_info(
                self,
                "Aktivní běhy",
                "Žádný běh právě neprobíhá. Dokončené výsledky jsou v Historii.",
            )
        for context in self._run_contexts.values():
            context["dialog"].show()
            context["dialog"].raise_()

    def on_run_ok(self, key, result):
        context = self._run_contexts.get(key)
        if not context:
            return
        cfg = context["worker"].cfg
        batch = bool(result.get("batch_id") or result.get("mode") == "C")
        terminal_status = "batch_pending" if batch else str(result.get("status") or "completed")
        if terminal_status not in ("completed", "partial", "batch_pending"):
            terminal_status = "completed"
        terminal_detail = ""
        if terminal_status == "partial":
            missing = [str(path) for path in result.get("missing_deliverables", []) if path]
            terminal_detail = "Běh skončil s částečným výstupem."
            if missing:
                terminal_detail += " Nedodané soubory: " + ", ".join(missing[:20])
                if len(missing) > 20:
                    terminal_detail += f" … a dalších {len(missing) - 20}."
        context["dialog"].on_progress_event(
            ProgressEvent("RUN", terminal_status, detail=terminal_detail)
        )
        self.txt_response_view.setPlainText(
            str(
                result.get("text")
                or json.dumps(result.get("contract", result), ensure_ascii=False, indent=2)
            )
        )
        continuation_id = result.get("last_response_id") or result.get("response_id")
        if continuation_id:
            self.ed_response_id.setText(continuation_id)
        if batch:
            msg_info(
                self,
                "Dávka odeslána",
                f"ID: {result.get('batch_id', 'viz historie')}\nV Dávkách použijte Obnovit stav; tím zahájíte periodické sledování. Po dokončení použijte Dokončit.",
            )
        else:
            if terminal_status == "partial":
                missing = [str(path) for path in result.get("missing_deliverables", []) if path]
                text = "GENERATE skončil s částečným výstupem. Nedodané položky jsou evidovány v MISSINGFILES.md."
                if missing:
                    text += "\nNedodáno: " + ", ".join(missing[:20])
                    if len(missing) > 20:
                        text += f" … a dalších {len(missing) - 20}."
                msg_info(self, "Částečný výstup", text, details=result)
            if terminal_status == "completed":
                for attr, remote in (("diag_windows_out", False), ("diag_ssh_out", True)):
                    if getattr(cfg, attr, False):
                        self._maybe_execute_repair(cfg.out_dir, cfg, remote)
            if (
                cfg.out_dir
                and Path(cfg.out_dir).is_dir()
                and msg_question(self, "Otevřít výstup?", cfg.out_dir) == QMessageBox.Yes
            ):
                QDesktopServices.openUrl(QUrl.fromLocalFile(cfg.out_dir))
        if context["dialog"].chk_bzz.isChecked():
            from ..core.notifications import send_smtp_notification
            if batch:
                subject = "Kájovo NG · dávka odeslána"
                body = (
                    f"Běh {key}\nProjekt {cfg.project}\nDávka {result.get('batch_id', '')} byla odeslána. "
                    "Vzdálené zpracování ani import do OUT tím nejsou dokončeny."
                )
                title = "Oznámení o odeslání dávky"
            else:
                repair_requested = bool(getattr(cfg, "diag_windows_out", False) or getattr(cfg, "diag_ssh_out", False))
                if terminal_status == "partial":
                    missing = [str(path) for path in result.get("missing_deliverables", []) if path]
                    subject = "Kájovo NG · částečný výstup"
                    body = f"Běh {key}\nProjekt {cfg.project}\nOUT {cfg.out_dir}\nVýstup není úplný."
                    if missing:
                        body += "\nNedodáno: " + ", ".join(missing[:20])
                    title = "Oznámení o částečném výstupu"
                else:
                    subject = "Kájovo NG · generování dokončeno" if repair_requested else "Kájovo NG · dokončeno"
                    body = f"Běh {key}\nProjekt {cfg.project}\nOUT {cfg.out_dir}"
                    if repair_requested:
                        body += "\nGenerování skončilo; zvolená opravná operace je samostatný potvrzovaný child Job a má vlastní výsledek/log."
                    title = "Oznámení o dokončení"
            self.jobs.start(
                title,
                lambda job: send_smtp_notification(self.s.smtp, subject, body),
                lambda result: self.log(result[1]),
                popup=False,
            )
        self.history_panel.refresh_runs()
        self.batch_panel.render_records()

    def on_run_err(self, key, error):
        context = self._run_contexts.get(key)
        if context:
            context["dialog"].on_progress_event(
                ProgressEvent(
                    "RUN",
                    "cancelled" if error in ("STOPPED", "STOP_REQUESTED") else "failed",
                    detail=error,
                )
            )
        msg_critical(self, "Běh skončil chybou", error)

    def _gather_state(self):
        state = asdict(self._config())
        state["ssh_password"] = ""
        state["temperature"] = self.sp_temp.value()
        state["git"] = self.git_panel.get_state()
        state["settings"] = self.settings_page.get_state()
        state["cascade_file"] = self.cb_run_cascade.currentText()
        return state

    def _apply_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("Uložené zadání musí být objekt JSON.")
        for key, field in (
            ("project", self.ed_project),
            ("in_dir", self.ed_in),
            ("out_dir", self.ed_out),
            ("response_id", self.ed_response_id),
        ):
            field.setText(str(state.get(key, "")))
        self.txt_prompt.setPlainText(str(state.get("prompt", "")))
        self.cb_mode.setCurrentText(state.get("mode", "GENERATE"))
        self.chk_send_as_c.setChecked(bool(state.get("send_as_c")))
        self._set_active_model(state.get("model", ""))
        for key, field in (
            ("model_a1", self.cb_model_a1),
            ("model_a2", self.cb_model_a2),
            ("model_a3", self.cb_model_a3),
        ):
            self._set_generate_model_override(field, state.get(key, ""))
        self.chk_in_eq_out.setChecked(bool(state.get("in_equals_out")))
        self.chk_versing.setChecked(bool(state.get("versing")))
        self.sp_temp.setValue(float(state.get("temperature", self.s.default_temperature)))
        self.files_panel.set_attached(state.get("attached_file_ids", []))
        self.vector_panel.set_attached(state.get("attached_vector_store_ids", []))
        for key, old, field in (
            ("diag_windows_in", "win_in", self.chk_diag_win_in),
            ("diag_windows_out", "win_out", self.chk_diag_win_out),
            ("diag_ssh_in", "ssh_in", self.chk_diag_ssh_in),
            ("diag_ssh_out", "ssh_out", self.chk_diag_ssh_out),
        ):
            field.setChecked(bool(state.get(key, state.get("diag", {}).get(old))))
        for key, old, field in (
            ("ssh_user", "user", self.ed_ssh_user),
            ("ssh_host", "host", self.ed_ssh_host),
            ("ssh_key", "key", self.ed_ssh_key),
        ):
            field.setText(str(state.get(key, state.get("ssh", {}).get(old, ""))))
        self.chk_ssh_pin_required.setChecked(
            bool(state.get("ssh_pin_required", state.get("ssh", {}).get("pin_required")))
        )
        self.git_panel.apply_state(state.get("git", {}))
        self.settings_page.apply_state(state.get("settings", {}))
        self.refresh_run_cascades()
        self.cb_run_cascade.setCurrentText(state.get("cascade_file", ""))
        self.on_mode_changed()
        self.select_page("run")

    def on_save_state(self):
        path, _ = dialog_save_file(self, "Uložit zadání", "kajovo_state.json", "JSON (*.json)")
        if path:
            try:
                atomic_write_text(
                    path, json.dumps(self._gather_state(), ensure_ascii=False, indent=2)
                )
            except Exception as exc:
                msg_warning(self, "Uložení zadání", str(exc))

    def on_load_state(self):
        path, _ = dialog_open_file(self, "Načíst zadání", filters="JSON (*.json)")
        if not path:
            return
        try:
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            msg_warning(self, "Načtení zadání", str(exc))
            return
        def apply_loaded():
            try:
                self._apply_state(state)
            except Exception as exc:
                msg_warning(self, "Načtení zadání", str(exc))
        if self._queue_replacement(apply_loaded):
            return
        apply_loaded()

    def on_new(self):
        def apply_new():
            self._apply_state({"model": self.s.default_model})
            self._resume_files, self._resume_prev_id = [], None
            self.skip_paths_current = []
            self.txt_response_view.clear()
        if self._queue_replacement(apply_new):
            return
        apply_new()

    def _confirm_replace(self):
        """Kompatibilní dotaz; vlastní odloženou náhradu řeší _queue_replacement."""
        if not self._run_contexts:
            return True
        return msg_question(
            self, "Nahradit zadání?",
            "Probíhá běh. Nejprve požádám všechny běhy o zastavení a zadání změním až po jejich skutečném ukončení."
        ) == QMessageBox.Yes

    def _queue_replacement(self, action):
        if not self._run_contexts:
            return False
        if not self._confirm_replace():
            return True
        self._pending_replacement = action
        for key in list(self._run_contexts):
            self.on_stop(force=True, run_key=key)
        self.log(f"Čekám na zastavení {len(self._run_contexts)} běhů před změnou zadání.")
        return True

    def on_exit(self):
        if (
            msg_question(
                self,
                "Ukončit aplikaci?",
                "Neuložené zadání se ztratí. Probíhající operace musejí nejprve dokončit zastavení.",
            )
            == QMessageBox.Yes
        ):
            self.close()

    def _gather_completed_paths(self, run_id, out_dir):
        run_dir = Path(self.s.log_dir) / run_id
        return sorted({entry["path"] for entry in verified_output_evidence(run_dir, out_dir)})

    def on_rerun(self):
        self.rerun(self.ed_rerun.text().strip())

    def rerun(self, run_id):
        try:
            if not RUN_ID_RE.fullmatch(run_id):
                raise ValueError("Neplatný RUN ID.")
            if run_id in self.batch_panel.active_runs:
                raise ValueError("Běh nebo jeho související pracovní dávku právě zpracovává aktivní práce.")
            from .recovery import read_record
            state = read_record(Path(self.s.log_dir) / run_id / "run_state.json")
            if batch_ids(state):
                self.batch_panel.complete_run(run_id)
                return
            if state.get("preflight_batches"):
                raise ValueError(
                    "Tento historický běh obsahuje pouze odstraněné preflight podklady. "
                    "Automatické odeslání z nich je zakázáno; spusťte nové zadání."
                )
            if self.batch_panel.busy_run_id == run_id:
                raise ValueError("Tento běh již zpracovává dávku.")
            if state.get("generate_batch"):
                if run_id in self._run_contexts:
                    raise ValueError("Tento běh již pokračuje.")
                if state.get("submission_unknown"):
                    raise ValueError("Výsledek odeslání není známý. Nejprve dohledávejte dávku v panelu Dávky.")
            ui, previous, structure = recover_run(self.s.log_dir, run_id)
            self._apply_state(ui)
            self.skip_paths_current = self._gather_completed_paths(run_id, ui.get("out_dir", ""))
            self._resume_files, self._resume_prev_id = structure, previous
            self.ed_response_id.setText(previous or "")
            self._resume_batch_state = state if state.get("generate_batch") else None
            try:
                self.on_go()
            finally:
                self._resume_batch_state = None
        except Exception as exc:
            msg_warning(self, "ReRun", str(exc))

    def _maybe_execute_repair(self, out_dir, cfg, remote=False):
        import hashlib
        import subprocess

        readme = Path(safe_join_under_root(out_dir, "readmerepair.txt"))
        if not readme.is_file():
            self.log("Opravný skript: chybí readmerepair.txt.")
            return
        names = (
            ["run_this_script_repairme_kajovo.sh", "RUN_THIS_SCRIPT_REPAIRME_KAJOVO.sh"]
            if remote
            else [
                "RUN_THIS_SCRIPT_REPAIRME_KAJOVO_WINDOWS.bat",
                "run_this_script_repairme_kajovo_windows.bat",
            ]
        )
        script = next(
            (
                Path(safe_join_under_root(out_dir, name))
                for name in names
                if Path(safe_join_under_root(out_dir, name)).is_file()
            ),
            None,
        )
        if not script:
            msg_warning(self, "Oprava", "Opravný skript nebyl nalezen.")
            return
        content = script.read_bytes()
        description = readme.read_text(encoding="utf-8", errors="replace")
        target = cfg.ssh_user + "@" + cfg.ssh_host if remote else "místní Windows"
        if (
            msg_question(
                self,
                "Spustit opravný skript?",
                f"Nedůvěryhodný skript: {script.name}\nCíl: {target}\nSHA256: {hashlib.sha256(content).hexdigest()}\n\n{description}",
            )
            != QMessageBox.Yes
        ):
            return

        def execute(job):
            if remote:
                from ..core.diagnostics.ssh import execute_ssh_repair

                result = execute_ssh_repair(content, cfg)
            elif os.name == "nt":
                result = subprocess.run(
                    ["cmd", "/c", str(script)],
                    cwd=out_dir,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                raise ValueError("Oprava Windows vyžaduje systém Windows.")
            path = safe_join_under_root(
                out_dir, "_repair_ssh_exec_log.txt" if remote else "_repair_exec_log.txt"
            )
            atomic_write_text(
                path,
                description
                + f"\nRETURN_CODE: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
            )
            return f"Návratový kód: {result.returncode}\nLog: {path}"

        self.jobs.start(
            "Spuštění opravy", execute, lambda result: msg_info(self, "Výsledek opravy", result)
        )

    def closeEvent(self, event):
        jobs = [
            job for manager in self.findChildren(Jobs) for job in manager.active if job.isRunning()
        ]
        workers = [
            context["worker"]
            for context in self._run_contexts.values()
            if context["worker"].isRunning()
        ]
        if jobs or workers:
            for worker in [*jobs, *workers]:
                worker.request_stop()
            self.log("Čekám na dokončení aktivních operací. Poté okno zavřete znovu.")
            event.ignore()
            return
        event.accept()
