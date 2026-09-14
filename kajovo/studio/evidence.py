"""Lidský pohled na záznamy s oddělenými úplnými technickými podklady."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QListWidget, QListWidgetItem, QPlainTextEdit, QWidget

from .components import vertical


NAMES = {
    "project": "Projekt", "run_id": "Identifikátor běhu", "status": "Stav",
    "mode": "Způsob práce", "created_at": "Vytvořeno", "finished_at": "Ukončeno",
    "input_summary": "Zadání", "output_summary": "Výsledek", "model_summary": "Modely",
    "title": "Krok", "stage": "Etapa", "human_summary": "Shrnutí",
    "human_message": "Událost", "output_text": "Odpověď", "display_name": "Soubor",
    "size_bytes": "Velikost v bajtech", "mime_type": "Druh souboru",
    "path_in_bundle": "Umístění v evidenci", "source_run_id": "Zdrojový běh",
    "target_run_id": "Navazující běh", "relation_type": "Druh návaznosti",
    "timestamp": "Čas", "severity": "Závažnost", "notes": "Poznámka",
    "text": "Výsledný text", "saved": "Uložené soubory", "response_id": "Identifikátor odpovědi",
}

VALUES = {
    "queued": "Čeká ve frontě", "in_progress": "Probíhá zpracování", "validating": "Ověřování podkladů",
    "finalizing": "Příprava výsledků", "cancelling": "Probíhá rušení", "expired": "Vypršel čas služby",
    "pending": "Čeká na zpracování", "submitted": "Odesláno službě", "downloaded": "Výsledky byly převzaty",
    "info": "Informace", "warning": "Upozornění", "error": "Chyba",
    "completed": "Dokončeno", "failed": "Chyba", "partial": "Částečný výsledek",
    "cancelled": "Zastaveno", "active": "Probíhá", "running": "Probíhá",
    "waiting": "Čeká", "batch_pending": "Předáno do dávky", "submission_unknown": "Výsledek odeslání není znám",
    "response_pending": "Čeká na odpověď", "dry_run": "Návrh bez zápisu",
    "files_complete_unverified": "Soubory převzaté, funkčnost neověřena",
    "clone": "Klonování zadání", "continue": "Pokračování", "repair": "Oprava",
    "rerun": "Opakované spuštění", "reuse_artifacts": "Opětovné použití souborů",
    "GENERATE": "Vytvoření projektu", "MODIFY": "Úprava projektu", "QA": "Odpověď na dotaz",
    "QFILE": "Vytvoření souboru", "KASKADA": "Posloupnost kroků",
}


class EvidenceView(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.value = None
        self.setAccessibleName(title)
        root = vertical(self)
        self.technical = QCheckBox("Zobrazit úplné technické podklady")
        self.technical.setObjectName("evidence.technical")
        self.technical.toggled.connect(self.render)
        root.addWidget(self.technical)
        self.records = QListWidget()
        self.records.setWordWrap(True)
        self.records.setAccessibleName("Záznamy · " + title)
        self.records.currentItemChanged.connect(self.render)
        root.addWidget(self.records, 1)
        self.content = QPlainTextEdit()
        self.content.setReadOnly(True)
        self.content.setAccessibleName("Obsah · " + title)
        root.addWidget(self.content, 2)

    def clear(self):
        self.value = None
        self.records.clear()
        self.content.clear()

    def setPlainText(self, text):
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            value = text
        self.set_value(value)

    def set_value(self, value):
        self.value = value
        self.records.clear()
        self.records.setVisible(isinstance(value, list))
        if isinstance(value, list):
            for index, record in enumerate(value, 1):
                if isinstance(record, dict):
                    title = next((str(record[key]) for key in ("title", "display_name", "human_message", "response_id", "artifact_id", "step_id", "event_type") if record.get(key)), f"Záznam {index}")
                    status = record.get("status", "")
                    item = QListWidgetItem(title + (" · " + VALUES.get(status, status) if status else ""))
                else:
                    item = QListWidgetItem(str(record))
                item.setData(Qt.UserRole, record)
                self.records.addItem(item)
            if self.records.count():
                self.records.setCurrentRow(0)
        self.render()

    def selected(self):
        item = self.records.currentItem()
        return item.data(Qt.UserRole) if item else self.value

    def render(self, *_):
        value = self.selected()
        if self.technical.isChecked():
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        elif isinstance(value, dict):
            parts = []
            for key, name in NAMES.items():
                content = value.get(key)
                if content in (None, "", []):
                    continue
                if isinstance(content, list):
                    content = ", ".join(str(item) for item in content)
                elif isinstance(content, str):
                    content = VALUES.get(content, content)
                parts.append(f"{name}\n{content}")
            text = "\n\n".join(parts) or "Tento záznam nemá lidské shrnutí; úplné údaje jsou dostupné v technických podkladech."
        elif value:
            text = str(value)
        else:
            text = "Žádné záznamy nejsou evidovány."
        self.content.setPlainText(text)
