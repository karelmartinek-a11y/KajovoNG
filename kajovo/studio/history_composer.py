"""Lokální confirmation/repair composer bez generativního preflightu."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QPlainTextEdit, QSplitter, QWidget

from .components import action, actions, caption, friendly_error, panel, scroll, vertical


RELATIONS = {"continue": "Pokračovat", "rerun": "Znovu spustit", "repair": "Opravit"}


class BranchComposer(QDialog):
    def __init__(self, launcher, adapter, checkpoints, relation, selected_stage="", parent=None, *, edit_input=False):
        super().__init__(parent)
        self.launcher, self.adapter, self.relation, self.selected_stage = launcher, adapter, relation, selected_stage
        self.setWindowTitle("Historie · příprava navazujícího běhu")
        self.resize(1120, 900)
        self.setMinimumSize(640, 360)
        content = QWidget()
        body = vertical(content)
        body.addWidget(caption("Chyba a příprava opravného běhu" if relation == "repair" else "Příprava navazujícího běhu", "heading"))
        body.addWidget(caption(f"Zdrojový běh: {adapter.run_id}", "muted"))
        from .history_models import RunTableModel
        from .history_timeline import RunTrackView
        self.timeline = RunTrackView()
        self.timeline_model = RunTableModel(self)
        self.timeline.setModel(self.timeline_model)
        self.timeline.setColumnHidden(0, True)
        self.timeline.setColumnWidth(1, 200)
        self.timeline.setFixedHeight(168)
        self.timeline.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self.timeline)
        columns = QSplitter(Qt.Horizontal)
        source, source_body = panel("Co se stalo")
        self.error = caption("Načítám doloženou chybu…", "error")
        self.technical = QPlainTextEdit()
        self.technical.setReadOnly(True)
        self.technical.setAccessibleName("Technický detail zdrojové chyby")
        self.technical.setMaximumHeight(180)
        self.technical.hide()
        source_body.addWidget(self.error)
        source_body.addWidget(action("history.branch.technical", "Zobrazit nebo skrýt technické podrobnosti", lambda: self.technical.setVisible(not self.technical.isVisible())))
        source_body.addWidget(self.technical, 1)
        self.preserved = caption("", "muted")
        source_body.addWidget(self.preserved)
        source_body.addStretch()
        columns.addWidget(source)
        destination, destination_body = panel("Navazující běh")
        self.checkpoint = QComboBox()
        self.checkpoint.setAccessibleName("Ověřený bod obnovy")
        self.titles = {}
        for row in checkpoints:
            if edit_input and row.get("checkpoint_type") != "input_ready":
                continue
            if row.get("safe_to_continue") and row.get("_availability_valid", True):
                from .history_models import STAGE_TITLES
                state = row.get("state_snapshot") or {}
                for step in (state.get("cascade_definition") or {}).get("steps") or []:
                    self.titles[step.get("id")] = step.get("title") or step.get("id")
                kind = row.get("checkpoint_type") or ""
                title = ("Od původního vstupu" if kind in {"input_ready", "cascade_input_ready"}
                         else "Po fázi: " + STAGE_TITLES[kind] if kind in STAGE_TITLES
                         else "Pokračovat krokem: " + str(self.titles.get(state.get("next_step_id"), state.get("next_step_id")))
                         if state.get("next_step_id") else "Uložený bod obnovy")
                self.checkpoint.addItem(
                    title, row.get("checkpoint_id")
                )
                self.checkpoint.setItemData(self.checkpoint.count() - 1, row.get("checkpoint_id"), Qt.ToolTipRole)
        if relation != "rerun" and self.checkpoint.count():
            self.checkpoint.setCurrentIndex(self.checkpoint.count() - 1)
        destination_body.addWidget(caption("Odkud navázat"))
        destination_body.addWidget(self.checkpoint)
        self.summary_box, summary_layout = panel("Co se po spuštění provede")
        self.summary = caption("")
        summary_layout.addWidget(self.summary)
        destination_body.addWidget(self.summary_box)
        self.instruction = QPlainTextEdit()
        self.instruction.setAccessibleName("Pokyn pro nově prováděnou část")
        self.instruction.setMaximumHeight(180)
        self.instruction.setPlaceholderText(
            "Upravte dotaz pro nové zpracování. Původní běh i jeho vstupy zůstanou zachovány."
            if edit_input else "Popište opravu. Pokyn se použije na práci následující po ověřeném bodu obnovy."
        )
        self.instruction.setVisible(relation == "repair" or edit_input)
        destination_body.addWidget(self.instruction, 1)
        destination_body.addStretch()
        columns.addWidget(destination)
        columns.setSizes([440, 560])
        columns.setMinimumHeight(340)
        body.addWidget(columns, 1)
        root = vertical(self, 0)
        root.addWidget(scroll(content), 1)
        self.confirm_button = action("history.branch.confirm", RELATIONS[relation], self.accept, "primary")
        root.addWidget(actions(action("history.branch.cancel", "Zrušit", self.reject), self.confirm_button))
        self.preview = None
        self.generation = 0
        self.checkpoint.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def update_preview(self):
        identifier = self.checkpoint.currentData()
        self.generation += 1
        generation = self.generation
        self.preview = None
        self.confirm_button.setEnabled(False)
        self.summary.setText("Ověřuji zdroj a připravuji místní plán. Nic se neodesílá.")

        def calculate(task):
            try:
                from .history_models import build_run
                preview = self.launcher.preview(self.adapter, identifier, self.relation, self.selected_stage)
                run = build_run(self.adapter.run_record(), steps=self.adapter.steps())
                return preview, run
            except (ValueError, OSError, KeyError) as error:
                return friendly_error(error, "Přípravu náhledu")

        def receive(result):
            if generation != self.generation:
                return
            if isinstance(result, str):
                self.summary.setText(result)
                return
            self.preview, run = result
            self.timeline_model.set_runs([run])
            stage = next((row for row in run.stages if row.stage == self.selected_stage), None)
            if stage:
                self.timeline.select_stage(run.run_id, stage.step_id)
            value = asdict(self.preview)
            from .history_models import STAGE_TITLES
            titles = {**STAGE_TITLES, **self.titles}
            first = titles.get(value['first_paid_operation'], value['first_paid_operation'])
            if first.startswith("GET "):
                first = "Načtení již odeslané odpovědi; nové zadání se neodesílá"
            elif first.startswith("Bez nového requestu"):
                first = "Žádná. Nejprve potvrďte výrobu z uloženého plánu"
            inherited = [titles.get(stage, stage) for stage in value['inherited_stages']]
            self.error.setText(friendly_error(ValueError(value['source_error']), "Obnovu běhu"))
            self.technical.setPlainText(value['technical_error'])
            self.preserved.setText("Zachované soubory:\n" + ("\n".join(value['skipped_paths']) or "Žádné ověřené soubory k převzetí."))
            self.summary.setText(
                f"Převezme se: {', '.join(inherited) or 'uložený vstup'}\n"
                f"Přeskočí se: {', '.join(inherited) or 'nic'}\n"
                f"První nová placená operace: {first}\n\n"
                "Zdrojový běh → nová větev\nNové ID vznikne při spuštění. Původní běh zůstane zachován."
            )
            self.confirm_button.setEnabled(True)

        self.launcher.context.operations.start_read("Příprava historické větve", calculate, receive, popup=False)

    def repair_instruction(self):
        return self.instruction.toPlainText().strip()
