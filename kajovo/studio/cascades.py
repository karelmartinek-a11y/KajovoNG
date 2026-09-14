"""Editor kaskád s ověřovaným pořadím a úplným kontraktem každého kroku."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFileDialog, QListWidget,
    QListWidgetItem, QPlainTextEdit, QTabWidget, QWidget,
)

from kajovo.core.cascade_contract import validate_cascade_definition
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from kajovo.core.cascade_types import CascadeDefinition, CascadeInput, CascadeOutput, CascadeStep
from kajovo.core.utils import atomic_write_text, new_run_id
from .components import Form, PathInput, action, actions, caption, confirm, scroll, vertical
from .resources import ValueDialog
from .cascade_items import CascadeItemDialog


class CascadesPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.definition = CascadeDefinition("Nová kaskáda")
        self.current_id = None
        self.location = None
        self.loading = False
        root = vertical(self, 0)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        overview = QWidget()
        body = vertical(overview)
        self.form = Form()
        self.name = self.form.text("cascade.name", "Název kaskády", self.definition.name)
        self.project = self.form.text("cascade.project", "Projekt")
        self.input = self.form.add("cascade.input", "Vstupní adresář", PathInput(directories=True))
        self.output = self.form.add("cascade.output", "Výstupní adresář", PathInput(directories=True))
        body.addWidget(self.form)
        self.steps = QListWidget()
        self.steps.setWordWrap(True)
        self.steps.setDragDropMode(QAbstractItemView.InternalMove)
        self.steps.setAccessibleName("Kroky kaskády v pořadí zpracování")
        self.steps.currentItemChanged.connect(self.select_step)
        self.steps.model().rowsMoved.connect(self.reorder)
        body.addWidget(self.steps, 1)
        body.addWidget(actions(action("cascade.step.add", "Přidat krok", self.add_step),
                               action("cascade.step.copy", "Duplikovat krok", self.duplicate_step),
                               action("cascade.step.remove", "Odstranit krok", self.remove_step, "danger")))
        body.addWidget(actions(action("cascade.step.up", "Posunout výše", lambda: self.move_step(-1)),
                               action("cascade.step.down", "Posunout níže", lambda: self.move_step(1)),
                               action("cascade.step.edit", "Upravit vybraný krok", lambda: self.tabs.setCurrentIndex(1)),
                               action("cascade.outputs", "Finální výstupy a spuštění od kroku", self.definition_options)))
        self.tabs.addTab(scroll(overview), "Posloupnost")
        detail = QWidget()
        body = vertical(detail)
        self.step_form = Form()
        self.title = self.step_form.text("cascade.step.title", "Název kroku")
        self.model = self.step_form.text("cascade.step.model", "Přesný model kroku")
        self.context_id = self.step_form.text("cascade.step.context", "Sdílený kontext")
        self.deterministic = self.step_form.check("cascade.step.deterministic", "Deterministický krok")
        self.inherit_temperature = self.step_form.check("cascade.step.default_temperature", "Použít výchozí teplotu modelu", True)
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0, 2)
        self.step_form.add("cascade.step.temperature", "Teplota", self.temperature)
        body.addWidget(self.step_form)
        body.addWidget(caption("Instrukce kroku"))
        self.instructions = QPlainTextEdit()
        self.instructions.setAccessibleName("Instrukce kroku")
        self.instructions.setMinimumHeight(130)
        body.addWidget(self.instructions)
        body.addWidget(caption("Textový vstup"))
        self.text = QPlainTextEdit()
        self.text.setAccessibleName("Textový vstup kroku")
        self.text.setMinimumHeight(130)
        body.addWidget(self.text)
        body.addWidget(actions(action("cascade.step.apply", "Použít změny kroku", self.commit_step, "primary"),
                               action("cascade.step.contract", "Vstupy, výstupy a pokročilé vlastnosti", self.edit_contract)))
        self.tabs.addTab(scroll(detail), "Detail kroku")
        self.item_lists = {}
        for kind, title in (("inputs", "Vstupy kroku"), ("outputs", "Výstupy kroku")):
            page = QWidget()
            layout = vertical(page)
            listing = QListWidget()
            listing.setWordWrap(True)
            listing.setAccessibleName(title)
            self.item_lists[kind] = listing
            layout.addWidget(listing, 1)
            layout.addWidget(actions(
                action("cascade." + kind + ".add", "Přidat", lambda checked=False, target=kind: self.edit_item(target, new=True)),
                action("cascade." + kind + ".edit", "Upravit", lambda checked=False, target=kind: self.edit_item(target)),
                action("cascade." + kind + ".remove", "Odebrat", lambda checked=False, target=kind: self.remove_item(target), "danger")))
            self.tabs.addTab(page, title)
        self.validation = caption("Přidejte kroky kaskády.", "muted")
        root.addWidget(self.validation)
        root.addWidget(actions(action("cascade.new", "Nová kaskáda", self.reset),
                               action("cascade.load", "Načíst", self.load),
                               action("cascade.save", "Uložit", self.save),
                               action("cascade.save_as", "Uložit jako", lambda: self.save(force_path=True)),
                               action("cascade.start", "Spustit kaskádu", self.start, "primary")))

    def selected(self):
        return self.definition.step_by_id(self.current_id) if self.current_id else None

    def draw_steps(self, selected=None):
        self.loading = True
        self.steps.clear()
        for index, step in enumerate(self.definition.steps, 1):
            item = QListWidgetItem(f"{index}. {step.title or 'Nepojmenovaný krok'}\n{step.model or 'Model není vybraný'}")
            item.setData(Qt.UserRole, step.id)
            self.steps.addItem(item)
            if step.id == selected:
                self.steps.setCurrentItem(item)
        self.loading = False
        self.select_step(self.steps.currentItem())
        self.validate()

    def select_step(self, item, previous=None):
        if self.loading:
            return
        if previous and self.current_id == previous.data(Qt.UserRole):
            self.commit_step(redraw=False)
        self.current_id = item.data(Qt.UserRole) if item else None
        step = self.selected()
        if step:
            self.title.setText(step.title)
            self.model.setText(step.model)
            self.context_id.setText(step.context_id)
            self.deterministic.setChecked(step.deterministic)
            self.inherit_temperature.setChecked(step.temperature is None)
            self.temperature.setValue(step.temperature or 0)
            self.instructions.setPlainText(step.instructions)
            self.text.setPlainText(step.input_text)
        self.refresh_items()

    def refresh_items(self):
        if not hasattr(self, "item_lists"):
            return
        step = self.selected()
        for kind, listing in self.item_lists.items():
            listing.clear()
            if step:
                if kind == "outputs":
                    step.ensure_outputs()
                for record in getattr(step, kind):
                    item = QListWidgetItem(record.name or record.id)
                    item.setData(Qt.UserRole, record.id)
                    listing.addItem(item)

    def edit_item(self, kind, new=False):
        step = self.selected()
        if not step:
            self.validation.setText("Nejdříve vyberte krok kaskády.")
            return
        rows = getattr(step, kind)
        listing = self.item_lists[kind]
        index = listing.currentRow()
        if not new and not 0 <= index < len(rows):
            return
        record = (CascadeInput(name="Nový vstup") if kind == "inputs" else CascadeOutput(name="Nový výstup")) if new else copy.deepcopy(rows[index])
        previous = self.definition.steps[:self.definition.step_index(step.id)]
        dialog = CascadeItemDialog(record, previous, self, inputs=step.inputs)
        if dialog.exec() == QDialog.Accepted:
            if new:
                rows.append(dialog.record)
            else:
                rows[index] = dialog.record
            self.refresh_items()
            self.validate()

    def remove_item(self, kind):
        step = self.selected()
        index = self.item_lists[kind].currentRow()
        if step and 0 <= index < len(getattr(step, kind)):
            getattr(step, kind).pop(index)
            self.refresh_items()
            self.validate()

    def commit_step(self, checked=False, redraw=True):
        step = self.selected()
        if step:
            step.title = self.title.text()
            step.model = self.model.text().strip()
            step.context_id = self.context_id.text()
            step.deterministic = self.deterministic.isChecked()
            step.temperature = None if self.inherit_temperature.isChecked() else self.temperature.value()
            step.instructions = self.instructions.toPlainText()
            step.input_text = self.text.toPlainText()
            if redraw:
                self.draw_steps(step.id)

    def add_step(self):
        self.commit_step(redraw=False)
        step = CascadeStep(title=f"Krok {len(self.definition.steps) + 1}", model=self.context.settings.default_model)
        self.definition.steps.append(step)
        self.draw_steps(step.id)

    def duplicate_step(self):
        self.commit_step(redraw=False)
        step = self.selected()
        if step:
            value = step.to_dict()
            value.pop("id", None)
            value["title"] += " · kopie"
            for collection in ("inputs", "outputs"):
                for entry in value[collection]:
                    entry.pop("id", None)
            new_step = CascadeStep.from_dict(value)
            input_ids = {old.id: new.id for old, new in zip(step.inputs, new_step.inputs, strict=True)}
            for output in new_step.outputs:
                if output.modify_input_id in input_ids:
                    output.modify_input_id = input_ids[output.modify_input_id]
            self.definition.steps.append(new_step)
            self.draw_steps(new_step.id)

    def remove_step(self):
        step = self.selected()
        if step and confirm(self, "Odstranit krok", "Odstranit vybraný krok? Návaznosti ostatních kroků se znovu ověří."):
            self.definition.steps.remove(step)
            self.current_id = None
            self.draw_steps()

    def move_step(self, offset):
        current = self.steps.currentRow()
        other = current + offset
        if 0 <= current < len(self.definition.steps) and 0 <= other < len(self.definition.steps):
            self.commit_step(redraw=False)
            self.definition.steps[current], self.definition.steps[other] = self.definition.steps[other], self.definition.steps[current]
            self.draw_steps(self.current_id)

    def reorder(self, *_):
        if self.loading:
            return
        by_id = {step.id: step for step in self.definition.steps}
        self.definition.steps = [by_id[self.steps.item(i).data(Qt.UserRole)] for i in range(self.steps.count())]
        self.validate()

    def edit_contract(self):
        self.commit_step(redraw=False)
        step = self.selected()
        if not step:
            return
        dialog = ValueDialog("Úplný kontrakt kroku", "Vstupy, výstupy, rozhodnutí, návaznosti, schéma a soubory kroku", json.dumps(step.to_dict(), ensure_ascii=False, indent=2), self, structured=True)
        if dialog.exec() == QDialog.Accepted:
            try:
                updated = CascadeStep.from_dict(dialog.value)
                if updated.id != step.id:
                    raise ValueError("Identifikátor existujícího kroku nelze změnit.")
                self.definition.steps[self.definition.step_index(step.id)] = updated
                self.draw_steps(step.id)
            except (TypeError, ValueError) as error:
                self.validation.setText(str(error))

    def definition_options(self):
        self.commit_step(redraw=False)
        dialog = ValueDialog("Kontrakt kaskády", "Úplná definice včetně finálních výstupů a vstupního kroku", json.dumps(self.definition.to_dict(), ensure_ascii=False, indent=2), self, structured=True)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.definition = CascadeDefinition.from_dict(dialog.value)
                self.name.setText(self.definition.name)
                self.draw_steps()
            except (TypeError, ValueError) as error:
                self.validation.setText(str(error))

    def validate(self):
        self.definition.name = self.name.text()
        self.definition.default_out_dir = self.output.text()
        try:
            validate_cascade_definition(self.definition)
        except ValueError as error:
            self.validation.setText(str(error))
            return False
        self.validation.setText("Definice kaskády splňuje místní pravidla návazností.")
        return True

    def reset(self):
        self.definition = CascadeDefinition("Nová kaskáda")
        self.name.setText(self.definition.name)
        self.location = None
        self.current_id = None
        self.draw_steps()

    def load(self):
        base = Path(self.context.settings.log_dir).parent / "cascades"
        path, _ = QFileDialog.getOpenFileName(self, "Načíst kaskádu", str(base), "Kaskády (*.json)")
        if path:
            try:
                definition = CascadeDefinition.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
                self.definition = definition
                self.location = path
                self.current_id = None
                self.name.setText(definition.name)
                self.output.setText(definition.default_out_dir)
                self.draw_steps()
            except (ValueError, OSError) as error:
                self.validation.setText(str(error))

    def save(self, checked=False, force_path=False):
        self.commit_step(redraw=False)
        if not self.validate():
            return
        path = self.location
        if not path or force_path:
            base = Path(self.context.settings.log_dir).parent / "cascades"
            base.mkdir(parents=True, exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(self, "Uložit kaskádu", str(base / "kaskada.json"), "Kaskády (*.json)")
        if path:
            try:
                atomic_write_text(path, json.dumps(self.definition.to_dict(), ensure_ascii=False, indent=2))
                self.location = path
                self.validation.setText("Kaskáda byla uložena.")
            except OSError as error:
                self.validation.setText(str(error))

    def start(self):
        self.commit_step(redraw=False)
        if not self.validate():
            return
        if not self.context.api_key or not self.project.text().strip():
            self.validation.setText("Vyplňte projekt a uložte přístupový klíč.")
            return
        if any(step.model not in self.context.models for step in self.definition.steps):
            self.validation.setText("Některý krok používá model nedostupný v katalogu účtu.")
            return
        cfg = CascadeRunConfig(self.project.text(), copy.deepcopy(self.definition), self.input.text(), self.output.text(), new_run_id())
        try:
            self.context.operations.assert_output_available(cfg.out_dir)
        except ValueError as error:
            self.validation.setText(str(error))
            return
        worker = CascadeRunWorker(cfg, copy.deepcopy(self.context.settings), self.context.api_key)
        return self.context.operations.adopt("Kaskáda · " + self.definition.name, worker, identifier=cfg.run_id, output_dir=cfg.out_dir)
