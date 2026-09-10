from __future__ import annotations
from .widgets import msg_info, dialog_save_file

import json
import os
import time
import csv
from decimal import Decimal
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QThread, QDate
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QSizePolicy,
    QLineEdit, QCheckBox, QFileDialog, QDialog, QTextBrowser, QDialogButtonBox, QInputDialog,
    QDateEdit, QComboBox, QSpinBox, QGroupBox, QFormLayout,
)

from ..core.pricing import PriceRow, PriceTable
from ..core.pricing_audit import PricingAuditor
from ..core.receipt import ReceiptDB
from ..core.retry import CircuitBreaker
from .layouts import FlowLayout, ContentTabs


class PricingPanel(QWidget):
    logline = Signal(str)

    def __init__(self, settings, price_table: PriceTable, receipt_db: ReceiptDB, parent=None):
        super().__init__(parent)
        self.s = settings
        self.pt = price_table
        self.db = receipt_db
        self.api_key: str = os.environ.get("OPENAI_API_KEY", "")
        self.breaker = CircuitBreaker(self.s.retry.circuit_breaker_failures, self.s.retry.circuit_breaker_cooldown_s)

        v = QVBoxLayout(self)

        intro = QLabel("Cena se počítá ze vstupních a výstupních tokenů. LIVE, Batch a zkušební volání mají vlastní položky. Částky jsou v USD; chybějící podklady nikdy neznamenají cenu nula.")
        intro.setWordWrap(True)
        v.addWidget(intro)
        calculator = QGroupBox("Rychlá kalkulace — bez volání API")
        form = QFormLayout(calculator)
        self.calc_model, self.calc_mode = QComboBox(), QComboBox()
        self.calc_mode.addItems(["LIVE · standardní", "BATCH", "LIVE · Flex", "LIVE · Priority / Fast"])
        self.calc_input, self.calc_output = QSpinBox(), QSpinBox()
        for field in (self.calc_input, self.calc_output):
            field.setRange(0, 2_000_000)
            field.setValue(1000)
            field.valueChanged.connect(self.update_calculation)
        self.calc_result = QLabel()
        self.calc_result.setWordWrap(True)
        self.calc_result.setStyleSheet("font-size: 16px; font-weight: 700;")
        for label, field in (("Model", self.calc_model), ("Způsob odeslání", self.calc_mode), ("Vstupních tokenů", self.calc_input), ("Výstupních tokenů", self.calc_output)):
            form.addRow(label, field)
        form.addRow(self.calc_result)
        self.calc_model.currentTextChanged.connect(self.update_calculation)
        self.calc_mode.currentIndexChanged.connect(self.update_calculation)
        v.addWidget(calculator)
        top = FlowLayout()
        self.lbl_status = QLabel("")
        self.lbl_summary = QLabel("")
        self.lbl_audit = QLabel("")
        self.btn_refresh = QPushButton("Obnovit oficiální ceník")
        self.btn_refresh_api = QPushButton("Import ceníku JSON…")
        self.btn_reload_receipts = QPushButton("Načíst účtenky")
        self.btn_audit = QPushButton("Doplnit ceny ze spotřeby a LOG")
        self.btn_export = QPushButton("Export JSON / CSV")
        self.btn_delete = QPushButton("Archivovat vybrané účtenky")
        self.btn_budgets = QPushButton("Rozpočty a rezervace…")
        self.btn_budgets.clicked.connect(self.show_budgets)
        v.addWidget(self.lbl_summary)
        v.addWidget(self.lbl_status)
        v.addWidget(self.lbl_audit)
        top.addStretch(1)
        for w in (
            self.btn_refresh,
            self.btn_refresh_api,
            self.btn_reload_receipts,
            self.btn_audit,
            self.btn_export,
            self.btn_delete,
            self.btn_budgets,
        ):
            top.addWidget(w)
        v.addLayout(top)
        filters = FlowLayout()
        self.filter_project, self.filter_run, self.filter_model = QLineEdit(), QLineEdit(), QLineEdit()
        for field, name in ((self.filter_project, "Projekt"), (self.filter_run, "Běh"), (self.filter_model, "Model")):
            field.setPlaceholderText(name + " (přesná hodnota)")
            field.editingFinished.connect(self.reset_page)
            filters.addWidget(field)
        self.filter_archived = QCheckBox("Archivované")
        self.filter_archived.toggled.connect(self.reset_page)
        filters.addWidget(self.filter_archived)
        self.previous_page, self.next_page = QPushButton("Předchozí"), QPushButton("Další")
        self.previous_page.clicked.connect(lambda: self.change_page(-1))
        self.next_page.clicked.connect(lambda: self.change_page(1))
        filters.addWidget(self.previous_page)
        filters.addWidget(self.next_page)
        v.addLayout(filters)
        self.page = 0
        date_filters = QHBoxLayout()
        self.filter_dates = QCheckBox("Období")
        self.date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.date_until = QDateEdit(QDate.currentDate())
        self.filter_status = QComboBox()
        self.filter_status.addItems(["Všechny ceny", "Vyčíslená cena", "Cena chybí"])
        for field in (self.filter_dates, self.date_from, self.date_until, self.filter_status):
            date_filters.addWidget(field)
        self.filter_dates.toggled.connect(self.reset_page)
        self.date_from.dateChanged.connect(self.reset_page)
        self.date_until.dateChanged.connect(self.reset_page)
        self.filter_status.currentIndexChanged.connect(self.reset_page)
        v.addLayout(date_filters)

        for lbl in (self.lbl_status, self.lbl_summary, self.lbl_audit):
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.tbl_prices = QTableWidget(0, 8)
        self.tbl_prices.setHorizontalHeaderLabels(["Model", "LIVE vstup", "LIVE výstup", "LIVE cache", "Batch vstup", "Batch výstup", "Batch cache", "Zdroj / ověřeno"])
        self.tbl_prices.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tbl_prices.horizontalHeader().setStretchLastSection(True)
        self.tbl_prices.horizontalHeader().resizeSection(0, 235)
        self.tbl_prices.horizontalHeader().resizeSection(1, 100)
        self.tbl_prices.horizontalHeader().resizeSection(2, 110)
        self.data_tabs = ContentTabs()
        self.data_tabs.addTab(self.tbl_prices, "Sazby USD za milion tokenů")

        self.tbl_receipts = QTableWidget(0, 10)
        self.tbl_receipts.setHorizontalHeaderLabels(["ID", "Datum", "Projekt", "Model", "Režim", "Etapa", "Vstupní tokeny", "Výstupní tokeny", "Cena USD", "Podklady"])
        hr = self.tbl_receipts.horizontalHeader()
        hr.setSectionResizeMode(QHeaderView.Interactive)
        hr.setStretchLastSection(True)
        hr.resizeSection(0, 60)
        hr.resizeSection(1, 150)
        hr.resizeSection(2, 140)
        hr.resizeSection(3, 120)
        hr.resizeSection(4, 80)
        hr.resizeSection(5, 80)
        self.tbl_receipts.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl_receipts.setSelectionMode(QTableWidget.MultiSelection)
        v.addWidget(QLabel("Účtenky – 100 záznamů na stránku; souhrn a export zahrnují celý filtr"))
        self.data_tabs.addTab(self.tbl_receipts, "Účtenky")
        self.data_tabs.setCurrentWidget(self.tbl_receipts if self.db.query(limit=1) else self.tbl_prices)
        v.addWidget(self.data_tabs, 1)

        self.btn_refresh.clicked.connect(self.on_refresh)
        self.btn_refresh_api.clicked.connect(self.on_refresh_via_model)
        self.btn_reload_receipts.clicked.connect(self.load_receipts)
        self.btn_audit.clicked.connect(self.on_audit)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_delete.clicked.connect(self.on_delete)
        self.tbl_receipts.cellDoubleClicked.connect(self.show_detail)

        self.audit_worker: Optional[PricingAuditWorker] = None

        self.load_prices()
        self.load_receipts()

    # Pomocné metody.
    def _log(self, msg: str):
        try:
            self.logline.emit(msg)
        except Exception:
            pass

    def set_api_key(self, api_key: str):
        self.api_key = api_key or ""

    def _set_status(self, detail: str = ""):
        ts = time.strftime("%d. %m. %Y %H:%M", time.localtime(self.pt.last_updated)) if self.pt.last_updated else "datum není známé"
        stale = self.pt.is_stale(self.s.pricing.cache_ttl_hours)
        text = f"Ceník: {len(self.pt.rows)} modelů · {self.pt.last_fetch_source or 'ruční sazby'} · {ts}"
        if stale:
            text += " · Starší sazby: doporučeno obnovit."
        if detail:
            text += " · " + detail
        self.lbl_status.setText(text)

    def _shorten_source(self, src: str) -> str:
        if not src:
            return "unknown"
        if len(src) <= 80:
            return src
        return src[:38] + "…" + src[-38:]

    # Načítání údajů do rozhraní.
    def load_prices(self, detail: str = ""):
        self._set_status(detail)
        rows: List[PriceRow] = list(self.pt.rows.values())
        rows.sort(key=lambda r: r.model)
        self.tbl_prices.setRowCount(len(rows))
        for i, r in enumerate(rows):
            def item(x):
                it = QTableWidgetItem(str(x))
                it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                return it

            from .receipt_view import amount
            live, batch = r.rates(), r.rates(True)
            values = [r.model, amount(live.input) if live else "Nedoloženo", amount(live.output) if live else "Nedoloženo",
                amount(live.cached) if live else "Nedoloženo", amount(batch.input) if batch else "Nedoloženo",
                amount(batch.output) if batch else "Nedoloženo", amount(batch.cached) if batch else "Nedoloženo",
                ("OpenAI · " + r.verified_at[:10]) if self.pt.is_verified(r.model) else "Ruční / neověřené"]
            for column, value in enumerate(values):
                cell = item(value)
                cell.setToolTip(r.source + (" · dlouhý kontext nad " + str(r.context_threshold) + " vstupních tokenů" if r.context_threshold else ""))
                self.tbl_prices.setItem(i, column, cell)
        current = self.calc_model.currentText()
        self.calc_model.blockSignals(True)
        self.calc_model.clear()
        from ..core.model_registry import model_ids, selectable
        self.calc_model.addItems(sorted(m for m in model_ids() if selectable(m)))
        self.calc_model.setCurrentText(current or "gpt-5.2")
        self.calc_model.blockSignals(False)
        self.update_calculation()

    def update_calculation(self, *_args):
        from ..core.cost_accounting import calculate
        from .receipt_view import amount
        model = self.calc_model.currentText()
        row = self.pt.get(model)
        batch = self.calc_mode.currentIndex() == 1
        tier = ("default", "default", "flex", "priority")[self.calc_mode.currentIndex()]
        rates = row.rates(batch, tier) if row else None
        if row:
            for index in range(self.tbl_prices.rowCount()):
                cell = self.tbl_prices.item(index, 0)
                if cell and cell.text() == row.model:
                    self.tbl_prices.selectRow(index)
                    self.tbl_prices.scrollToItem(cell)
                    break
        cost = calculate(rates, {"input_tokens": self.calc_input.value(), "output_tokens": self.calc_output.value()})
        if cost.total is None:
            self.calc_result.setText("Cenu nelze vyčíslit: " + cost.reason)
        else:
            self.calc_result.setText("Jedno volání: " + amount(cost.total) + " · se stejně dlouhou zkouškou: " + amount(cost.total * 2) +
                "\nVstup " + amount(cost.input) + " + výstup " + amount(cost.output) + ". Bez slevy za cache, nástrojů a regionálního příplatku.")

    def load_receipts(self):
        all_rows = self.db.query(**self.filters())
        rows = all_rows[self.page * 100:(self.page + 1) * 100]
        self.previous_page.setEnabled(self.page > 0)
        self.next_page.setEnabled((self.page + 1) * 100 < len(all_rows))
        self.tbl_receipts.setRowCount(len(rows))

        def rget(r, key, default=""):
            try:
                return r[key]
            except Exception:
                return getattr(r, "get", lambda k, d=default: d)(key, default)

        for i, r in enumerate(rows):
            def item(x):
                it = QTableWidgetItem(str(x))
                it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                return it

            self.tbl_receipts.setItem(i, 0, item(r["id"]))
            self.tbl_receipts.setItem(i, 1, item(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["created_at"]))))
            self.tbl_receipts.setItem(i, 2, item(rget(r, "project", "")))
            self.tbl_receipts.setItem(i, 3, item(rget(r, "model", "")))
            self.tbl_receipts.setItem(i, 4, item(rget(r, "mode", "")))
            self.tbl_receipts.setItem(i, 5, item(rget(r, "flow_type", "")))
            self.tbl_receipts.setItem(i, 6, item(rget(r, "input_tokens", 0)))
            self.tbl_receipts.setItem(i, 7, item(rget(r, "output_tokens", 0)))
            from .receipt_view import amount
            self.tbl_receipts.setItem(i, 8, item(amount(rget(r, "total_usd", None))))
            self.tbl_receipts.setItem(i, 9, item("Oficiální sazby" if rget(r, "pricing_verified", 0) else "Ruční / neúplné"))

        known = [Decimal(r["total_usd"]) for r in all_rows if r["total_usd"] is not None]
        from .receipt_view import amount
        self.lbl_summary.setText(f"Účtenky: {len(all_rows)} · Vyčíslená část: {amount(sum(known, Decimal(0)))} · Cena chybí: {len(all_rows) - len(known)} · Strana {self.page + 1}")

    def filters(self):
        return dict(project=self.filter_project.text().strip(), run_id=self.filter_run.text().strip(),
                    model=self.filter_model.text().strip(), archived=self.filter_archived.isChecked(),
                    since=self.date_from.date().startOfDay().toSecsSinceEpoch() if self.filter_dates.isChecked() else None,
                    until=self.date_until.date().addDays(1).startOfDay().toSecsSinceEpoch() - .000001 if self.filter_dates.isChecked() else None,
                    status=(None, "known", "unknown")[self.filter_status.currentIndex()])

    def reset_page(self):
        self.page = 0
        self.load_receipts()

    def change_page(self, delta):
        self.page = max(0, self.page + delta)
        self.load_receipts()

    def show_detail(self, row, column):
        rid = int(self.tbl_receipts.item(row, 0).text())
        selected = [r for r in self.db.query(**self.filters()) if r["id"] == rid]
        dialog = QDialog(self)
        dialog.setWindowTitle("Podklady účtenky")
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        layout.addWidget(text)
        from .receipt_view import detail_toggle, receipt_summary
        data = self.db.export_rows(selected)
        detail_toggle(layout, text, receipt_summary(data), data)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def on_import(self):
        from .widgets import msg_warning
        path, _ = QFileDialog.getOpenFileName(self, "Import ceníku (USD / 1000 tokenů)", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as stream:
                raw = json.load(stream)
            if raw.get("schema_version") != 2:
                raise ValueError("Import vyžaduje schema_version=2 a pole rows se sazbami *_per_1k.")
            rows = {}
            for value in raw["rows"]:
                row = PriceRow.from_dict(value)
                row.source, row.verified_at = "ruční import " + os.path.basename(path), ""
                rows[row.model] = row
            self.pt.update_from_rows(rows, verified=False, source="ruční import")
            self.load_prices()
        except (ValueError, KeyError, TypeError, OSError) as exc:
            msg_warning(self, "Import ceníku", str(exc))

    def show_budgets(self):
        from ..core.cost_accounting import CostLedger
        from .widgets import msg_warning
        ledger = CostLedger(self.db.db_path)
        dialog = QDialog(self)
        dialog.setWindowTitle("Rozpočty a rezervace")
        dialog.resize(850, 550)
        layout = QVBoxLayout(dialog)
        text = QTextBrowser()
        with ledger.connect() as con:
            scopes = [dict(r) for r in con.execute("SELECT * FROM cost_scopes")]
        layout.addWidget(text)
        from .receipt_view import detail_toggle, table
        data = [{**scope, "operations": ledger.operations(scope["id"])} for scope in scopes]
        rows = []
        for scope in data:
            operations = scope["operations"]
            actual = sum((Decimal(op["actual_usd"]) for op in operations if op["actual_usd"] is not None), Decimal(0))
            pending = [op for op in operations if op["status"] not in ("settled", "released")]
            reserved = sum((Decimal(op["reserved_usd"]) for op in pending if op["reserved_usd"] is not None), Decimal(0))
            rows.append((scope["id"], scope["limit_usd"] or "Bez limitu", actual, reserved, len(pending)))
        detail_toggle(layout, text, "<h2>Rozpočty běhů</h2>" + table(
            ("Běh", "Limit USD", "Vyčísleno USD", "Doložené rezervace USD", "Nevyřešené operace"), rows), data)
        edit = QPushButton("Změnit limit běhu…")
        def change():
            scope, ok = QInputDialog.getItem(dialog, "Rozpočet", "Běh", [s["id"] for s in scopes], editable=False)
            if not ok or not scope:
                return
            value, ok = QInputDialog.getText(dialog, "Limit USD", "Nový limit; prázdné = bez limitu")
            if ok:
                try:
                    ledger.set_limit(scope, value.strip() or None)
                    dialog.accept()
                except ValueError as exc:
                    msg_warning(dialog, "Rozpočet", str(exc))
        edit.clicked.connect(change)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _update_summary(self, receipts_count: int):
        self.lbl_summary.setText(f"Models: {len(self.pt.rows)} | Receipts: {receipts_count}")

    # Uživatelské akce.
    def on_refresh(self):
        if getattr(self, 'refresh_worker', None) and self.refresh_worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.refresh_worker = PriceRefreshWorker(self.pt, self.s.pricing.source_url, self)
        self.refresh_worker.done.connect(self._refresh_done)
        self.refresh_worker.start()

    def _refresh_done(self, result):
        self.btn_refresh.setEnabled(True)
        self.load_prices(result[1])
        self.start_audit(quiet=True)

    def on_refresh_via_model(self):
        self.on_import()

    def on_audit(self):
        self.start_audit(quiet=False)

    def start_audit(self, quiet: bool = True):
        if self.audit_worker and self.audit_worker.isRunning():
            if not quiet:
                self._log("Pricing audit already running.")
            return
        self.audit_worker = PricingAuditWorker(self.s, self.pt, self.db, self.api_key, quiet=quiet)
        self.audit_worker.logline.connect(self._log)
        self.audit_worker.finished_ok.connect(self._on_audit_done)
        self.audit_worker.finished_err.connect(self._on_audit_error)
        self.audit_worker.start()
        self._set_audit_status("Doplňuji ceny podle uložené spotřeby…")

    def _on_audit_done(self, summary: dict):
        if self.audit_worker:
            self.audit_worker.wait()
            self.audit_worker.deleteLater()
        self.audit_worker = None
        msg = (
            f"Hotovo: projito běhů {summary.get('runs_scanned')}, odpovědí {summary.get('responses_seen')}, "
            f"nových účtenek {summary.get('inserted')}, doplněných cen {summary.get('updated')}."
        )
        self._log(msg)
        if summary.get("errors"):
            self._log(f"Audit warnings: {summary.get('errors')}")
        self._set_audit_status(msg)
        self.load_prices()
        self.load_receipts()

    def _on_audit_error(self, err: str):
        if self.audit_worker:
            self.audit_worker.wait()
            self.audit_worker.deleteLater()
        self.audit_worker = None
        self._log(f"Doplnění cen selhalo: {err}")
        self._set_audit_status(f"Doplnění cen selhalo: {err}")

    def _set_audit_status(self, text: str):
        try:
            self.lbl_audit.setText(text)
        except Exception:
            pass

    def on_export(self):
        rows = self.db.query(**self.filters())
        data = self.db.export_rows(rows)
        fp, _ = dialog_save_file(self, "Export účtenek", "receipts.json", "JSON (*.json);;CSV (*.csv)")
        if not fp:
            return
        with open(fp, "w", encoding="utf-8", newline="") as f:
            if fp.lower().endswith(".csv"):
                writer = csv.DictWriter(f, fieldnames=list(data[0]) if data else ["id"])
                writer.writeheader()
                for row in data:
                    writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
            else:
                json.dump(data, f, ensure_ascii=False, indent=2)
        msg_info(self, "Export", f"Uloženo: {fp}")

    def on_delete(self):
        sel = self.tbl_receipts.selectionModel().selectedRows()
        ids = []
        for idx in sel:
            try:
                rid = int(self.tbl_receipts.item(idx.row(), 0).text())
                ids.append(rid)
            except Exception:
                pass
        if not ids:
            msg_info(self, "Delete", "Nic nevybráno.")
            return
        self.db.delete_ids(ids)
        self.load_receipts()


class PriceRefreshWorker(QThread):
    done = Signal(object)

    def __init__(self, table, url, parent=None):
        super().__init__(parent)
        self.table, self.url = table, url

    def run(self):
        self.done.emit(self.table.refresh_from_url(self.url))


class PricingAuditWorker(QThread):
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)

    def __init__(self, settings, price_table: PriceTable, db: ReceiptDB, api_key: str, quiet: bool = True, parent=None):
        super().__init__(parent)
        self.s = settings
        self.pt = price_table
        self.db = db
        self.api_key = api_key or ""
        self.quiet = quiet

    def _emit(self, msg: str):
        if not self.quiet:
            try:
                self.logline.emit(msg)
            except Exception:
                pass

    def run(self):
        try:
            auditor = PricingAuditor(self.s, self.pt, self.db, api_key=self.api_key, log_fn=self._emit)
            summary = auditor.audit()
            self.finished_ok.emit(summary)
        except Exception as exc:
            self.finished_err.emit(str(exc))
