"""Čitelné finanční podklady s odděleným technickým detailem."""

from html import escape
import json
from PySide6.QtWidgets import QPushButton


def table(headers, rows):
    head = "".join(f"<th align='left'>{escape(str(value))}</th>" for value in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f"<td>{escape(str(value if value is not None else 'Nedoloženo'))}</td>" for value in row
        )
        + "</tr>"
        for row in rows
    )
    return f"<table width='100%' cellspacing='0' cellpadding='7' border='1'><tr>{head}</tr>{body}</table>"


def detail_toggle(layout, browser, summary, data):
    browser.setHtml(summary)
    button = QPushButton("Technické podklady")
    button.setCheckable(True)

    def toggle(checked):
        if checked:
            browser.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            browser.setHtml(summary)
        button.setText("Zobrazit souhrn" if checked else "Technické podklady")

    button.toggled.connect(toggle)
    layout.addWidget(button)


def receipt_summary(rows):
    return "<h2>Spotřeba a cena</h2>" + table(
        ("Projekt / model", "Etapa", "Vstup / výstup tokenů", "USD"),
        [
            (
                f"{r.get('project', '')} / {r.get('model', '')}",
                r.get("flow_type", ""),
                f"{r.get('input_tokens', 0)} / {r.get('output_tokens', 0)}",
                r.get("total_usd"),
            )
            for r in rows
        ],
    )
