"""Formuláře typovaných vstupů a výstupů, včetně rozhodovacích větví."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QComboBox, QDialog, QLineEdit, QPlainTextEdit, QWidget

from kajovo.core.cascade_types import CASCADE_FILE_TYPES, CascadeInput
from .components import Form, action, actions, caption, friendly_error, scroll, vertical


class CascadeItemDialog(QDialog):
    def __init__(self, record, previous_steps, parent=None, inputs=()):
        super().__init__(parent)
        self.record = record
        self.previous_steps = previous_steps
        self.setWindowTitle("Vstup kroku" if isinstance(record, CascadeInput) else "Výstup kroku")
        self.resize(620, 650)
        root = vertical(self)
        page = QWidget()
        body = vertical(page)
        self.form = Form()
        self.form.text("name", "Název", record.name)
        if isinstance(record, CascadeInput):
            self.form.choice("source", "Zdroj vstupu", [("Nový text", "text"), ("Místní soubor", "local_file"), ("Nahraný soubor", "file_id"), ("Výstup předchozího kroku", "output")], record.source)
            value = QPlainTextEdit(record.value)
            value.setMinimumHeight(140)
            self.form.add("value", "Text, cesta nebo kód souboru podle zvoleného typu", value)
            step_choices = [("Vyberte předchozí krok", ""), *[(step.title or step.id, step.id) for step in previous_steps]]
            self.form.choice("source_step_id", "Zdrojový krok", step_choices, record.source_step_id)
            self.form.choice("source_output_id", "Zdrojový výstup", [("Vyberte výstup", "")])
            self.form.fields["source_step_id"].currentIndexChanged.connect(self.refresh_outputs)
            self.refresh_outputs()
            widget = self.form.fields["source_output_id"]
            if record.source_output_id:
                index = widget.findData(record.source_output_id)
                if index < 0:
                    widget.addItem(record.source_output_id + " · nedostupný", record.source_output_id)
                    index = widget.count() - 1
                widget.setCurrentIndex(index)
        else:
            self.form.choice("kind", "Druh výstupu", [("Text", "text"), ("Strukturovaná data", "json"), ("Soubor", "file"), ("Rozhodnutí", "decision")], record.kind)
            self.form.choice("file_type", "Typ souboru", [("Bez souboru", ""), *sorted(CASCADE_FILE_TYPES)], record.file_type)
            self.form.text("file_name", "Název a podsložka výsledného souboru", record.file_name)
            self.form.choice("file_mode", "Způsob zápisu", [("Vytvořit soubor", "create"), ("Upravit vstupní soubor", "modify")], record.file_mode)
            self.form.choice("modify_input_id", "Upravovaný vstup", [("Vyberte vstup", ""), *[(item.name or item.id, item.id) for item in inputs]], record.modify_input_id)
            json_mask = QPlainTextEdit(
                json.dumps(record.json_schema, ensure_ascii=False, indent=2)
                if record.json_schema is not None
                else ""
            )
            json_mask.setMinimumHeight(180)
            self.form.add(
                "json_schema",
                "Pravidla pro požadovaná pole odpovědi (technický zápis JSON)",
                json_mask,
            )
            self.schema_hint = caption(
                "Výsledek musí být sada pojmenovaných údajů. Každý popsaný údaj je povinný a další údaje se nepřidávají. Toto pokročilé nastavení se zapisuje ve formátu JSON Schema.",
                "muted",
            )
            body.addWidget(self.schema_hint)
            options = QPlainTextEdit(json.dumps([item.to_dict() for item in record.decision_options], ensure_ascii=False, indent=2))
            options.setMinimumHeight(160)
            self.form.add("decision_options", "Možné odpovědi a navazující kroky (technický zápis JSON)", options)
            self.branch_hint = caption("Pro každou možnou odpověď určete následující krok. V technickém zápisu je odpověď v poli value a číslo následujícího kroku v target_step_id. Prázdný cíl ukončí úlohu.", "muted")
            body.addWidget(self.branch_hint)
        body.addWidget(self.form)
        self.notice = caption("", "error")
        body.addWidget(self.notice)
        root.addWidget(scroll(page), 1)
        root.addWidget(actions(action("cascade.item.cancel", "Zrušit", self.reject), action("cascade.item.save", "Použít", self.submit, "primary")))
        self.form.changed.connect(self.update_fields)
        self.update_fields()

    def update_fields(self):
        fields = self.form.fields
        if isinstance(self.record, CascadeInput):
            source = fields["source"].currentData()
            fields["value"].setEnabled(source != "output")
            for name in ("source_step_id", "source_output_id"):
                fields[name].setEnabled(source == "output")
            title = {"text": "Text předaný kroku", "local_file": "Úplná cesta k souboru v počítači", "file_id": "Číslo souboru nahraného do služby", "output": "Obsah se převezme z vybraného předchozího kroku"}.get(source, "Obsah vstupu")
            self.form.body.labelForField(fields["value"]).setText(title)
            fields["value"].setAccessibleName(title)
        else:
            kind = fields["kind"].currentData()
            for name in ("file_type", "file_name", "file_mode"):
                fields[name].setEnabled(kind == "file")
            fields["modify_input_id"].setEnabled(kind == "file" and fields["file_mode"].currentData() == "modify")
            fields["json_schema"].setEnabled(kind == "json")
            fields["decision_options"].setEnabled(kind == "decision")
            self.schema_hint.setVisible(kind == "json")
            self.branch_hint.setVisible(kind == "decision")

    def refresh_outputs(self):
        source = self.form.fields["source_step_id"].currentData()
        outputs = self.form.fields["source_output_id"]
        selected = outputs.currentData()
        outputs.clear()
        outputs.addItem("Vyberte výstup", "")
        for step in self.previous_steps:
            if step.id == source:
                step.ensure_outputs()
                for output in step.outputs:
                    outputs.addItem(output.name, output.id)
        outputs.setCurrentIndex(max(0, outputs.findData(selected)))

    def submit(self):
        from kajovo.core.orchestration.contracts import parse_json_value_strict
        value = self.record.to_dict()
        try:
            for name, widget in self.form.fields.items():
                if isinstance(widget, QComboBox):
                    value[name] = widget.currentData() or ""
                elif isinstance(widget, QLineEdit):
                    value[name] = widget.text()
                else:
                    text = widget.toPlainText()
                    if name == "decision_options":
                        value[name] = parse_json_value_strict(text)
                    elif name == "json_schema":
                        value[name] = parse_json_value_strict(text) if text.strip() else None
                    else:
                        value[name] = text
            if value.get("kind") != "json":
                value["json_schema"] = None
            self.record = type(self.record).from_dict(value)
        except (ValueError, TypeError) as error:
            self.notice.setText(friendly_error(error))
            return
        self.accept()
