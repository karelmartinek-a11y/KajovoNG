"""Lidský pohled na záznamy s oddělenými úplnými technickými podklady."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QListWidget, QListWidgetItem, QPlainTextEdit, QWidget

from .components import vertical
from .presentation import FIELDS, VALUES, human_readable


NAMES = FIELDS


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
                    title = next((str(record[key]) for key in ("title", "display_name", "human_message") if record.get(key)), f"Záznam {index}")
                    status = record.get("status", "")
                    item = QListWidgetItem(title + (" · " + VALUES.get(status, "Stav není znám") if status else ""))
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
            text = human_readable(value)
        elif value:
            text = str(value)
        else:
            text = "Žádné záznamy nejsou evidovány."
        self.content.setPlainText(text)
