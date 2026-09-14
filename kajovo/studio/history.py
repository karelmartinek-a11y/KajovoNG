"""Čtení kanonické evidence a nové běhy s doloženou návazností."""

from __future__ import annotations

import copy
import hashlib
import json
from uuid import uuid4
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QListWidget, QListWidgetItem, QTabWidget, QWidget

from kajovo.core.batch_completion import batch_ids, read_state
from kajovo.core.delivery_preparation import validate_preparation_snapshot
from kajovo.core.run_bundle import HistoryIndex, LegacyRunAdapter
from kajovo.core.runlog import verified_output_evidence
from kajovo.core.utils import safe_join_under_root
from .components import DetailDialog, Form, action, actions, caption, vertical
from .resources import ValueDialog
from .evidence import EvidenceView, VALUES


class HistoryPage(QWidget):
    activate_workbench = Signal()

    def __init__(self, context, workbench, parent=None):
        super().__init__(parent)
        self.context = context
        self.workbench = workbench
        self.records = []
        self.adapter = None
        self.payload = {}
        self.generation = 0
        root = vertical(self, 0)
        self.filters = Form()
        self.search = self.filters.text("history.search", "Hledat v evidenci")
        self.project = self.filters.text("history.project", "Projekt")
        self.mode = self.filters.text("history.mode", "Režim")
        self.state = self.filters.text("history.state", "Stav")
        self.model = self.filters.text("history.model", "Model")
        self.date_from = self.filters.text("history.from", "Od data ve tvaru den.měsíc.rok")
        self.date_to = self.filters.text("history.to", "Do data ve tvaru den.měsíc.rok")
        self.tabs = QTabWidget()
        overview = QWidget()
        body = vertical(overview)
        body.addWidget(self.filters)
        body.addWidget(actions(action("history.refresh", "Obnovit historii", self.refresh),
                               action("history.filter", "Použít filtry", self.filter)))
        self.runs = QListWidget()
        self.runs.setWordWrap(True)
        self.runs.setAccessibleName("Evidované běhy")
        self.runs.currentItemChanged.connect(self.select_run)
        body.addWidget(self.runs, 1)
        self.tabs.addTab(overview, "Výběr běhu")
        self.views = {}
        for key, title in (("summary", "Přehled"), ("steps", "Průběh"), ("responses", "Odpovědi"),
                           ("artifacts", "Soubory"), ("events", "Události"), ("lineage", "Návaznosti"), ("integrity", "Technické")):
            view = EvidenceView(title)
            self.views[key] = view
            self.tabs.addTab(view, title)
        root.addWidget(self.tabs, 1)
        self.notice = caption("Vyberte běh; zdrojová evidence zůstává neměnná.", "muted")
        root.addWidget(self.notice)
        root.addWidget(actions(action("history.clone", "Klonovat zadání", self.clone),
                               action("history.continue", "Pokračovat", self.resume),
                               action("history.rerun", "Znovu spustit od checkpointu", lambda: self.resume(relation="rerun")),
                               action("history.repair", "Opravit od checkpointu", lambda: self.resume(relation="repair"))))
        root.addWidget(actions(action("history.step", "Detail kroku", self.step_detail),
                               action("history.artifact", "Otevřít nebo exportovat soubor", self.artifact),
                               action("history.reuse", "Použít soubor v novém zadání", lambda: self.artifact(reuse=True)),
                               action("history.integrity", "Ověřit integritu", self.verify)))

    def refresh(self):
        root = self.context.settings.log_dir
        def receive(records):
            self.records = records
            self.filter()
        self.context.operations.start("Načtení historie", lambda task: HistoryIndex(root).refresh(), receive)

    def filter(self):
        from datetime import datetime

        try:
            lower = datetime.strptime(self.date_from.text().strip(), "%d.%m.%Y").date() if self.date_from.text().strip() else None
            upper = datetime.strptime(self.date_to.text().strip(), "%d.%m.%Y").date() if self.date_to.text().strip() else None
            if lower and upper and lower > upper:
                raise ValueError("Začátek období nesmí být pozdější než konec.")
        except ValueError:
            self.notice.setText("Zadejte platné a správně seřazené datum ve tvaru den.měsíc.rok.")
            return
        selected = self.runs.currentItem().data(Qt.UserRole) if self.runs.currentItem() else None
        self.runs.clear()
        for record in self.records:
            searchable = json.dumps(record, ensure_ascii=False).casefold()
            if self.search.text().casefold() not in searchable:
                continue
            filters = {"project": self.project.text(), "mode": self.mode.text(), "status": self.state.text(), "model_summary": self.model.text()}
            if any(value.casefold() not in str(record.get(key, "")).casefold() for key, value in filters.items() if value):
                continue
            if lower or upper:
                try:
                    created = datetime.fromisoformat(record.get("created_at", "").replace("Z", "+00:00")).astimezone().date()
                except ValueError:
                    continue
                if (lower and created < lower) or (upper and created > upper):
                    continue
            item = QListWidgetItem(f"{record.get('project') or 'Projekt není evidován'} · {VALUES.get(record.get('status'), 'Není evidováno')}\n{record['run_id']} · {record.get('created_at', '')}")
            item.setData(Qt.UserRole, record["run_id"])
            self.runs.addItem(item)
            if record["run_id"] == selected:
                self.runs.setCurrentItem(item)

    def select_run(self, item, previous=None):
        self.generation += 1
        generation = self.generation
        self.adapter = None
        self.payload = {}
        for view in self.views.values():
            view.clear()
        if not item:
            return
        directory = safe_join_under_root(self.context.settings.log_dir, item.data(Qt.UserRole))

        def read(task):
            adapter = LegacyRunAdapter(directory)
            return adapter, {"summary": adapter.run_record(), "steps": adapter.steps(), "responses": adapter.responses(),
                             "artifacts": adapter.artifacts(), "events": adapter.events(), "lineage": adapter.lineage(),
                             "requests": adapter.requests(), "validations": adapter.validations(), "checkpoints": adapter.checkpoints()}

        def receive(result):
            if self.generation != generation:
                return
            self.adapter, self.payload = result
            for key, view in self.views.items():
                value = self.payload.get(key, "Ověření integrity spusťte příslušným tlačítkem.")
                view.setPlainText(json.dumps(value, ensure_ascii=False, indent=2, default=str) if not isinstance(value, str) else value)
            self.notice.setText("Starší evidence je pouze ke čtení; chybějící fakta nejsou doplněna." if self.adapter.legacy else "Kanonická evidence načtena; navazující akce vytvoří nový běh.")

        self.context.operations.start("Načtení evidence běhu", read, receive, popup=False)

    def clone(self):
        if not self.adapter:
            return
        state = read_state(self.adapter.root)
        ui = state.get("ui_state")
        if not isinstance(ui, dict) or not ui:
            self.notice.setText("Běh nemá přesně uložené zadání, které lze klonovat.")
            return
        self.workbench.reset()
        self.workbench.apply_state(copy.deepcopy(ui))
        self.workbench.widgets["response_id"].clear()
        self.workbench.pending_lineage = {"source_run_id": self.adapter.run_id, "relation_type": "clone"}
        self.activate_workbench.emit()

    def resume(self, checked=False, relation="continue"):
        if not self.adapter or not self.adapter.bundle:
            self.notice.setText("Tento běh nemá bezpečný checkpoint pro pokračování.")
            return
        checkpoints = [row for row in self.payload.get("checkpoints", []) if row.get("safe_to_continue")]
        if not checkpoints:
            self.notice.setText("Běh neobsahuje bezpečný checkpoint.")
            return
        dialog = ValueDialog("Vybrat checkpoint", "Identifikátor bezpečného checkpointu", str(checkpoints[-1].get("checkpoint_id", "")), self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            if batch_ids(read_state(self.adapter.root)):
                raise ValueError("Běh již odeslal dávku; výsledky převezměte v sekci Dávky, aby nevzniklo duplicitní odeslání.")
            checkpoint = self.adapter.bundle.validate_checkpoint(dialog.value)
            state = checkpoint["state_snapshot"]
            ui = state.get("ui_state")
            if not isinstance(ui, dict) or not ui:
                raise ValueError("Checkpoint neobsahuje přesné zadání.")
            snapshot = state.get("preparation_snapshot")
            if snapshot and ui.get("mode") in {"GENERATE", "MODIFY"}:
                snapshot = validate_preparation_snapshot(snapshot, ui["mode"], ui.get("maximum_quality", False))
            self.workbench.reset()
            self.workbench.apply_state(copy.deepcopy(ui))
            evidence = verified_output_evidence(self.adapter.root, ui.get("out_dir"))
            hashes = {row["path"]: row["sha256"] for row in evidence if row.get("sha256")}
            previous = snapshot.get("response_id") if snapshot else state.get("last_response_id")
            structure = snapshot.get("structure", {}) if snapshot else {}
            files = structure.get("touched_files" if ui.get("mode") == "MODIFY" else "files", [])
            self.workbench.resume = {"preparation_snapshot": snapshot, "completed_hashes": hashes, "skip_paths": list(hashes),
                                     "resume_prev_id": previous, "resume_files": copy.deepcopy(files), "response_id": previous}
            self.workbench.pending_lineage = {"source_run_id": self.adapter.run_id, "relation_type": relation, "source_checkpoint_id": dialog.value}
        except (ValueError, KeyError, OSError) as error:
            self.notice.setText(str(error))
            return
        self.activate_workbench.emit()

    def step_detail(self):
        steps = self.payload.get("steps", [])
        if not steps:
            return
        identifiers = [row.get("step_id", "") for row in steps]
        dialog = ValueDialog("Detail kroku", "Identifikátor kroku: " + ", ".join(identifiers), identifiers[0], self)
        if dialog.exec() == QDialog.Accepted:
            value = {"step": next((row for row in steps if row.get("step_id") == dialog.value), None)}
            for key in ("requests", "responses", "artifacts", "events", "validations"):
                value[key] = [row for row in self.payload.get(key, []) if row.get("step_id") == dialog.value]
            DetailDialog("Detail kroku", dialog.value, self, value).exec()

    def verify(self):
        if self.adapter:
            adapter = self.adapter
            generation = self.generation
            def receive(value):
                if generation == self.generation:
                    self.views["integrity"].set_value(value)
            self.context.operations.start("Ověření integrity evidence", lambda task: adapter.integrity(),
                                          receive)

    def artifact(self, checked=False, reuse=False):
        artifacts = self.payload.get("artifacts", [])
        if not artifacts or not self.adapter:
            return
        identifier = ValueDialog("Vybrat soubor", "Identifikátor artefaktu z přehledu Soubory", artifacts[0].get("artifact_id", ""), self)
        if identifier.exec() != QDialog.Accepted:
            return
        record = next((row for row in artifacts if row.get("artifact_id") == identifier.value), None)
        if not record:
            self.notice.setText("Zvolený artefakt není součástí tohoto běhu.")
            return
        relative = record.get("path_in_bundle")
        if not relative:
            self.notice.setText("Soubor nemá evidovanou archivní cestu.")
            return
        try:
            if reuse and (self.adapter.legacy or not record.get("reusable")):
                raise ValueError("Tento soubor nemá doložené povolení k opětovnému použití.")
            source = Path(safe_join_under_root(self.adapter.root, relative))
            data = source.read_bytes()
            if not record.get("sha256") or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ValueError("Obsah souboru neodpovídá evidovanému otisku.")
            if reuse:
                staging = Path(self.context.settings.cache_dir).resolve() / "history_reuse" / uuid4().hex
                staging.mkdir(parents=True, exist_ok=False)
                destination = str(staging / source.name)
            else:
                destination, _ = QFileDialog.getSaveFileName(self, "Exportovat ověřený soubor", source.name)
            if not destination:
                return
            target = Path(destination).resolve()
            if target == self.adapter.root.resolve() or self.adapter.root.resolve() in target.parents:
                raise ValueError("Export nesmí přepsat zdrojovou evidenci.")
            target.write_bytes(data)
            if reuse:
                self.workbench.reset()
                self.workbench.widgets["in_dir"].setText(str(target.parent))
                self.workbench.pending_lineage = {"source_run_id": self.adapter.run_id, "relation_type": "reuse_artifacts", "inherited_artifact_ids": [identifier.value]}
                self.activate_workbench.emit()
        except (ValueError, OSError) as error:
            self.notice.setText(str(error))
