from __future__ import annotations
from .widgets import msg_info, msg_warning, msg_critical, msg_question, dialog_save_file

import os, time, json
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QMessageBox
from PySide6.QtCore import Qt

from ..core.openai_client import OpenAIClient
from ..core.retry import with_retry, CircuitBreaker

class BatchMonitorWindow(QWidget):
    def __init__(self, settings, api_key: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch monitor")
        self.resize(980, 520)
        self.s = settings
        self.api_key = api_key
        self.client = OpenAIClient(api_key) if api_key else None
        self.breaker = CircuitBreaker(settings.retry.circuit_breaker_failures, settings.retry.circuit_breaker_cooldown_s)

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.lbl = QLabel("Batches (OpenAI)")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_download = QPushButton("Download output")
        self.btn_cancel = QPushButton("Cancel batch")
        top.addWidget(self.lbl)
        top.addStretch(1)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_download)
        top.addWidget(self.btn_cancel)
        v.addLayout(top)

        self.tbl = QTableWidget(0, 7)
        self.tbl.setHorizontalHeaderLabels(["id","status","created_at","endpoint","input_file_id","output_file_id","errors"])
        self.tbl.setSelectionBehavior(QTableWidget.SelectRows)
        self.tbl.setSelectionMode(QTableWidget.SingleSelection)
        v.addWidget(self.tbl, 1)

        self.btn_refresh.clicked.connect(self.load)
        self.btn_download.clicked.connect(self.download)
        self.btn_cancel.clicked.connect(self.cancel)

        self.load()

    def _need_client(self) -> bool:
        if not self.api_key:
            msg_warning(self, "Batch", "Chybí OPENAI_API_KEY.")
            return False
        if self.client is None:
            self.client = OpenAIClient(self.api_key)
        return True

    def load(self):
        if not self._need_client():
            return
        try:
            batches = with_retry(lambda: self.client.list_batches(), self.s.retry, self.breaker)
        except Exception as e:
            msg_critical(self, "Batch", str(e))
            return

        self.tbl.setRowCount(len(batches))
        for i, b in enumerate(batches):
            def item(x):
                it = QTableWidgetItem(str(x))
                it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                return it
            self.tbl.setItem(i,0,item(b.get("id","")))
            self.tbl.setItem(i,1,item(b.get("status","")))
            ca = b.get("created_at")
            self.tbl.setItem(i,2,item(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ca)) if isinstance(ca,(int,float)) else ca))
            self.tbl.setItem(i,3,item(b.get("endpoint","")))
            self.tbl.setItem(i,4,item(b.get("input_file_id","")))
            self.tbl.setItem(i,5,item(b.get("output_file_id","")))
            self.tbl.setItem(i,6,item(b.get("error","") or b.get("errors","")))
        self.tbl.resizeColumnsToContents()

    def _selected_batch(self):
        sel = self.tbl.selectionModel().selectedRows()
        if not sel:
            return None
        row = sel[0].row()
        bid = self.tbl.item(row,0).text()
        ofid = self.tbl.item(row,5).text()
        return bid, ofid

    def download(self):
        if not self._need_client():
            return
        sb = self._selected_batch()
        if not sb:
            msg_info(self, "Batch", "Vyber batch.")
            return
        bid, ofid = sb
        if not ofid:
            msg_info(self, "Batch", "Batch nemá output_file_id.")
            return
        fp, _ = dialog_save_file(self, "Save output JSONL", f"batch_{bid}.jsonl", "JSONL (*.jsonl)")
        if not fp:
            return
        try:
            raw = with_retry(lambda: self.client.file_content(ofid), self.s.retry, self.breaker)
            with open(fp, "wb") as f:
                f.write(raw)
            msg_info(self, "Batch", f"Uloženo: {fp}")
        except Exception as e:
            msg_critical(self, "Batch", str(e))

    def cancel(self):
        if not self._need_client():
            return
        sb = self._selected_batch()
        if not sb:
            msg_info(self, "Batch", "Vyber batch.")
            return
        bid, _ = sb
        try:
            with_retry(lambda: self.client.cancel_batch(bid), self.s.retry, self.breaker)
            self.load()
        except Exception as e:
            msg_critical(self, "Batch", str(e))
