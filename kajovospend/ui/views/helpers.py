from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from kajovospend.ui.widgets.cards import Card


def table_card(title: str, headers: list[str], rows: list[list[str]]) -> QWidget:
    card = Card()
    title_label = QLabel(title)
    title_label.setObjectName('SectionTitle')
    card.layout.addWidget(title_label)
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(value))
    table.horizontalHeader().setStretchLastSection(True)
    card.layout.addWidget(table)
    wrap = QWidget()
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(card)
    return wrap
