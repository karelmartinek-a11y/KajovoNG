"""Čitelné finanční podklady s odděleným technickým detailem."""

from html import escape
import json
from decimal import Decimal
from PySide6.QtWidgets import QPushButton


def amount(value, unit="USD"):
    if value is None:
        return "Nelze vyčíslit"
    value = Decimal(str(value))
    if 0 < value < Decimal("0.000001"):
        return "<0.000001 " + unit
    number = f"{value:.6f}".rstrip("0").rstrip(".")
    if "." not in number:
        number += ".00"
    return number + " " + unit


def estimate_summary(estimate):
    items = estimate.get("items", [estimate])
    rows, typical = [], Decimal(0)
    known = True
    for item in items:
        scenarios = item.get("scenarios", [])
        middle = scenarios[1].get("usd") if len(scenarios) > 1 else None
        known &= middle is not None
        typical += Decimal(middle) if middle is not None else 0
        rows.append((item.get("model", ""), item.get("input_tokens") if item.get("input_tokens") is not None else "Neznámý",
                     *[f"{s.get('output_tokens', 0):,} tokenů · {amount(s.get('usd'))}" for s in scenarios]))
    title = "Orientační cena: " + amount(typical if known else None)
    if estimate.get("includes_trial") and known:
        title = "Pracovní volání a zkouška: přibližně " + amount(typical * 2)
    html = "<h2>" + escape(title) + "</h2>"
    html += "<p>" + ("Zkušební volání" if str(estimate.get("stage", "")).startswith("PREFLIGHT") else "Pracovní volání") + " · " + ("BATCH" if estimate.get("batch") else "LIVE") + "</p>"
    html += table(("Model", "Vstupní tokeny", "Krátký výstup", "Střední výstup", "Dlouhý výstup"), rows)
    html += "<p>Scénáře jsou odhady délky výstupu. Skutečná cena se dopočítá z odpovědi API.</p>"
    if estimate.get("includes_trial"):
        html += "<p>Zkouška používá stejné zadání a je placená samostatně. Tabulka ukazuje pracovní část; nadpis zahrnuje i stejně dlouhou zkoušku. Již dokončená převzatá zkouška Batch se znovu neúčtuje.</p>"
    html += "<p>Horní mez pracovní operace: " + amount(estimate.get("maximum_usd")) + ". Dosud vyčísleno v běhu: " + amount(estimate.get("spent_usd", "0")) + ".</p>"
    for item in items:
        rates = item.get("rates")
        if rates:
            html += "<p>" + escape(item.get("model", "")) + ": sazby za milion tokenů — vstup " + amount(rates.get("input")) + ", výstup " + amount(rates.get("output")) + ".</p>"
        if item.get("reason"):
            html += "<p>" + escape(item["reason"]) + "</p>"
        if item.get("tools"):
            html += "<p>File search může přidat tokeny a poplatky za hledání; scénáře tyto budoucí položky nezahrnují.</p>"
    if estimate.get("fx") and known:
        fx = estimate["fx"]
        html += "<p>Orientačně " + amount(typical * (2 if estimate.get("includes_trial") else 1) * Decimal(fx["czk_per_usd"]), "CZK") + " · kurz ČNB " + escape(fx["date"]) + ".</p>"
    if estimate.get("error"):
        html += "<p><b>" + escape(estimate["error"]) + "</b></p>"
    return title, html


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
    result = "<h2>Spotřeba a cena</h2>" + table(
        ("Projekt / model", "Etapa", "Vstup / výstup tokenů", "USD"),
        [
            (
                f"{r.get('project', '')} / {r.get('model', '')}",
                r.get("flow_type", ""),
                f"{r.get('input_tokens', 0)} / {r.get('output_tokens', 0)}",
                amount(r.get("total_usd")),
            )
            for r in rows
        ],
    )
    for row in rows:
        usage, rates = row.get("usage_json") or {}, row.get("pricing_snapshot_json") or {}
        details = usage.get("input_tokens_details") or {}
        result += "<p>Cache: " + str(details.get("cached_tokens", 0)) + " tokenů; hledání v souborech: " + str(usage.get("_file_search_calls", "viz podklady")) + ".</p>"
        if rates:
            result += "<p>Sazby za milion tokenů: vstup " + amount(rates.get("input")) + ", výstup " + amount(rates.get("output")) + ". Zdroj: " + escape(rates.get("source", "")) + "; ověřeno " + escape(rates.get("verified_at", "")) + ".</p>"
        if row.get("total_usd") is None:
            result += "<p>" + escape(usage.get("_pricing_reason") or "Chybí sazba nebo úplné podklady spotřeby. Použijte Doplnit ceny ze spotřeby a LOG.") + "</p>"
        result += "<p>" + escape(row.get("notes", "")) + "</p>"
    return result
