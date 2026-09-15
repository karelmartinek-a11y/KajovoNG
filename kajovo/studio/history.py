"""Run Studio: virtuální časové stopy nad kanonickou evidencí Historie."""

from __future__ import annotations

import copy
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QWidget

from kajovo.core.batch_completion import complete_saved_batch, pending_batch_ids, read_state
from kajovo.core.run_bundle import HistoryIndex, LegacyRunAdapter
from kajovo.core.utils import safe_join_under_root

from .components import action, actions, caption, panel, vertical
from .history_artifacts import ArtifactGuard
from .history_composer import BranchComposer
from .history_details import RunDetailDialog
from .history_launcher import HistoryBranchLauncher
from .history_models import RunTableModel, RunView, build_run
from .history_policy import ActionAvailabilityPolicy, apply_decision
from .history_timeline import RunTrackView
from .resources import ValueDialog


def _payload(adapter: LegacyRunAdapter) -> dict:
    return {
        "summary": adapter.run_record(), "steps": adapter.steps(), "requests": adapter.requests(),
        "responses": adapter.responses(), "artifacts": adapter.artifacts(),
        "validations": adapter.validations(), "events": adapter.events(),
        "lineage": adapter.lineage(), "checkpoints": adapter.checkpoints(),
        "integrity": adapter.integrity(),
    }


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
        self.launcher = HistoryBranchLauncher(context, self.refresh)
        self.records, self.reverse_lineage = [], {}
        self.adapter, self.payload, self.run, self.selected_step = None, {}, None, None
        self.generation, self.zoom = 0, 1.0

        root = vertical(self, 0)
        root.addWidget(caption("Run Studio", "heading"))
        root.addWidget(caption("Historické běhy jako vícestopá časová osa · zdrojová evidence je neměnná.", "muted"))
        root.addWidget(self._filter_bar())
        root.addWidget(self._timeline_bar())
        self.tracks = RunTrackView()
        self.tracks.setModel(self.model)
        self.tracks.selectionModel().currentRowChanged.connect(self._row_selected)
        self.tracks.stage_selected.connect(self._stage_selected)
        self.tracks.run_activated.connect(lambda _run: self.open_detail())
        root.addWidget(self.tracks, 1)

        inspector, body = panel("Kontext vybrané fáze")
        self.phase = caption("Vyberte běh nebo jeho fázi.")
        self.phase.setAccessibleName("Vybraná fáze")
        body.addWidget(self.phase)
        self.notice = caption("Procházení ani náhled první placené operace neposílají síťový požadavek.", "muted")
        body.addWidget(self.notice)
        self.buttons = {
            "continue": action("history.continue", "Pokračovat", lambda: self.branch("continue")),
            "rerun": action("history.rerun", "Znovu spustit", lambda: self.branch("rerun")),
            "repair": action("history.repair", "Opravit", lambda: self.branch("repair")),
            "edit_branch": action("history.qa.edit_branch", "Upravit QA a spustit novou větev",
                                  lambda: self.branch("rerun", edit_input=True)),
            "clone": action("history.clone", "Klonovat jako nové zadání", self.clone),
            "complete_batch": action("history.batch.complete", "Převzít soubory", self.complete_batch, "primary"),
            "open_batch": action("history.batch.open", "Otevřít v Dávkách", self.open_batch),
            "clone_artifact": action("history.clone.artifact", "Klonovat s reusable souborem", self.choose_reusable_clone),
        }
        body.addWidget(actions(self.buttons["continue"], self.buttons["rerun"], self.buttons["repair"],
                               self.buttons["edit_branch"], self.buttons["clone"]))
        body.addWidget(actions(
            self.buttons["complete_batch"], self.buttons["open_batch"],
            action("history.detail", "Detail běhu", self.open_detail),
            action("history.integrity", "Ověřit integritu", self.verify),
            action("history.bundle.open", "Otevřít Run Bundle", self.open_bundle),
            action("history.bundle.export", "Exportovat Run Bundle", self.export_bundle),
            action("history.comic", "Otevřít komiks", self.open_comic),
            action("history.lineage.parent", "Přejít na parent", self.open_parent),
            self.buttons["clone_artifact"],
        ))
        inspector.setMaximumHeight(245)
        root.addWidget(inspector)
        for button in self.buttons.values():
            button.setEnabled(False)

        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(self.apply_filters)

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
                combo.addItem(value, value)
        for widget, stretch in ((self.search, 3), (self.project_filter, 1), (self.mode_filter, 1),
                                (self.status_filter, 1), (self.transport_filter, 1), (self.model_filter, 1)):
            layout.addWidget(widget, stretch)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.currentIndexChanged
            signal.connect(lambda *_: self.filter_timer.start())
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
        self.flag_checkpoint = QCheckBox("Checkpoint")
        self.flag_output = QCheckBox("Výstup")
        self.flag_lineage = QCheckBox("Lineage")
        for widget in (self.date_from, self.date_to, self.only_errors, self.flag_batch,
                       self.flag_checkpoint, self.flag_output, self.flag_lineage):
            layout.addWidget(widget)
            signal = widget.textChanged if isinstance(widget, QLineEdit) else widget.toggled
            signal.connect(lambda *_: self.filter_timer.start())
        layout.addStretch()
        self.zoom_label = caption("Zoom 100 %", "muted")
        layout.addWidget(self.zoom_label)
        layout.addWidget(action("history.zoom.out", "−", lambda: self.set_zoom(self.zoom / 1.2)))
        layout.addWidget(action("history.zoom.in", "+", lambda: self.set_zoom(self.zoom * 1.2)))
        layout.addWidget(action("history.zoom.fit", "Přizpůsobit", lambda: self.set_zoom(0.7)))
        return box

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
        self.model.apply_filters({
            "search": self.search.text(), "project": self.project_filter.text(),
            "mode": self.mode_filter.currentData(), "status": self.status_filter.currentData(),
            "transport": self.transport_filter.currentData(), "model": self.model_filter.text(),
            "date_from": lower, "date_to": upper, "errors": self.only_errors.isChecked(),
            "batch": self.flag_batch.isChecked(), "checkpoint": self.flag_checkpoint.isChecked(),
            "output": self.flag_output.isChecked(), "lineage": self.flag_lineage.isChecked(),
        })
        self.notice.setText(f"Zobrazeno {self.model.rowCount()} z {len(self.records)} běhů; filtr používá místní index.")

    def refresh(self, *_):
        root = self.context.settings.log_dir

        def scan(task):
            index = HistoryIndex(root)
            return index.refresh(), index.reverse_lineage()

        def receive(value):
            self.records, self.reverse_lineage = value
            self.model.set_runs([build_run(record, steps=record.get("timeline_steps") or [],
                                           reverse_lineage=self.reverse_lineage) for record in self.records])
            self.apply_filters()

        self.context.operations.start("Obnovení Run Studia", scan, receive, popup=False, identifier="history.index")

    def _row_selected(self, current, _previous):
        run = self.model.run_at(current.row()) if current.isValid() else None
        if run:
            self.tracks.select_stage(run.run_id, "")
            self.load_run(run)

    def load_run(self, run: RunView):
        self.generation += 1
        generation = self.generation
        self.run, self.adapter, self.payload, self.selected_step = run, None, {}, None
        self.phase.setText(f"Vybraný běh: {run.mode} · {run.run_id} · načítám kanonickou evidenci…")
        for button in self.buttons.values():
            button.setEnabled(False)
        directory = safe_join_under_root(self.context.settings.log_dir, run.run_id)

        def read(task):
            adapter = LegacyRunAdapter(directory)
            payload = _payload(adapter)
            if adapter.bundle:
                checked = []
                for checkpoint in payload["checkpoints"]:
                    row = dict(checkpoint)
                    try:
                        adapter.bundle.validate_checkpoint(str(row.get("checkpoint_id") or ""))
                        row["_availability_valid"] = True
                    except (ValueError, OSError, KeyError) as error:
                        row["_availability_valid"] = False
                        row["_availability_error"] = str(error)
                    checked.append(row)
                payload["checkpoints"] = checked
            state = read_state(adapter.root)
            full = build_run(payload["summary"], steps=payload["steps"], state=state,
                             events=payload["events"], reverse_lineage=self.reverse_lineage)
            return adapter, payload, state, full

        def receive(value):
            if generation != self.generation:
                return
            self.adapter, self.payload, self._state, self.run = value
            self.phase.setText(f"Vybraný běh: {self.run.mode} · {self.run.run_id}")
            self.notice.setText("Legacy evidence je pouze ke čtení; chybějící údaje jsou Není evidováno."
                                if self.adapter.legacy else "Kanonická evidence načtena. Přímá akce vytvoří nový Run ID.")
            self.update_actions()

        self.context.operations.start("Načtení detailu běhu", read, receive, popup=False)

    def _stage_selected(self, run, stage):
        if not self.run or run.run_id != self.run.run_id:
            self.load_run(run)
        self.selected_step = stage.technical
        self.phase.setText(f"Vybraná fáze: {stage.stage or 'Není evidováno'} · {stage.title}")
        self.update_actions()

    def update_actions(self):
        if not self.adapter or not self.run:
            return
        decisions = self.policy.evaluate(self.payload.get("summary") or {}, getattr(self, "_state", {}),
                                         self.payload.get("checkpoints") or [], legacy=self.adapter.legacy,
                                         selected_step=self.selected_step,
                                         artifacts=self.payload.get("artifacts") or [])
        for name, button in self.buttons.items():
            apply_decision(button, decisions[name])

    def branch(self, relation, *, edit_input=False):
        if not self.adapter:
            return
        composer = BranchComposer(self.launcher, self.adapter, self.payload.get("checkpoints") or [], relation,
                                  str((self.selected_step or {}).get("stage") or ""), self, edit_input=edit_input)
        if composer.exec() != QDialog.Accepted or not composer.preview:
            return
        try:
            self.launcher.launch(self.adapter, composer.preview, composer.repair_instruction())
            composer.confirm_button.setEnabled(False)
            self.notice.setText("Nová větev byla spuštěna přímo v Run Studiu; sledujte standardní okno průběhu.")
        except (ValueError, OSError, KeyError) as error:
            self.notice.setText(str(error))

    def clone(self):
        if not self.adapter:
            return
        state = getattr(self, "_state", None) or read_state(self.adapter.root)
        ui = state.get("ui_state")
        if not isinstance(ui, dict) or not ui:
            self.notice.setText("Běh nemá přesně uložený ui_state.")
            return
        cloned = copy.deepcopy(ui)
        for key in ("response_id", "resume_prev_id", "resume_files", "preparation_snapshot",
                    "completed_hashes", "recovery_instruction", "source_checkpoint_id"):
            cloned.pop(key, None)
        self.workbench.reset()
        self.workbench.apply_state(cloned)
        self.workbench.widgets["response_id"].clear()
        self.workbench.pending_lineage = {"source_run_id": self.adapter.run_id, "relation_type": "clone"}
        self.activate_workbench.emit()

    def complete_batch(self):
        if not self.adapter:
            return
        state = getattr(self, "_state", {})
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
        root = str(self.adapter.root)
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
            RunDetailDialog(self.context, self.adapter, self.payload, getattr(self, "_state", {}), self.run, self).exec()

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
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in source.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(source.parent))
            return str(target)

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
        self.notice.setText("Parent existuje v lineage, ale není v aktuálním filtru.")

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
