"""Kalkulačka, sazby a účetní doklady v oddělených pracovních pohledech."""

import csv
import json
from html import escape
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from PySide6.QtWidgets import QWidget, QTabWidget, QTableWidgetItem, QCheckBox, QDateEdit
from PySide6.QtCore import QDate, Signal
from ..core.cost_accounting import calculate, CostLedger, money
from ..core.pricing import PriceRow
from ..core.pricing_audit import PricingAuditor
from .design import column, card, label, row, button, combo, text, number, form, table, scroll
from .dialogs import (
    DetailDialog,
    msg_info,
    msg_warning,
    dialog_open_file,
    dialog_save_file,
    dialog_input_text,
)
from .finance import amount, table as html_table
from .jobs import Jobs


class PricingPanel(QWidget):
    logline = Signal(str)

    def __init__(self, settings, price_table, receipt_db, parent=None):
        super().__init__(parent)
        self.s, self.pt, self.db = settings, price_table, receipt_db
        self.api_key = ""
        self.jobs = Jobs(self)
        self.page = 0
        self.audit_worker = self.refresh_worker = None
        layout = column(self)
        self.data_tabs = QTabWidget()
        layout.addWidget(self.data_tabs, 1)
        calc = QWidget()
        cl = column(calc, 16)
        box, bl = card(
            "Rychlá kalkulace",
            "Zobrazí cenu zadané spotřeby bez volání API. Před odesláním se zvlášť spočítá skutečný připravený vstup.",
        )
        self.calc_result = label("", "Metric")
        bl.addWidget(self.calc_result)
        fields = form(bl)
        self.calc_model = combo()
        self.calc_mode = combo(
            ["LIVE · standardní", "BATCH", "LIVE · Flex", "LIVE · Priority / Fast"]
        )
        self.calc_input, self.calc_output = number(1000), number(1000)
        for name, field in (
            ("Model", self.calc_model),
            ("Způsob odeslání", self.calc_mode),
            ("Vstupní tokeny", self.calc_input),
            ("Výstupní tokeny", self.calc_output),
        ):
            fields.addRow(name, field)
        self.calc_explanation = label("", "Hint")
        bl.addWidget(self.calc_explanation)
        cl.addWidget(box)
        cl.addWidget(
            label(
                "Cena = vstupní tokeny × sazba vstupu + výstupní tokeny × sazba výstupu. Sazby jsou za milion tokenů. Cache a skutečné nástroje se dopočítají v účtence. BATCH používá vlastní sazby; příprava GENERATE A1/A2 zůstává LIVE.",
                "Hint",
            )
        )
        cl.addStretch()
        self.data_tabs.addTab(scroll(calc), "Kalkulačka")
        rates = QWidget()
        rl = column(rates, 16)
        self.lbl_status = label("", "Hint")
        rl.addWidget(self.lbl_status)
        rl.addWidget(
            row(
                button("Obnovit oficiální ceník", self.on_refresh),
                button("Import JSON", self.on_import),
            )
        )
        self.tbl_prices = table(
            [
                "Model",
                "LIVE vstup",
                "LIVE výstup",
                "LIVE cache",
                "BATCH vstup",
                "BATCH výstup",
                "BATCH cache",
                "Zdroj / ověřeno",
            ]
        )
        rl.addWidget(label("Všechny sazby v USD za milion tokenů"))
        rl.addWidget(self.tbl_prices, 1)
        self.data_tabs.addTab(rates, "Sazby")
        receipts = QWidget()
        rr = column(receipts, 16)
        self.lbl_summary = label("", "Metric")
        self.lbl_audit = label("", "Hint")
        rr.addWidget(self.lbl_summary)
        rr.addWidget(self.lbl_audit)
        self.filter_project, self.filter_run, self.filter_model = (
            text(placeholder="Přesný projekt"),
            text(placeholder="Přesný RUN ID"),
            text(placeholder="Přesný model"),
        )
        rr.addWidget(row(self.filter_project, self.filter_run, self.filter_model))
        self.filter_archived = QCheckBox("Archivované")
        self.filter_dates = QCheckBox("Období")
        self.date_from, self.date_until = (
            QDateEdit(QDate.currentDate().addMonths(-1)),
            QDateEdit(QDate.currentDate()),
        )
        for field in (self.date_from, self.date_until):
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd.MM.yyyy")
        self.filter_status = combo(["Všechny ceny", "Vyčíslená cena", "Cena chybí"])
        rr.addWidget(
            row(
                self.filter_dates,
                self.date_from,
                self.date_until,
                self.filter_archived,
                self.filter_status,
            )
        )
        rr.addWidget(
            row(
                button("Načíst účtenky", self.reset_page),
                button("Doplnit chybějící ceny", self.on_audit),
                button("Rozpočty", self.show_budgets),
            )
        )
        self.tbl_receipts = table(
            [
                "ID",
                "Datum",
                "Projekt",
                "Model",
                "Režim",
                "Etapa",
                "Vstup",
                "Výstup",
                "Cena USD",
                "Podklady",
            ]
        )
        self.tbl_receipts.cellDoubleClicked.connect(self.show_detail)
        rr.addWidget(self.tbl_receipts, 1)
        self.previous_page = button("Předchozí", lambda: self.change_page(-1))
        self.next_page = button("Další", lambda: self.change_page(1))
        self.page_label = label()
        rr.addWidget(row(self.previous_page, self.page_label, self.next_page))
        rr.addWidget(
            row(
                button("Detail vybrané účtenky", self.show_detail),
                button("Export JSON / CSV", self.on_export),
                button("Archivovat vybrané", self.on_delete),
            )
        )
        self.data_tabs.addTab(receipts, "Účtenky")
        for field in (self.calc_input, self.calc_output):
            field.valueChanged.connect(self.update_calculation)
        self.calc_model.currentTextChanged.connect(self.update_calculation)
        self.calc_mode.currentIndexChanged.connect(self.update_calculation)
        for field in (self.filter_project, self.filter_run, self.filter_model):
            field.editingFinished.connect(self.reset_page)
        self.filter_archived.toggled.connect(self.reset_page)
        self.filter_dates.toggled.connect(self.reset_page)
        self.filter_status.currentIndexChanged.connect(self.reset_page)
        self.load_prices()
        self.load_receipts()

    def set_api_key(self, key):
        self.api_key = key

    def load_prices(self, detail=""):
        selected = self.calc_model.currentText()
        self.calc_model.blockSignals(True)
        self.calc_model.clear()
        self.calc_model.addItems(sorted(self.pt.rows))
        if selected:
            self.calc_model.setCurrentText(selected)
        elif self.calc_model.findText("gpt-4.1") >= 0:
            self.calc_model.setCurrentText("gpt-4.1")
        self.calc_model.blockSignals(False)
        records = sorted(self.pt.rows.values(), key=lambda entry: entry.model)
        self.tbl_prices.setRowCount(len(records))
        for index, record in enumerate(records):
            live, batch = record.rates(), record.rates(True)
            values = [
                record.model,
                *[
                    amount(getattr(rates, field)) if rates else "Nedoloženo"
                    for rates in (live, batch)
                    for field in ("input", "output", "cached")
                ],
                record.source + " · " + (record.verified_at or "neověřeno"),
            ]
            for col, value in enumerate(values):
                self.tbl_prices.setItem(index, col, QTableWidgetItem(value))
        self.tbl_prices.setColumnWidth(0, 220)
        self.lbl_status.setText(
            f"{len(records)} modelů · {self.pt.last_fetch_source or 'Uložený ceník'} · "
            + (
                "Starší sazby; obnovte ceník."
                if self.pt.is_stale(self.s.pricing.cache_ttl_hours)
                else "Sazby jsou aktuální."
            )
            + (" " + str(detail) if detail else "")
        )
        self.update_calculation()

    def update_calculation(self):
        record = self.pt.get(self.calc_model.currentText())
        mode = self.calc_mode.currentIndex()
        rates = record.rates(mode == 1, {2: "flex", 3: "priority"}.get(mode)) if record else None
        if not rates:
            self.calc_result.setText("Cena není doložena")
            self.calc_explanation.setText(
                "Pro tuto kombinaci modelu a způsobu odeslání chybí doložená sazba. Vyberte jiný režim nebo obnovte ceník."
            )
            return
        usage = dict(input_tokens=self.calc_input.value(), output_tokens=self.calc_output.value())
        result = calculate(rates, usage)
        self.calc_result.setText(amount(result.total))
        self.calc_explanation.setText(
            f"{usage['input_tokens']:,} vstupních + {usage['output_tokens']:,} výstupních tokenů.\nSe stejně dlouhou samostatnou zkouškou: {amount(result.total * 2) if result.total is not None else 'Nelze vyčíslit'}.\nSazba vstupu {amount(rates.input)}, výstupu {amount(rates.output)} / milion. Zdroj: {rates.source}\nBez dodatečných nástrojů, úložiště a regionálního příplatku."
        )

    def filters(self):
        values = dict(
            project=self.filter_project.text().strip() or None,
            run_id=self.filter_run.text().strip() or None,
            model=self.filter_model.text().strip() or None,
            archived=self.filter_archived.isChecked(),
        )
        if self.filter_dates.isChecked():
            values["since"] = datetime.combine(
                self.date_from.date().toPython(), datetime.min.time()
            ).timestamp()
            values["until"] = datetime.combine(
                self.date_until.date().toPython() + timedelta(days=1), datetime.min.time()
            ).timestamp()
        status = self.filter_status.currentIndex()
        if status:
            values["status"] = "known" if status == 1 else "unknown"
        return values

    def reset_page(self):
        self.page = 0
        self.load_receipts()

    def change_page(self, delta):
        self.page = max(0, self.page + delta)
        self.load_receipts()

    def load_receipts(self):
        records = self.db.query(**self.filters())
        total = sum(
            (money(record["total_usd"]) for record in records if record["total_usd"] is not None),
            Decimal(0),
        )
        unknown = sum(record["total_usd"] is None for record in records)
        self.lbl_summary.setText(
            amount(total) + f" · {len(records)} dokladů · {unknown} nevyčíslených"
        )
        self.page = min(self.page, max(0, (len(records) - 1) // 100))
        shown = records[self.page * 100 : (self.page + 1) * 100]
        self.tbl_receipts.setRowCount(len(shown))
        for index, record in enumerate(shown):
            values = [
                record["id"],
                datetime.fromtimestamp(record["created_at"]).strftime("%d.%m.%Y %H:%M"),
                record["project"],
                record["model"],
                record["mode"],
                record["flow_type"],
                record["input_tokens"],
                record["output_tokens"],
                amount(record["total_usd"]),
                record["notes"],
            ]
            for col, value in enumerate(values):
                self.tbl_receipts.setItem(index, col, QTableWidgetItem(str(value)))
        self.previous_page.setEnabled(self.page > 0)
        self.next_page.setEnabled((self.page + 1) * 100 < len(records))
        self.page_label.setText(f"Strana {self.page + 1} · souhrn a export zahrnují celý filtr")

    def show_detail(self, *_):
        selected = self.tbl_receipts.selectionModel().selectedRows()
        ids = {int(self.tbl_receipts.item(index.row(), 0).text()) for index in selected}
        records = [
            record
            for record in self.db.export_rows(self.db.query(**self.filters()))
            if record["id"] in ids
        ]
        if not records:
            msg_info(self, "Účtenka", "Vyberte účtenku v tabulce.")
            return
        dialog = DetailDialog("Spotřeba a cena", "", self, records)
        content = html_table(
            ("Model", "Etapa", "Vstup / výstup", "Cena"),
            [
                (
                    r["model"],
                    r["flow_type"],
                    f"{r['input_tokens']} / {r['output_tokens']}",
                    amount(r["total_usd"]),
                )
                for r in records
            ],
        )
        content += "<p>Cena vychází ze skutečné spotřeby API a uložených sazeb. Reasoning je součástí výstupu, úložiště se účtuje průběžně. Nejde o fakturu OpenAI.</p>"
        for record in records:
            usage = record.get("usage_json") or {}
            rates = record.get("pricing_snapshot_json") or {}
            cache = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
            content += (
                "<p>Cache: "
                + str(cache)
                + " tokenů · Hledání v souborech: "
                + escape(str(usage.get("_file_search_calls", "viz podklady")))
                + " volání.</p>"
            )
            if rates:
                content += (
                    "<p>Sazby za milion tokenů: vstup "
                    + amount(rates.get("input"))
                    + ", výstup "
                    + amount(rates.get("output"))
                    + ". Zdroj: "
                    + escape(rates.get("source", ""))
                    + " · ověřeno "
                    + escape(rates.get("verified_at", ""))
                    + ".</p>"
                )
            if record.get("total_usd") is None:
                content += (
                    "<p>"
                    + escape(
                        usage.get("_pricing_reason")
                        or "Chybí sazby nebo úplná spotřeba; použijte Doplnit chybějící ceny."
                    )
                    + "</p>"
                )
            content += "<p>" + escape(record.get("notes", "")) + "</p>"
        dialog.browser.setHtml(content)
        dialog.exec()

    def on_refresh(self):
        if self.refresh_worker and self.refresh_worker.isRunning():
            return

        def done(result):
            self.load_prices(str(result))
            self.refresh_worker = None
            self.start_audit(True)

        self.refresh_worker = self.jobs.start(
            "Obnova ceníku", lambda job: self.pt.refresh_from_url(self.s.pricing.source_url), done
        )
        self.refresh_worker.finished.connect(lambda: setattr(self, "refresh_worker", None))

    def on_import(self):
        path, _ = dialog_open_file(
            self, "Import ceníku (sazby USD / 1000 tokenů)", filters="JSON (*.json)"
        )
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            if raw.get("schema_version") != 2:
                raise ValueError("Vyžadováno schema_version=2 a rows se sazbami *_per_1k.")
            records = {}
            for entry in raw["rows"]:
                record = PriceRow.from_dict(entry)
                record.source, record.verified_at = "ruční import " + Path(path).name, ""
                records[record.model] = record
            self.pt.update_from_rows(records, verified=False, source="ruční import")
            self.load_prices()
        except Exception as exc:
            msg_warning(self, "Import ceníku", str(exc))

    def on_audit(self):
        self.start_audit(False)

    def start_audit(self, quiet=True):
        if self.audit_worker and self.audit_worker.isRunning():
            return

        def execute(job):
            return PricingAuditor(
                self.s, self.pt, self.db, api_key=self.api_key, log_fn=job.logline.emit
            ).audit()

        def done(result):
            self.audit_worker = None
            self.lbl_audit.setText(
                f"Prošlo běhů {result.get('runs_scanned', 0)} · doplněno cen {result.get('updated', 0)} · nových dokladů {result.get('inserted', 0)}"
            )
            self.load_prices()
            self.load_receipts()

        self.audit_worker = self.jobs.start("Doplnění cen", execute, done, popup=not quiet)
        self.audit_worker.finished.connect(lambda: setattr(self, "audit_worker", None))

    def on_export(self):
        path, _ = dialog_save_file(
            self, "Export účtenek", "receipts.json", "JSON (*.json);;CSV (*.csv)"
        )
        if not path:
            return
        records = self.db.export_rows(self.db.query(**self.filters()))
        try:
            with open(path, "w", encoding="utf-8", newline="") as stream:
                if path.lower().endswith(".csv"):
                    writer = csv.DictWriter(stream, list(records[0]) if records else ["id"])
                    writer.writeheader()
                    writer.writerows(
                        {
                            key: json.dumps(value, ensure_ascii=False)
                            if isinstance(value, (dict, list))
                            else value
                            for key, value in record.items()
                        }
                        for record in records
                    )
                else:
                    json.dump(records, stream, ensure_ascii=False, indent=2)
            msg_info(self, "Export uložen", path)
        except OSError as exc:
            msg_warning(self, "Export", str(exc))

    def on_delete(self):
        ids = [
            int(self.tbl_receipts.item(index.row(), 0).text())
            for index in self.tbl_receipts.selectionModel().selectedRows()
        ]
        if ids:
            self.db.delete_ids(ids)
            self.load_receipts()
        else:
            msg_info(self, "Archivace", "Vyberte účtenky v tabulce.")

    def show_budgets(self):
        ledger = CostLedger(self.db.db_path)
        with ledger.connect() as connection:
            scopes = [dict(record) for record in connection.execute("SELECT * FROM cost_scopes")]
        records = [dict(scope, operations=ledger.operations(scope["id"])) for scope in scopes]
        dialog = DetailDialog("Rozpočty a rezervace", "", self, records)
        rows = []
        for scope in records:
            operations = scope["operations"]
            actual = sum(
                (money(op["actual_usd"]) for op in operations if op["actual_usd"] is not None),
                Decimal(0),
            )
            pending = [op for op in operations if op["status"] not in ("settled", "released")]
            reserved = sum(
                (money(op["reserved_usd"]) for op in pending if op["reserved_usd"] is not None),
                Decimal(0),
            )
            rows.append(
                (
                    scope["id"],
                    amount(scope["limit_usd"]) if scope["limit_usd"] is not None else "Bez limitu",
                    amount(actual),
                    amount(reserved),
                    len(pending),
                )
            )
        dialog.browser.setHtml(
            html_table(("Běh", "Limit", "Vyčísleno", "Rezervace", "Neuzavřené"), rows)
        )

        def change():
            scope, ok = dialog_input_text(dialog, "Rozpočet", "Přesný RUN ID:")
            if not ok or scope not in {record["id"] for record in scopes}:
                return
            value, ok = dialog_input_text(dialog, "Limit USD", "Nový limit; prázdné = bez limitu")
            if ok:
                try:
                    ledger.set_limit(scope, value.strip() or None)
                    dialog.accept()
                except ValueError as exc:
                    msg_warning(dialog, "Rozpočet", str(exc))

        dialog.layout().insertWidget(2, button("Změnit limit běhu", change))
        dialog.exec()
