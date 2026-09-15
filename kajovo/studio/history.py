"""Run Studio: virtuální časové stopy nad kanonickou evidencí Historie."""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QMenu, QWidget

from kajovo.core.batch_completion import complete_saved_batch, pending_batch_ids, read_state
from kajovo.core.run_bundle import HistoryIndex
from kajovo.core.utils import safe_join_under_root

from .components import action, actions, caption, panel, vertical
from .history_artifacts import ArtifactGuard
from .history_composer import BranchComposer
from .history_details import RunDetailDialog
from .history_data import HistoryData, checked_checkpoints, payload
from .history_launcher import HistoryBranchLauncher
from .history_models import RunTableModel, RunView, build_run
from .history_policy import ActionAvailabilityPolicy, apply_decision
from .history_timeline import RunTrackView
from .resources import ValueDialog


def _payload(adapter):
    return payload(adapter)


class HistoryPage(QWidget):
    """Orchestrátor; skenování i detailní čtení běží mimo GUI thread."""

    activate_workbench = Signal()
    activate_comic = Signal(str)
    activate_batch = Signal(str)

    def __init__(self, context, workbench, parent=None):
        super().__init__(parent)
        self.context, self.workbench = context, workbench
        self.model = RunTableModel(self)
        self.policy = ActionAvailabilityPolicy()
        self.data = HistoryData()
        self.launcher = HistoryBranchLauncher(context, self.refresh)
        self.records, self.reverse_lineage = [], {}
        self.adapter, self.payload, self.run, self.selected_step = None, {}, None, None
        self.generation, self.zoom = 0, 1.0

        root = vertical(self, 0)
        root.addWidget(caption("Run Studio · průběh, výsledky a nové větve", "muted"))
        root.addWidget(self._filter_bar())
        root.addWidget(self._timeline_bar())
        self.tracks = RunTrackView()
        self.tracks.setModel(self.model)
        self.tracks.selectionModel().currentRowChanged.connect(self._row_selected)
        self.tracks.stage_selected.connect(self._stage_selected)
        self.tracks.run_activated.connect(lambda _run: self.open_detail())
        root.addWidget(self.tracks, 1)

        inspector, body = panel("Vybraný běh a fáze")
        self.phase = caption("Vyberte běh nebo jeho fázi.")
        self.phase.setAccessibleName("Vybraná fáze")
        body.addWidget(self.phase)
        self.notice = caption("Vyberte fázi na časové ose. Výsledky a dostupné akce se zobrazí zde.", "muted")
        body.addWidget(self.notice)
        self.selected_files = caption("", "muted")
        body.addWidget(self.selected_files)
        self.buttons = {
            "continue": action("history.continue", "Pokračovat", lambda: self.branch("continue")),
            "rerun": action("history.rerun", "Znovu spustit", lambda: self.branch("rerun")),
            "repair": action("history.repair", "Opravit", lambda: self.branch("repair")),
            "edit_branch": action("history.qa.edit_branch", "Upravit QA a spustit novou větev",
                                  lambda: self.branch("rerun", edit_input=True)),
            "clone": action("history.clone", "Klonovat jako nové zadání", self.clone),
            "complete_batch": action("history.batch.complete", "Převzít soubory", self.complete_batch, "primary"),
            "open_batch": action("history.batch.open", "Otevřít v Dávkách", self.open_batch),
            "clone_artifact": action("history.clone.artifact", "Nové zadání s vybraným souborem", self.choose_reusable_clone),
            "detail": action("history.detail", "Detail běhu", self.open_detail),
            "integrity": action("history.integrity", "Ověřit integritu", self.verify),
            "bundle_open": action("history.bundle.open", "Otevřít složku běhu", self.open_bundle),
            "bundle_export": action("history.bundle.export", "Exportovat záznam běhu", self.export_bundle),
            "comic": action("history.comic", "Otevřít komiks", self.open_comic),
            "parent": action("history.lineage.parent", "Přejít na zdrojový běh", self.open_parent),
        }
        self.more = action("history.more", "Další možnosti ▾", self.show_more)
        self.more.setEnabled(False)
        self.secondary = {name: self.buttons[name] for name in (
            "edit_branch", "clone", "open_batch", "clone_artifact", "integrity",
            "bundle_open", "bundle_export", "comic", "parent")}
        for button in self.secondary.values():
            button.setParent(self)
            button.hide()
        body.addWidget(actions(self.buttons["detail"], self.buttons["continue"], self.buttons["rerun"],
                               self.buttons["repair"], self.buttons["complete_batch"], self.more))
        root.addWidget(inspector)
        for name, button in self.buttons.items():
            button.setEnabled(False)
            button.setVisible(name == "detail")

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.apply_filters)

    def show_more(self):
        menu = QMenu(self)
        for name, button in self.secondary.items():
            decision = getattr(self, "decisions", {}).get(name)
            if decision and decision.visible:
                item = menu.addAction(button.text(), button.click)
                item.setEnabled(decision.enabled)
                item.setToolTip(decision.reason)
        menu.setToolTipsVisible(True)
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def _filter_bar(self):
        box, layout = QWidget(), QHBoxLayout()
        box.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Hledat projekt, Run ID, Response ID, soubor…")
        self.search.setAccessibleName("Fulltext historie")
        self.project_filter = QLineEdit()
        self.project_filter.setPlaceholderText("Projekt")
        self.mode_filter, self.status_filter, self.transport_filter = QComboBox(), QComboBox(), QComboBox()
        self.model_filter = QLineEdit()
        self.model_filter.setPlaceholderText("Model")
        for combo, label, values in (
            (self.mode_filter, "Druh běhu", ["GENERATE", "MODIFY", "QA", "QFILE", "KASKADA", "COMIC"]),
            (self.status_filter, "Stav", ["completed", "partial", "failed", "running", "batch_pending", "ready_to_import", "dry_run", "unknown"]),
            (self.transport_filter, "LIVE/BATCH", ["LIVE", "BATCH"]),
        ):
            combo.setAccessibleName(label)
            combo.addItem(label, "")
            for value in values:
                from .history_state import present_state
                combo.addItem(present_state(value).label if combo is self.status_filter else value, value)
        for widget, stretch in ((self.search, 3), (self.project_filter, 1), (self.mode_filter, 1),
                                (self.status_filter, 1)):
            layout.addWidget(widget, stretch)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(lambda *_: self.filter_timer.start())
        self.transport_filter.currentIndexChanged.connect(lambda *_: self.filter_timer.start())
        self.model_filter.textChanged.connect(lambda *_: self.filter_timer.start())
        layout.addWidget(action("history.refresh", "Obnovit", self.refresh))
        return box

    def _timeline_bar(self):
        box, layout = QWidget(), QHBoxLayout()
        box.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        self.date_from, self.date_to = QLineEdit(), QLineEdit()
        self.date_from.setPlaceholderText("Od DD.MM.RRRR")
        self.date_to.setPlaceholderText("Do DD.MM.RRRR")
        self.only_errors = QCheckBox("Jen chybové")
        self.flag_batch = QCheckBox("BATCH")
        self.flag_checkpoint = QCheckBox("Bod obnovy")
        self.flag_output = QCheckBox("Výstup")
        self.flag_lineage = QCheckBox("Větve")
        self.filter_dialog = QDialog(self)
        self.filter_dialog.setWindowTitle("Filtry historie")
        self.filter_dialog.resize(480, 460)
        fields = QFormLayout(self.filter_dialog)
        for label, widget in (("Od data", self.date_from), ("Do data", self.date_to),
                              ("Zpracování", self.transport_filter), ("Model", self.model_filter),
                              ("", self.flag_batch), ("", self.flag_checkpoint),
                              ("", self.flag_output), ("", self.flag_lineage)):
            fields.addRow(label, widget)
        fields.addRow(action("history.filters.close", "Hotovo", self.filter_dialog.hide))
        for widget in (self.date_from, self.date_to, self.only_errors, self.flag_batch,
                       self.flag_checkpoint, self.flag_output, self.flag_lineage):
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.toggled
            signal.connect(lambda *_: self.filter_timer.start())
        layout.addWidget(self.only_errors)
        layout.addWidget(action("history.filters.advanced", "Další filtry…", self.filter_dialog.show))
        layout.addWidget(action("history.filters.reset", "Zrušit filtry", self.reset_filters))
        layout.addStretch()
        self.zoom_label = caption("Zoom 100 %", "muted")
        layout.addWidget(self.zoom_label)
        layout.addWidget(action("history.zoom.out", "−", lambda: self.set_zoom(self.zoom / 1.2)))
        layout.addWidget(action("history.zoom.in", "+", lambda: self.set_zoom(self.zoom * 1.2)))
        layout.addWidget(action("history.zoom.fit", "Přizpůsobit", self.fit_tracks))
        return box

    def reset_filters(self):
        for widget in (self.search, self.project_filter, self.model_filter, self.date_from, self.date_to):
            widget.clear()
        for widget in (self.mode_filter, self.status_filter, self.transport_filter):
            widget.setCurrentIndex(0)
        for widget in (self.only_errors, self.flag_batch, self.flag_checkpoint, self.flag_output, self.flag_lineage):
            widget.setChecked(False)

    def fit_tracks(self):
        available = self.tracks.width() - 2 * self.tracks.frameWidth() - self.tracks.columnWidth(0)
        if self.model.rowCount() * self.tracks.verticalHeader().defaultSectionSize() > self.tracks.viewport().height():
            available -= self.tracks.verticalScrollBar().sizeHint().width()
        self.set_zoom(max(0.55, (available - 2) / 1100))

    def set_zoom(self, value):
        self.zoom = max(0.55, min(3.0, float(value)))
        self.tracks.set_zoom(self.zoom)
        self.zoom_label.setText(f"Zoom {self.zoom * 100:.0f} %")

    @staticmethod
    def _date_value(text: str, *, end=False):
        if not text.strip():
            return None
        value = datetime.strptime(text.strip(), "%d.%m.%Y")
        return value.timestamp() + (86399 if end else 0)

    def apply_filters(self):
        try:
            lower, upper = self._date_value(self.date_from.text()), self._date_value(self.date_to.text(), end=True)
            if lower is not None and upper is not None and lower > upper:
                raise ValueError
        except ValueError:
            self.notice.setText("Zadejte platný a správně seřazený interval DD.MM.RRRR.")
            return
        values = {
            "search": self.search.text(), "project": self.project_filter.text(),
            "mode": self.mode_filter.currentData(), "status": self.status_filter.currentData(),
            "transport": self.transport_filter.currentData(), "model": self.model_filter.text(),
            "date_from": lower, "date_to": upper, "errors": self.only_errors.isChecked(),
            "batch": self.flag_batch.isChecked(), "checkpoint": self.flag_checkpoint.isChecked(),
            "output": self.flag_output.isChecked(), "lineage": self.flag_lineage.isChecked(),
        }
        self.filter_generation = getattr(self, "filter_generation", 0) + 1
        generation = self.filter_generation
        runs = list(self.model._all)

        def receive(rows):
            if generation != self.filter_generation:
                return
            self.model.set_filtered(rows)
            self.notice.setText(f"Zobrazeno {len(rows)} z {len(self.records)} běhů.")
            target = getattr(self, "_focus_new_run", "")
            if target:
                for index, row in enumerate(rows):
                    if row.run_id == target:
                        self.tracks.selectRow(index)
                        self.tracks.scrollTo(self.model.index(index, 0))
                        self._focus_new_run = ""
                        break

        if len(runs) < 1000:
            receive(RunTableModel.filter_runs(runs, values))
        else:
            self.context.operations.start_read("Vyhledání v historii",
                lambda task: RunTableModel.filter_runs(runs, values), receive, popup=False)

    def refresh(self, *_):
        root = self.context.settings.log_dir

        def scan(task):
            index = HistoryIndex(root)
            records, reverse = index.refresh(), index.reverse_lineage()
            views = [build_run(record, steps=record.get("timeline_steps") or [], reverse_lineage=reverse)
                     for record in records]
            return records, reverse, views

        def receive(value):
            self.records, self.reverse_lineage, views = value
            self.model.set_runs(views)
            for mode in sorted({run.mode for run in views}):
                if self.mode_filter.findData(mode) < 0:
                    self.mode_filter.addItem(mode, mode)
            self.apply_filters()
            self.fit_tracks()

        self.context.operations.start_read("Obnovení Run Studia", scan, receive, popup=False, identifier="history.index")

    def _row_selected(self, current, _previous):
        run = self.model.run_at(current.row()) if current.isValid() else None
        if run:
            self.tracks.select_stage(run.run_id, "")
            self.load_run(run)
        elif hasattr(self, "buttons"):
            self.generation += 1
            self.run, self.adapter, self.payload, self.selected_step = None, None, {}, None
            self.phase.setText("Vyberte běh nebo jeho fázi.")
            self.selected_files.clear()
            self.more.setEnabled(False)
            for name, button in self.buttons.items():
                button.setEnabled(False)
                button.setVisible(name == "detail")

    def load_run(self, run: RunView):
        self.generation += 1
        generation = self.generation
        self.run, self.adapter, self.payload, self.selected_step = run, None, {}, None
        self.phase.setText(f"Vybraný běh: {run.mode} · {run.run_id} · načítám kanonickou evidenci…")
        self.more.setEnabled(False)
        for button in self.buttons.values():
            button.setEnabled(False)
        directory = safe_join_under_root(self.context.settings.log_dir, run.run_id)

        def read(task):
            adapter, payload, state = self.data.read(directory)
            full = build_run(payload["summary"], steps=payload["steps"], state=state,
                             reverse_lineage=self.reverse_lineage)
            return adapter, payload, state, full

        def receive(value):
            if generation != self.generation:
                return
            self.adapter, self.payload, self._state, self.run = value
            if self.selected_step:
                self.phase.setText(f"Vybraná fáze: {self.selected_step.get('stage')} · {self.run.project}")
            else:
                self.phase.setText(f"{self.run.mode} · {self.run.project} · {self.run.status.label}")
            self.notice.setText("Legacy evidence je pouze ke čtení; chybějící údaje jsou Není evidováno."
                                if self.adapter.legacy else "Vyberte Detail běhu pro zadání, výsledky a vysvětlení průběhu.")
            self.update_actions()
            self.describe_selection()
            if self.adapter.bundle and self.payload.get("checkpoints"):
                adapter = self.adapter
                checkpoints, artifacts = self.payload["checkpoints"], self.payload["artifacts"]

                def checked(rows):
                    if generation == self.generation:
                        self.payload = {**self.payload, "checkpoints": rows}
                        self.update_actions()

                self.context.operations.start_read("Ověření bodů obnovy",
                    lambda task: checked_checkpoints(adapter, checkpoints, artifacts), checked, popup=False)

        self.context.operations.start_read("Načtení detailu běhu", read, receive, popup=False)

    def _stage_selected(self, run, stage):
        if not self.run or run.run_id != self.run.run_id:
            self.load_run(run)
        self.selected_step = stage.technical
        self.tracks.select_stage(run.run_id, stage.step_id)
        self.phase.setText(f"Vybraná fáze: {stage.stage or 'Není evidováno'} · {stage.title}")
        self.update_actions()
        self.describe_selection()

    def describe_selection(self):
        if not self.run or not self.adapter:
            return
        identifier = (self.selected_step or {}).get("step_id")
        stage = next((row for row in self.run.stages if row.step_id == identifier), None)
        if stage:
            from .history_models import format_duration
            self.phase.setText(f"Vybraná fáze: {stage.title} · {stage.stage} · {stage.status.label}")
            parts = [value for value in (stage.model, stage.reasoning,
                format_duration(stage.duration) if stage.duration is not None else "Trvání fáze nebylo uloženo") if value]
            self.notice.setText(" · ".join(parts))
        records = [row for row in self.payload.get("artifacts") or []
                   if not identifier or row.get("step_id") == identifier]
        names = [str(row.get("display_name") or "Soubor") for row in records]
        error = (getattr(self, "_state", {}) or {}).get("human_error") or (getattr(self, "_state", {}) or {}).get("error")
        self.selected_files.setText(("Soubory: " + ", ".join(names[:4]) + (f" a dalších {len(names) - 4}" if len(names) > 4 else ""))
                                    if names else str(error)[:240] if error else "Soubory, odpovědi a podklady najdete v detailu běhu.")

    def update_actions(self):
        if not self.adapter or not self.run:
            return
        decisions = self.policy.evaluate(self.payload.get("summary") or {}, getattr(self, "_state", {}),
                                         self.payload.get("checkpoints") or [], legacy=self.adapter.legacy,
                                         selected_step=self.selected_step,
                                         artifacts=self.payload.get("artifacts") or [])
        for name, button in self.buttons.items():
            apply_decision(button, decisions[name])
            if name in self.secondary:
                button.hide()
        self.decisions = decisions
        self.more.setEnabled(True)

    def branch(self, relation, *, edit_input=False):
        if not self.adapter:
            return
        composer = BranchComposer(self.launcher, self.adapter, self.payload.get("checkpoints") or [], relation,
                                  str((self.selected_step or {}).get("stage") or ""), self, edit_input=edit_input)
        if composer.exec() != QDialog.Accepted or not composer.preview:
            return
        try:
            self.launcher.launch_async(self.adapter, composer.preview, composer.repair_instruction(),
                                       receive=lambda record: self.focus_new_branch(record.identifier))
            composer.confirm_button.setEnabled(False)
            self.notice.setText("Připravuji novou větev; průběh ověření a spuštění je v okně operace.")
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(str(error))

    def focus_new_branch(self, run_id):
        self._focus_new_run = run_id
        self.reset_filters()
        self.filter_timer.stop()
        self.refresh()

    def clone(self):
        if not self.adapter:
            return
        state = getattr(self, "_state", None) or read_state(self.adapter.root)
        self.clone_source(self.adapter, state)

    def clone_source(self, adapter, state):
        ui = state.get("ui_state")
        if not isinstance(ui, dict) or not ui:
            self.notice.setText("Běh nemá přesně uložený ui_state.")
            return
        cloned = copy.deepcopy(ui)
        for key in ("response_id", "resume_prev_id", "resume_files", "preparation_snapshot", "skip_paths",
                    "completed_hashes", "recovery_instruction", "source_checkpoint_id"):
            cloned.pop(key, None)
        self.workbench.reset()
        self.workbench.apply_state(cloned)
        self.workbench.widgets["response_id"].clear()
        self.workbench.pending_lineage = {"source_run_id": adapter.run_id, "relation_type": "clone"}
        self.activate_workbench.emit()

    def complete_batch(self):
        if not self.adapter:
            return
        state = getattr(self, "_state", {})
        self.complete_batch_source(self.adapter, state)

    def complete_batch_source(self, adapter, state):
        records = state.get("batch_records") if isinstance(state.get("batch_records"), dict) else {}
        pending = [identifier for identifier in pending_batch_ids(state)
                   if isinstance(records.get(identifier), dict) and records[identifier].get("status") == "completed"]
        if not pending:
            self.notice.setText("Žádná existující dávka nečeká na místní převzetí.")
            return
        identifier = pending[0]
        if len(pending) > 1:
            picker = ValueDialog("Převzít existující dávku", "Batch ID", identifier, self)
            if picker.exec() != QDialog.Accepted or picker.value not in pending:
                return
            identifier = picker.value
        root = str(adapter.root)
        try:
            client = self.context.client()
            output = Path(state["out_dir"]).resolve() if state.get("out_dir") else None
            self.context.operations.assert_output_available(output)
        except (ValueError, OSError) as error:
            self.notice.setText(str(error))
            return
        self.context.operations.start(
            "Převzetí výsledků dávky",
            lambda task: complete_saved_batch(client, root, identifier, self.context.settings,
                                              progress=task.progress_event.emit),
            lambda _value: self.refresh(), output_dir=output, identifier=f"batch.import:{identifier}",
        )

    def open_batch(self):
        pending = pending_batch_ids(getattr(self, "_state", {}))
        related = (self.payload.get("summary") or {}).get("related_batch_ids") or []
        identifier = (pending or related or [""])[0]
        if identifier:
            self.activate_batch.emit(identifier)

    def open_detail(self):
        if self.adapter and self.run:
            generation, directory = self.generation, self.adapter.root

            def receive(value):
                if generation != self.generation:
                    return
                adapter, payload, state = value
                RunDetailDialog(self.context, adapter, payload, state, self.run, self).exec()

            self.context.operations.start_read("Otevření výsledků běhu",
                lambda task: self.data.read(directory, detail=True), receive, popup=False)

    def verify(self):
        if not self.adapter:
            return
        adapter, generation = self.adapter, self.generation

        def receive(value):
            if generation == self.generation:
                self.notice.setText("Integrita: " + json.dumps(value, ensure_ascii=False))

        self.context.operations.start("Ověření integrity Run Bundle", lambda task: adapter.integrity(), receive)

    def open_bundle(self):
        if self.adapter:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.adapter.root)))

    def export_bundle(self):
        if not self.adapter:
            return
        destination, _ = QFileDialog.getSaveFileName(self, "Exportovat Run Bundle", self.adapter.run_id + ".zip", "ZIP (*.zip)")
        if not destination:
            return
        source, target = self.adapter.root.resolve(), Path(destination).resolve()
        if target == source or source in target.parents:
            self.notice.setText("Export nesmí přepsat zdrojový Run Bundle.")
            return

        def write(task):
            from .history_artifacts import export_run_bundle
            return export_run_bundle(adapter, target)

        adapter = self.adapter
        self.context.operations.start("Export Run Bundle", write,
                                      lambda value: self.notice.setText("Run Bundle exportován: " + value))

    def open_comic(self):
        if not self.adapter:
            return
        state = getattr(self, "_state", {})
        if state.get("mode") == "COMIC" and state.get("comic_operation_id"):
            self.activate_comic.emit(str(state["comic_operation_id"]))
        else:
            self.notice.setText("Vybraný běh nemá doloženou komiksovou operaci.")

    def open_parent(self):
        if not self.run or not self.run.parent_run_id:
            self.notice.setText("Parent běhu není evidován.")
            return
        for row in range(self.model.rowCount()):
            candidate = self.model.run_at(row)
            if candidate and candidate.run_id == self.run.parent_run_id:
                self.tracks.selectRow(row)
                self.tracks.scrollTo(self.model.index(row, 0))
                return
        self.open_related(self.run.parent_run_id)

    def open_related(self, identifier):
        directory = safe_join_under_root(self.context.settings.log_dir, identifier)

        def receive(value):
            adapter, payload, state = value
            run = build_run(payload["summary"], steps=payload["steps"], state=state,
                            events=payload.get("events") or [], reverse_lineage=self.reverse_lineage)
            RunDetailDialog(self.context, adapter, payload, state, run, self).exec()

        self.context.operations.start_read("Otevření souvisejícího běhu",
            lambda task: self.data.read(directory, detail=True), receive, popup=False)

    def clone_with_artifact(self, record: dict):
        """Sekundární clone varianta; reuse nikdy skrytě nepřepne do Zadání."""
        if not self.adapter or not record.get("reusable"):
            raise ValueError("Artefakt není označen jako reusable.")
        source = ArtifactGuard(self.adapter.root).resolve(record)
        staging = Path(self.context.settings.cache_dir).resolve() / "history_reuse" / uuid4().hex
        staging.mkdir(parents=True, exist_ok=False)
        shutil.copy2(source, staging / source.name)
        self.clone()
        self.workbench.widgets["in_dir"].setText(str(staging))
        self.workbench.pending_lineage = {"source_run_id": self.adapter.run_id, "relation_type": "clone",
                                          "inherited_artifact_ids": [record.get("artifact_id")]}

    def choose_reusable_clone(self):
        records = [row for row in self.payload.get("artifacts") or [] if row.get("reusable")]
        if not records:
            self.notice.setText("Běh nemá hashově ověřitelný reusable artefakt.")
            return
        picker = ValueDialog("Klonovat s artefaktem", "Artifact ID", str(records[0].get("artifact_id") or ""), self)
        if picker.exec() != QDialog.Accepted:
            return
        record = next((row for row in records if row.get("artifact_id") == picker.value), None)
        if not record:
            self.notice.setText("Zvolený artefakt není součástí běhu.")
            return
        try:
            self.clone_with_artifact(record)
        except (ValueError, OSError) as error:
            self.notice.setText(str(error))
