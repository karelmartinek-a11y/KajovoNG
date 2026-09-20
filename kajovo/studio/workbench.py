"""Příprava zadání a explicitní předání ověřeného snímku backendu."""

from __future__ import annotations

import copy
import json
from dataclasses import fields
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QPlainTextEdit, QTabWidget, QWidget,
)

from kajovo.core.model_capabilities import ModelCapabilitiesCache
from kajovo.core.runs.config import UiRunConfig
from kajovo.studio.workers.run_worker import RunWorker
from kajovo.core.request_rules import validate_run_options
from kajovo.core.runlog import RunLogger
from kajovo.core.utils import atomic_write_text, new_run_id, validate_relative_path
from .components import Form, PathInput, action, actions, caption, panel, scroll, vertical
from .evidence import EvidenceView
from .components import DetailDialog


MODES = [("Vytvořit projekt", "GENERATE"), ("Upravit projekt", "MODIFY"),
         ("Odpovědět na dotaz", "QA"), ("Vytvořit jeden soubor", "QFILE")]


def default_state(settings):
    text = (
        "project", "prompt", "response_id", "in_dir", "out_dir",
        "model_a1", "model_a2", "model_a3", "ssh_pin", "qfile_output_path",
    )
    flags = (
        "send_as_c", "in_equals_out", "versing", "maximum_quality",
        "stop_after_plan", "dry_run", "qfile_suggest_path", "qa_continue_conversation",
        "diag_windows_in", "diag_windows_out", "diag_ssh_in", "diag_ssh_out",
    )
    state = dict.fromkeys(text, "")
    state.update(dict.fromkeys(flags, False))
    state.update(model=settings.default_model, mode="GENERATE", temperature=settings.default_temperature,
                 auto_repair="off",
                 verification_profile_ids=[],
                 qfile_output_format="txt", qfile_plan=None,
                 attached_file_ids=[], input_file_ids=[], attached_vector_store_ids=[],
                 use_file_search=True, skip_paths=[], skip_exts=[], model_caps={},
                 ssh_user=settings.ssh.user, ssh_host=settings.ssh.host, ssh_key=settings.ssh.key,
                 ssh_password="", ssh_pin_required=settings.ssh.pin_required)
    return state


class Workbench(QWidget):
    result_ready = Signal(object)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.saved_extras = {}
        self.busy_outputs = {}
        self.pending_lineage = None
        root = vertical(self, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        page = QWidget()
        body = vertical(page)
        box, layout = panel("Co chcete vytvořit?")
        self.form = Form()
        self.form.text("project", "Projekt")
        self.form.choice("mode", "Cíl práce", MODES)
        self.form.choice("model", "Model", [])
        layout.addWidget(self.form)
        self.prompt = QPlainTextEdit()
        self.prompt.setObjectName("run.prompt")
        self.prompt.setAccessibleName("Zadání")
        self.prompt.setPlaceholderText("Popište požadovaný výsledek…")
        self.prompt.setMinimumHeight(220)
        layout.addWidget(self.prompt, 1)
        body.addWidget(box)
        self.attachments = caption("K zadání nejsou připojené zdroje.", "muted")
        body.addWidget(self.attachments)
        self.validation = caption("Vyplňte projekt a zadání.", "muted")
        body.addWidget(self.validation)
        body.addStretch()
        self.tabs.addTab(scroll(page), "Zadání")
        parameters = QWidget()
        layout = vertical(parameters)
        self.options = Form()
        for key, title in (("in_dir", "Vstupní adresář"), ("out_dir", "Výstupní adresář")):
            field = self.options.add(key, title, PathInput(directories=True))
            self.options.body.addRow("", action("run.browse." + key, "Vybrat adresář", lambda checked=False, target=field: self.browse(target)))
        for key, title in (("in_equals_out", "Zapisovat do vstupního adresáře"),
                           ("versing", "Pořídit snímek souborů"),
                           ("send_as_c", "Souborové úlohy zpracovat dávkově"),
                           ("maximum_quality", "Maximální propracovanost"),
                           ("stop_after_plan", "Zastavit po ověřené přípravě plánu"),
                           ("dry_run", "MODIFY bez publikace do projektu (dry-run)")):
            self.options.check(key, title)
        temperature = QDoubleSpinBox()
        temperature.setRange(0, 2)
        temperature.setSingleStep(0.1)
        self.options.add("temperature", "Teplota modelu", temperature)
        self.options.text("response_id", "Identifikátor předchozí odpovědi")
        self.options.text("qfile_output_path", "QFILE · Výstupní cesta")
        self.options.choice("qfile_output_format", "QFILE · Výstupní formát", [
            (value.upper(), value) for value in (
                "txt", "md", "json", "toml", "yaml", "csv", "html",
                "css", "js", "ts", "py", "svg", "xml"
            )
        ])
        self.options.check("qfile_suggest_path", "QFILE · Navrhnout název samostatným plánovacím krokem")
        self.options.check("qa_continue_conversation", "QA · Pokračovat v rozhovoru přes previous_response_id")
        for key, title in (("model_a1", "Model plánování"), ("model_a2", "Model struktury"), ("model_a3", "Model souborů")):
            self.options.choice(key, title, [("Použít hlavní model", "")])
        layout.addWidget(self.options)
        layout.addStretch()
        self.tabs.addTab(scroll(parameters), "Parametry a adresáře")
        diagnostics = QWidget()
        layout = vertical(diagnostics)
        self.diagnostics = Form()
        for key, title in (("diag_windows_in", "Diagnostika Windows na vstupu"), ("diag_windows_out", "Diagnostika Windows na výstupu"),
                           ("diag_ssh_in", "Vzdálená diagnostika na vstupu"), ("diag_ssh_out", "Vzdálená diagnostika na výstupu")):
            self.diagnostics.check(key, title)
        for key, title in (("ssh_user", "Vzdálený uživatel"), ("ssh_host", "Vzdálený počítač"), ("ssh_key", "Soubor přihlašovacího klíče"),
                           ("ssh_password", "Heslo"), ("ssh_pin", "Očekávaný otisk klíče počítače")):
            self.diagnostics.text(key, title, secret=key == "ssh_password")
        self.diagnostics.check("ssh_pin_required", "Vyžadovat ověření otisku")
        layout.addWidget(self.diagnostics)
        layout.addStretch()
        self.tabs.addTab(scroll(diagnostics), "Diagnostika")
        self.result = EvidenceView("Výsledek práce")
        self.tabs.addTab(self.result, "Výsledek")
        self.start_button = action("run.start", "Spustit práci", self.start, "primary")
        root.addWidget(actions(self.start_button,
                               action("run.new", "Nové zadání", self.reset),
                               action("run.save", "Uložit zadání", self.save),
                               action("run.load", "Načíst zadání", self.load)))
        self.widgets = {**self.form.fields, **self.options.fields, **self.diagnostics.fields}
        for form in (self.form, self.options, self.diagnostics):
            form.changed.connect(self.validate)
        self.prompt.textChanged.connect(self.validate)
        context.models_changed.connect(self.refresh_models)
        context.attachments_changed.connect(self.update_attachments)
        context.key_changed.connect(self.validate)
        self.widgets["mode"].currentIndexChanged.connect(self.refresh_models)
        self.widgets["send_as_c"].toggled.connect(self.refresh_models)
        self.reset()
        self.refresh_models()

    def browse(self, field):
        value = QFileDialog.getExistingDirectory(self, "Vybrat adresář", field.text())
        if value:
            field.setText(value)

    def reset(self):
        self.pending_lineage = None
        self.apply_state(default_state(self.context.settings))
        self.result.clear()

    def apply_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("Zadání musí být objekt.")
        self.saved_extras = copy.deepcopy(state)
        for key, widget in self.widgets.items():
            value = state.get(key, default_state(self.context.settings).get(key))
            widget.blockSignals(True)
            if isinstance(widget, QCheckBox):
                widget.setChecked(value is True)
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value or 0))
            elif isinstance(widget, QComboBox):
                index = widget.findData(value)
                if index < 0 and value:
                    widget.addItem(str(value), value)
                    index = widget.count() - 1
                widget.setCurrentIndex(max(0, index))
            else:
                widget.setText(str(value or ""))
            widget.blockSignals(False)
        self.prompt.setPlainText(str(state.get("prompt", "")))
        self.context.files = list(state.get("attached_file_ids") or [])
        self.context.stores = list(state.get("attached_vector_store_ids") or [])
        self.context.attachments_changed.emit()
        self.validate()

    def state(self, secrets=False):
        state = copy.deepcopy(self.saved_extras)
        for key, widget in self.widgets.items():
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QDoubleSpinBox):
                value = widget.value()
            elif isinstance(widget, QComboBox):
                value = widget.currentData() or ""
            else:
                value = widget.text()
            state[key] = value
        state.update(prompt=self.prompt.toPlainText(), attached_file_ids=list(self.context.files),
                     input_file_ids=list(self.context.files), attached_vector_store_ids=list(self.context.stores))
        if not secrets:
            state["ssh_password"] = ""
        return state

    def config(self):
        state = {**default_state(self.context.settings), **self.state(secrets=True)}
        cache = ModelCapabilitiesCache("")
        state["available_models"] = list(self.context.models)
        state["caps_by_model"] = {model: cache.get(model).to_dict() for model in self.context.models if cache.get(model)}
        capabilities = cache.get(state["model"])
        state["model_caps"] = capabilities.to_dict() if capabilities else {}
        if state["mode"] != "GENERATE":
            for name in ("model_a1", "model_a2", "model_a3"):
                state[name] = ""
        return UiRunConfig(**{field.name: state[field.name] for field in fields(UiRunConfig) if field.name in state})

    def _model_usage(self, key):
        mode = self.widgets["mode"].currentData()
        batch = self.widgets["send_as_c"].isChecked()
        if key == "model":
            if mode == "GENERATE":
                return "generate_plan"
            if mode == "MODIFY":
                return "responses_batch" if batch else "responses"
            if mode == "QA":
                return "qa"
            return "qfile"
        if key == "model_a1":
            return "generate_plan"
        if key == "model_a2":
            return "generate_structure"
        return "generate_file_batch" if batch else "generate_file"

    def refresh_models(self, *_):
        if not hasattr(self, "widgets"):
            return
        for key in ("model", "model_a1", "model_a2", "model_a3"):
            widget = self.widgets[key]
            usage = self._model_usage(key)
            values = self.context.models_for_usage(usage)
            recommended = self.context.recommended_model(usage)
            widget.blockSignals(True)
            widget.clear()
            if key != "model":
                widget.addItem("Použít hlavní model", "")
            for model in values:
                widget.addItem(model, model)
            target = widget.findData(recommended) if recommended else -1
            if target < 0 and key != "model":
                target = 0
            widget.setCurrentIndex(target)
            widget.blockSignals(False)
        self.validate()

    def update_attachments(self):
        self.attachments.setText(f"Připojené soubory: {len(self.context.files)} · vyhledávací úložiště: {len(self.context.stores)}")
        self.validate()

    def validate(self):
        if not hasattr(self, "widgets"):
            return False
        mode = self.widgets["mode"].currentData()
        grouped = mode in {"GENERATE", "MODIFY"}
        for key in ("maximum_quality", "send_as_c", "stop_after_plan"):
            widget = self.widgets[key]
            widget.setEnabled(grouped)
            if not grouped:
                widget.blockSignals(True)
                widget.setChecked(False)
                widget.blockSignals(False)
        dry_run = self.widgets["dry_run"]
        dry_run.setEnabled(mode == "MODIFY")
        if mode != "MODIFY":
            dry_run.blockSignals(True)
            dry_run.setChecked(False)
            dry_run.blockSignals(False)
        qfile = mode == "QFILE"
        for key in ("qfile_output_path", "qfile_output_format", "qfile_suggest_path"):
            self.widgets[key].setEnabled(qfile)
        qa_continue = self.widgets["qa_continue_conversation"]
        qa_continue.setEnabled(mode == "QA")
        if mode != "QA":
            qa_continue.blockSignals(True)
            qa_continue.setChecked(False)
            qa_continue.blockSignals(False)
        linked = self.widgets["in_equals_out"].isChecked()
        self.widgets["out_dir"].setReadOnly(linked)
        if linked:
            target = self.widgets["in_dir"].text()
            if self.widgets["out_dir"].text() != target:
                self.widgets["out_dir"].setText(target)
        batch = self.widgets["send_as_c"].isChecked()
        for key in ("diag_windows_out", "diag_ssh_out"):
            self.widgets[key].setEnabled(not batch)
            if batch:
                self.widgets[key].blockSignals(True)
                self.widgets[key].setChecked(False)
                self.widgets[key].blockSignals(False)
        for key in ("model_a1", "model_a2", "model_a3"):
            self.widgets[key].setEnabled(mode == "GENERATE")
        capability = ModelCapabilitiesCache("").get(self.widgets["model"].currentData())
        self.widgets["temperature"].setEnabled(bool(capability and capability.supports_temperature))
        try:
            cfg = self.config()
            if not cfg.project.strip() or not cfg.prompt.strip():
                raise ValueError("Vyplňte projekt a zadání.")
            if mode == "MODIFY" and (not cfg.in_dir or not Path(cfg.in_dir).is_dir()):
                raise ValueError("Úprava projektu vyžaduje existující vstupní adresář.")
            if mode != "QA" and not cfg.out_dir.strip():
                raise ValueError("Vyberte výstupní adresář.")
            if mode == "QFILE":
                if cfg.qfile_output_path:
                    relative = validate_relative_path(cfg.qfile_output_path)
                    suffix = Path(relative).suffix.lower().lstrip(".")
                    if suffix != cfg.qfile_output_format:
                        raise ValueError(
                            "QFILE: přípona výstupní cesty musí odpovídat zvolenému formátu."
                        )
                elif not cfg.qfile_suggest_path:
                    raise ValueError(
                        "QFILE: vyplňte výstupní cestu, nebo zvolte Navrhnout název."
                    )
            if not self.context.api_key:
                raise ValueError("Uložte přístupový klíč v Nastavení.")
            validate_run_options(cfg)
        except (ValueError, TypeError) as error:
            self.validation.setText(str(error))
            self.start_button.setEnabled(False)
            return False
        self.validation.setText("Zadání splňuje místní technickou a kontraktní kontrolu.")
        self.start_button.setEnabled(True)
        return True

    def start(self):
        if not self.validate():
            return
        cfg = self.config()
        if len(self.context.operations.active) >= 4:
            self.validation.setText("Počkejte na dokončení některé ze čtyř aktivních operací.")
            return
        target = (
            Path(cfg.out_dir).resolve()
            if cfg.out_dir and not cfg.send_as_c and cfg.mode != "QA"
            and not cfg.dry_run and not cfg.stop_after_plan
            else None
        )
        try:
            self.context.operations.assert_output_available(target)
        except ValueError as error:
            self.validation.setText(str(error))
            return
        if target and any(target == other or target in other.parents or other in target.parents for other in self.busy_outputs.values()):
            self.validation.setText("Do tohoto adresáře nebo jeho části již zapisuje jiná operace.")
            return
        run_id = new_run_id()
        logger = RunLogger(self.context.settings.log_dir, run_id, project_name=cfg.project)
        if self.pending_lineage:
            logger.record_lineage(**self.pending_lineage)
        settings = copy.deepcopy(self.context.settings)
        worker = RunWorker(cfg, settings, self.context.api_key, logger)
        if target:
            self.busy_outputs[run_id] = target

        def receive(value):
            self.result.setPlainText(json.dumps(value, ensure_ascii=False, indent=2, default=str))
            self.result_ready.emit(value)
            previous = value.get("last_response_id") or value.get("response_id")
            if previous:
                self.widgets["response_id"].setText(previous)
            if cfg.mode == "QA":
                # Provider historii nikdy nezapínáme automaticky; ID pouze zpřístupníme.
                self.widgets["qa_continue_conversation"].setChecked(False)
            if value.get("status") == "qfile_plan_ready":
                plan = value.get("qfile_plan") or {}
                self.saved_extras["qfile_plan"] = copy.deepcopy(plan)
                proposed = plan.get("proposed_path")
                fmt = plan.get("format")
                if proposed:
                    self.widgets["qfile_output_path"].setText(str(proposed))
                if fmt:
                    index = self.widgets["qfile_output_format"].findData(fmt)
                    if index >= 0:
                        self.widgets["qfile_output_format"].setCurrentIndex(index)
                self.widgets["qfile_suggest_path"].setChecked(False)
                self.validation.setText(
                    "QFILE: návrh cesty je připraven. Zkontrolujte jej a znovu klikněte "
                    "Spustit práci; tím cestu výslovně potvrdíte před výrobou souboru."
                )
            if (
                value.get("status") == "completed_unverified"
                and value.get("published_files")
                and not value.get("dry_run")
            ):
                self.offer_repair_from_publish(
                    str(Path(self.context.settings.log_dir).resolve() / run_id),
                    value,
                    cfg,
                )
            if record.dialog.notification.isChecked():
                from kajovo.core.notifications import send_smtp_notification
                from .operations import STATES
                state = "batch_pending" if value.get("batch_id") else value.get("status", "completed")
                message = f"Projekt: {cfg.project}\nBěh: {run_id}\nStav: {STATES.get(state, state)}"
                if state == "batch_pending":
                    message += "\nVzdálené zpracování a místní převzetí ještě nejsou potvrzené."
                self.context.operations.start("Odeslání oznámení o výsledku", lambda task: send_smtp_notification(settings.smtp, "Kájovo NG · výsledek operace", message, raise_errors=True))

        record = self.context.operations.adopt("Práce na projektu · " + cfg.project, worker, receive, identifier=run_id, output_dir=target)
        record.dialog.notification.show()
        worker.finished.connect(lambda: self.busy_outputs.pop(run_id, None))
        self.pending_lineage = None
        return record

    def offer_repair_from_publish(self, run_dir, state, cfg=None):
        from kajovo.core.repair_execution import claim_published_repair_offer

        if cfg is None:
            ui = state.get("ui_state") if isinstance(state, dict) else {}
            ui = ui if isinstance(ui, dict) else {}
            merged = {**default_state(self.context.settings), **ui}
            runtime = self.state(secrets=True)
            identity = ("ssh_user", "ssh_host", "ssh_key")
            if all(str(runtime.get(key) or "") == str(merged.get(key) or "") for key in identity):
                merged["ssh_password"] = str(runtime.get("ssh_password") or "")
            cfg = UiRunConfig(**{
                field.name: copy.deepcopy(merged.get(field.name))
                for field in fields(UiRunConfig)
            })
        for enabled, remote in (
            (bool(getattr(cfg, "diag_windows_out", False)), False),
            (bool(getattr(cfg, "diag_ssh_out", False)), True),
        ):
            if not enabled:
                continue
            artifact = claim_published_repair_offer(run_dir, state, remote)
            if artifact:
                self.offer_repair(
                    cfg, remote, expected_digest=artifact["sha256"]
                )

    def offer_repair(self, cfg, remote, expected_digest=""):
        from kajovo.core.repair_execution import execute_repair, prepare_repair
        from PySide6.QtWidgets import QDialog

        try:
            proposal = prepare_repair(
                cfg.out_dir, remote, expected_digest=expected_digest
            )
        except (ValueError, OSError) as error:
            self.validation.setText(str(error))
            return
        target = cfg.ssh_user + "@" + cfg.ssh_host if remote else "Tento počítač Windows"
        details = {"cíl": target, "soubor": str(proposal.script), "otisk SHA-256": proposal.digest,
                   "popis": proposal.description, "obsah skriptu": proposal.content.decode("utf-8", errors="replace")}
        dialog = DetailDialog("Spustit připravenou opravu", "Opravný skript může změnit cílový počítač: " + target + ". Zkontrolujte obsah a účel v podrobnostech.", self, details, confirm=True)
        if dialog.exec() == QDialog.Accepted:
            self.context.operations.start("Provedení potvrzené opravy", lambda task: execute_repair(proposal, cfg),
                                          self.result.set_value, output_dir=cfg.out_dir)

    def save(self):
        path, _ = QFileDialog.getSaveFileName(self, "Uložit zadání", "zadani.json", "Zadání (*.json)")
        if path:
            try:
                atomic_write_text(path, json.dumps(self.state(), ensure_ascii=False, indent=2))
            except OSError as error:
                self.validation.setText(str(error))

    def load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Načíst zadání", "", "Zadání (*.json)")
        if path:
            try:
                loaded = json.loads(Path(path).read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("Zadání musí být objekt.")
                self.reset()
                self.apply_state(loaded)
            except (OSError, ValueError, TypeError) as error:
                self.validation.setText(str(error))
