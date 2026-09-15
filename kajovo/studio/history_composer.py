"""Lokální confirmation/repair composer bez generativního preflightu."""

from __future__ import annotations

from dataclasses import asdict

from PySide6.QtWidgets import QComboBox, QDialog, QPlainTextEdit, QWidget

from .components import action, actions, caption, panel, scroll, vertical


RELATIONS = {"continue": "Pokračovat", "rerun": "Znovu spustit", "repair": "Opravit"}


class BranchComposer(QDialog):
    def __init__(self, launcher, adapter, checkpoints, relation, selected_stage="", parent=None, *, edit_input=False):
        super().__init__(parent)
        self.launcher, self.adapter, self.relation, self.selected_stage = launcher, adapter, relation, selected_stage
        self.setWindowTitle("Run Studio · příprava nové větve")
        self.resize(940, 680)
        self.setMinimumSize(640, 360)
        content = QWidget()
        body = vertical(content)
        body.addWidget(caption("Chyba a příprava nové větve" if relation == "repair" else "Příprava nové větve", "heading"))
        self.checkpoint = QComboBox()
        self.checkpoint.setAccessibleName("Bezpečný checkpoint")
        for row in checkpoints:
            if row.get("safe_to_continue") and row.get("_availability_valid", True):
                self.checkpoint.addItem(
                    f"{row.get('checkpoint_type') or 'Checkpoint'} · {row.get('checkpoint_id')}", row.get("checkpoint_id")
                )
        if relation != "rerun" and self.checkpoint.count():
            self.checkpoint.setCurrentIndex(self.checkpoint.count() - 1)
        body.addWidget(caption("Bezpečný počáteční bod"))
        body.addWidget(self.checkpoint)
        self.summary_box, summary_layout = panel("Lokální náhled dopadu")
        self.summary = caption("")
        summary_layout.addWidget(self.summary)
        body.addWidget(self.summary_box)
        self.instruction = QPlainTextEdit()
        self.instruction.setAccessibleName("Pokyn pro nově prováděnou část")
        self.instruction.setPlaceholderText(
            "Upravte zadání nové QA větve. Původní běh ani jeho vstupy se nezmění."
            if edit_input else "Popište opravu. Pokyn se použije pouze za bezpečným checkpointem."
        )
        self.instruction.setVisible(relation == "repair" or edit_input)
        body.addWidget(self.instruction)
        body.addStretch()
        root = vertical(self, 0)
        root.addWidget(scroll(content), 1)
        self.confirm_button = action("history.branch.confirm", RELATIONS[relation], self.accept, "primary")
        root.addWidget(actions(action("history.branch.cancel", "Zrušit", self.reject), self.confirm_button))
        self.preview = None
        self.checkpoint.currentIndexChanged.connect(self.update_preview)
        self.update_preview()

    def update_preview(self):
        identifier = self.checkpoint.currentData()
        try:
            self.preview = self.launcher.preview(self.adapter, identifier, self.relation, self.selected_stage)
            value = asdict(self.preview)
            self.summary.setText(
                f"Zdroj: {value['source_run_id']}\nVybraný checkpoint: {value['checkpoint_type']}\n"
                f"Vybraná fáze/krok: {value['selected_stage'] or 'Není evidováno'}\n"
                f"Převezme se: {', '.join(value['inherited_stages']) or 'uložený vstup'}\n"
                f"Zachované artefakty: {len(value['skipped_paths'])} hashově ověřených souborů\n"
                f"Přeskočí se: {', '.join(value['inherited_stages']) or 'nic'}\n"
                f"Znovu se spustí od: {value['first_paid_operation']}\n"
                f"První nová placená operace: {value['first_paid_operation']}\n"
                f"Doložená chyba: {value['source_error']}\n{value['impact']}\n"
                f"Technický detail: {value['technical_error']}\n"
                "Tento náhled je čistě lokální a neposlal žádný OpenAI požadavek."
            )
            self.confirm_button.setEnabled(True)
        except ValueError as error:
            self.preview = None
            self.summary.setText(str(error))
            self.confirm_button.setEnabled(False)

    def repair_instruction(self):
        return self.instruction.toPlainText().strip()
