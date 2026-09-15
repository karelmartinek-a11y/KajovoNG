"""Čitelné zadání, výsledek a kontext vybrané fáze; technické záznamy jsou vedlejší pohled."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFileDialog, QPlainTextEdit, QWidget

from .components import action, actions, caption, panel, vertical
from .history_data import unique_responses
from .history_models import format_duration


class TextCard(QWidget):
    def __init__(self, title, text, bundle_root, parent=None):
        super().__init__(parent)
        self.bundle_root = Path(bundle_root).resolve()
        self.text = text
        root = vertical(self, 0)
        box, body = panel(title)
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setMinimumHeight(80)
        self.editor.setAccessibleName(title)
        self.editor.setPlainText(text[:1024 * 1024] if text else "Text nebyl uložen.")
        body.addWidget(self.editor, 1)
        self.notice = caption("Náhled zkrácen na 1 MiB; uložení zachová celý text." if len(text) > 1024 * 1024 else "", "muted")
        self.notice.setVisible(bool(self.notice.text()))
        body.addWidget(self.notice)
        self.copy_button = action("history.text.copy", "Kopírovat", lambda: QGuiApplication.clipboard().setText(self.text))
        self.save_button = action("history.text.save", "Uložit TXT", self.save)
        self.copy_button.setEnabled(bool(text))
        self.save_button.setEnabled(bool(text))
        body.addWidget(actions(self.copy_button, self.save_button))
        root.addWidget(box, 1)

    def save(self):
        destination, _ = QFileDialog.getSaveFileName(self, "Uložit text", "text.txt", "Text (*.txt)")
        if destination:
            target = Path(destination).resolve()
            try:
                if target == self.bundle_root or self.bundle_root in target.parents:
                    raise ValueError("Uložení nesmí přepsat zdrojový běh.")
                target.write_text(self.text, encoding="utf-8")
            except (OSError, ValueError) as error:
                self.notice.setText(str(error))
                self.notice.show()


def human_answer(payload, step_id=None):
    records = [row for row in payload.get("responses") or [] if not step_id or row.get("step_id") == step_id]
    values = unique_responses(records)
    texts = []
    for row in values:
        structured = row.get("structured_value") or row.get("structured_output")
        if isinstance(structured, dict) and isinstance(structured.get("text"), str):
            texts.append(structured["text"])
        elif row.get("output_text"):
            import json
            text = row["output_text"]
            try:
                value = json.loads(text)
                text = value.get("text", text) if isinstance(value, dict) else text
            except (ValueError, TypeError):
                pass
            texts.append(str(text))
    return "\n\n".join(texts)


class PhaseInspector(QWidget):
    def __init__(self, run, payload, parent=None):
        super().__init__(parent)
        self.run, self.payload = run, payload
        root = vertical(self, 0)
        box, self.body = panel("Vybraná fáze")
        self.body.setAlignment(Qt.AlignTop)
        self.title = caption("Vyberte fázi na časové ose.", "section")
        self.info = caption("", "muted")
        self.validation = caption("")
        self.error = caption("", "error")
        self.answer = QPlainTextEdit()
        self.answer.setReadOnly(True)
        self.answer.setMinimumHeight(80)
        self.answer.setAccessibleName("Výstup vybrané fáze")
        for widget in (self.title, self.info, self.validation, self.error):
            self.body.addWidget(widget)
        self.body.addWidget(self.answer, 1)
        self.body.addStretch()
        root.addWidget(box, 1)
        if run.stages:
            self.select(run.stages[-1])

    def select(self, stage):
        responses = unique_responses([row for row in self.payload.get("responses") or []
                                      if row.get("step_id") == stage.step_id])
        self.title.setText(f"{stage.title} · {stage.stage}")
        duration = format_duration(stage.duration) if stage.duration is not None else "Konec fáze nebyl zapsán"
        status = stage.status.label
        if stage.status.key in {"running", "preparing", "created"} and self.run.status.terminal:
            status = "Konečný stav fáze nebyl zapsán"
        lines = [status, duration]
        if stage.model:
            lines.append(f"Model: {stage.model}")
        if stage.reasoning:
            lines.append(f"Hloubka uvažování: {stage.reasoning}")
        for key, label in (("input_tokens", "Vstupní tokeny"), ("output_tokens", "Výstupní tokeny")):
            values = [row[key] for row in responses if isinstance(row.get(key), int)]
            if values:
                lines.append(f"{label}: {sum(values):,}".replace(",", " "))
        ids = [str(row["response_id"]) for row in responses if row.get("response_id")]
        if ids:
            lines.append("Odpověď: " + ", ".join(ids))
        self.info.setText("\n".join(lines))
        validations = [row for row in self.payload.get("validations") or [] if row.get("step_id") == stage.step_id]
        self.validation.setText("\n".join(str(row.get("human_message") or row.get("summary") or row.get("status") or "")
                                           for row in validations) or "Validace nebyla uložena.")
        self.validation.setVisible(bool(validations))
        errors = [str(row.get("error") or row.get("incomplete_reason") or "") for row in responses
                  if row.get("error") or row.get("incomplete_reason")]
        self.error.setText("\n".join(errors))
        self.error.setVisible(bool(errors))
        answer = human_answer(self.payload, stage.step_id)
        self.answer.setPlainText(answer[:1024 * 1024])
        self.answer.setVisible(bool(answer))
