from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_save_file

import json
import os
import time
from typing import List, Optional

from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QSizePolicy,
)

from ..core.pricing import PriceRow, PriceTable
from ..core.pricing_fetcher import PricingFetcher
from ..core.pricing_audit import PricingAuditor
from ..core.receipt import ReceiptDB
from ..core.openai_client import OpenAIClient
from ..core.retry import with_retry, CircuitBreaker
from .widgets import BusyPopup


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

        top = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_summary = QLabel("")
        self.lbl_audit = QLabel("")
        self.btn_refresh = QPushButton("Refresh (official URL)")
        self.btn_refresh_api = QPushButton("Fetch via GPT-4.1 (odhad)")
        self.btn_reload_receipts = QPushButton("Reload receipts")
        self.btn_audit = QPushButton("Audit LOG pricing")
        self.btn_export = QPushButton("Export receipts JSON")
        self.btn_delete = QPushButton("Delete selected receipts")
        top.addWidget(self.lbl_status)
        top.addWidget(self.lbl_summary)
        top.addWidget(self.lbl_audit)
        top.addStretch(1)
        for w in (
            self.btn_refresh,
            self.btn_refresh_api,
            self.btn_reload_receipts,
            self.btn_audit,
            self.btn_export,
            self.btn_delete,
        ):
            top.addWidget(w)
        v.addLayout(top)

        for lbl in (self.lbl_status, self.lbl_summary, self.lbl_audit):
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.tbl_prices = QTableWidget(0, 6)
        self.tbl_prices.setHorizontalHeaderLabels(["Model", "Input/1k", "Output/1k", "Batch In/1k", "Batch Out/1k", "Verified"])
        self.tbl_prices.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tbl_prices.horizontalHeader().setStretchLastSection(True)
        self.tbl_prices.horizontalHeader().resizeSection(0, 160)
        self.tbl_prices.horizontalHeader().resizeSection(1, 100)
        self.tbl_prices.horizontalHeader().resizeSection(2, 110)
        v.addWidget(QLabel("Price table"))
        v.addWidget(self.tbl_prices, 1)

        self.tbl_receipts = QTableWidget(0, 10)
        self.tbl_receipts.setHorizontalHeaderLabels(["ID", "Created", "Project", "Model", "Mode", "Flow", "InTok", "OutTok", "Total($)", "Verified"])
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
        v.addWidget(QLabel("Receipts (last 1000)"))
        v.addWidget(self.tbl_receipts, 2)

        self.btn_refresh.clicked.connect(self.on_refresh)
        self.btn_refresh_api.clicked.connect(self.on_refresh_via_model)
        self.btn_reload_receipts.clicked.connect(self.load_receipts)
        self.btn_audit.clicked.connect(self.on_audit)
        self.btn_export.clicked.connect(self.on_export)
        self.btn_delete.clicked.connect(self.on_delete)

        self.audit_worker: Optional[PricingAuditWorker] = None

        self.load_prices()
        self.load_receipts()

    # --- helpers ---
    def _log(self, msg: str):
        try:
            self.logline.emit(msg)
        except Exception:
            pass

    def set_api_key(self, api_key: str):
        self.api_key = api_key or ""

    def _set_status(self, detail: str = ""):
        ver = "VERIFIED" if self.pt.verified else "UNVERIFIED"
        ts = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.pt.last_updated or 0))
            if self.pt.last_updated
            else "n/a"
        )
        src = self.pt.last_fetch_source or "unknown source"
        text = f"Pricing [{self._shorten_source(src)}] {ver} | updated: {ts}"
        if detail:
            text += f" | {detail}"
        self.lbl_status.setText(text)

    def _shorten_source(self, src: str) -> str:
        if not src:
            return "unknown"
        if len(src) <= 80:
            return src
        return src[:38] + "…" + src[-38:]

    # --- UI loaders ---
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

            self.tbl_prices.setItem(i, 0, item(r.model))
            self.tbl_prices.setItem(i, 1, item(r.input_per_1k))
            self.tbl_prices.setItem(i, 2, item(r.output_per_1k))
            self.tbl_prices.setItem(i, 3, item("" if r.batch_input_per_1k is None else r.batch_input_per_1k))
            self.tbl_prices.setItem(i, 4, item("" if r.batch_output_per_1k is None else r.batch_output_per_1k))
            self.tbl_prices.setItem(i, 5, item("1" if self.pt.verified else "0"))

    def load_receipts(self):
        rows = self.db.query()
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
            self.tbl_receipts.setItem(i, 8, item(rget(r, "total_cost", 0.0)))
            self.tbl_receipts.setItem(i, 9, item(rget(r, "pricing_verified", 0)))

        self._update_summary(self.tbl_receipts.rowCount())

    def _update_summary(self, receipts_count: int):
        self.lbl_summary.setText(f"Models: {len(self.pt.rows)} | Receipts: {receipts_count}")

    # --- actions ---
    def on_refresh(self):
        self.btn_refresh.setEnabled(False)
        original_text = self.btn_refresh.text()
        self.btn_refresh.setText("Refreshing...")
        try:
            url = (self.s.pricing.source_url or "").strip()
            if not url:
                self._log("Pricing URL není nastavená. Použije se cache nebo fallback.")
                msg_warning(self, "Pricing", "Není nastavená URL pro ceník. Použije se cache nebo fallback.")
                self.load_prices("URL missing; using cache/fallback")
                return
            with BusyPopup(self, "Stahuji ceník z URL..."):
                ok, msg = self.pt.refresh_from_url(self.s.pricing.source_url)
            if not ok:
                self._log(f"Pricing refresh failed: {msg}")
                level = QMessageBox.Warning
                if "fallback" in msg.lower():
                    level = QMessageBox.Information
                msg_info(
                    self,
                    "Pricing",
                    f"Obnovení ceníku selhalo: {msg}\n\nPoužije se cache nebo builtin fallback.",
                ) if level == QMessageBox.Information else msg_warning(
                    self,
                    "Pricing",
                    f"Obnovení ceníku selhalo: {msg}\n\nPoužije se cache nebo builtin fallback.",
                )
            else:
                self._log("Pricing refreshed from URL.")
            detail = "OK" if ok else f"{msg}"
        finally:
            self.btn_refresh.setEnabled(True)
            self.btn_refresh.setText(original_text)
        self.load_prices(detail)

    def on_refresh_via_model(self):
        if not self.api_key:
            msg_warning(self, "Pricing", "Chybí OPENAI_API_KEY.")
            return
        self.btn_refresh_api.setEnabled(False)
        original_text = self.btn_refresh_api.text()
        self.btn_refresh_api.setText("Fetching...")
        client = OpenAIClient(self.api_key)
        try:
            with BusyPopup(self, "Dotazuji GPT-4.1 na ceník..."):
                resp = with_retry(
                    lambda: client.create_response(PricingFetcher.payload()),
                    self.s.retry,
                    self.breaker,
                )
                rows = PricingFetcher.parse_response(resp)
            if not rows:
                self._log("Pricing model output empty.")
                msg_warning(self, "Pricing", "Výstup nelze parsovat jako price table.")
                return
            self.pt.update_from_rows(rows, verified=False, source="GPT-4.1 estimate (unverified)")
            try:
                self.pt.save_cache()
            except Exception:
                pass
            self._log(f"Pricing updated via gpt-4.1 ({len(rows)} modelů)")
            detail = f"GPT fetched {len(rows)} models"
            self.load_prices(detail)
        except Exception as e:
            self._log(f"Pricing model fetch failed: {e}")
            msg_critical(self, "Pricing", f"Model fetch failed: {e}")
            self.load_prices("GPT fetch failed")
        finally:
            self.btn_refresh_api.setEnabled(True)
            self.btn_refresh_api.setText(original_text)

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
        self._set_audit_status("Audit running...")

    def _on_audit_done(self, summary: dict):
        self.audit_worker = None
        msg = (
            f"Audit DONE: runs={summary.get('runs_scanned')}, responses={summary.get('responses_seen')}, "
            f"inserted={summary.get('inserted')}, updated={summary.get('updated')}, zero_usage={summary.get('zero_usage')}"
        )
        self._log(msg)
        if summary.get("errors"):
            self._log(f"Audit warnings: {summary.get('errors')}")
        self._set_audit_status(msg)
        self.load_receipts()

    def _on_audit_error(self, err: str):
        self.audit_worker = None
        self._log(f"Audit failed: {err}")
        self._set_audit_status(f"Audit failed: {err}")

    def _set_audit_status(self, text: str):
        try:
            self.lbl_audit.setText(text)
        except Exception:
            pass

    def on_export(self):
        rows = self.db.query()
        data = self.db.export_rows(rows)
        fp, _ = dialog_save_file(self, "Export receipts", "receipts.json", "JSON (*.json)")
        if not fp:
            return
        with open(fp, "w", encoding="utf-8") as f:
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
