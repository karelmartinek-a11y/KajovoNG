"""Oddělený stav vzdálených dávek a bezpečného místního převzetí."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QHeaderView, QTableWidget, QTableWidgetItem, QWidget

from kajovo.core.batch_completion import CANCELLABLE, batch_ids, complete_saved_batch, pending_batch_ids, read_state
from kajovo.core.generate_batch import repeat_saved_batch
from .components import DetailDialog, action, actions, caption, confirm, vertical
from .resources import ValueDialog
from .evidence import VALUES


class BatchesPage(QWidget):
    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.records = []
        self.busy = False
        self.poll_started = 0
        self.last_refresh = None
        root = vertical(self)
        root.addWidget(caption("Dávky a převzetí výsledků", "section"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Projekt / dávka", "Stav služby", "Zpracované úlohy", "Místní výsledek", "Poslední kontrola"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.setAccessibleName("Pracovní dávky")
        root.addWidget(self.table, 1)
        self.notice = caption("Načtěte dávky; stav služby sám nepotvrzuje uložení souborů.", "muted")
        root.addWidget(self.notice)
        self.buttons = [action("batches.refresh", "Obnovit", self.refresh),
                        action("batches.complete", "Převzít výsledky", self.complete, "primary"),
                        action("batches.cancel", "Zrušit dávku", self.cancel, "danger"),
                        action("batches.repeat", "Opakovat vybrané soubory", self.repeat),
                        action("batches.repair", "Opravit s připomínkou", lambda: self.repeat(repair=True)),
                        action("batches.raw", "Stáhnout původní výsledky", self.download_raw),
                        action("batches.details", "Podrobnosti", self.details)]
        root.addWidget(actions(*self.buttons[:4]))
        root.addWidget(actions(*self.buttons[4:]))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.refresh(automatic=True))
        context.key_changed.connect(self.reset)

    def reset(self):
        self.timer.stop()
        self.records = []
        self.table.setRowCount(0)
        self.poll_started = 0

    def selected(self):
        row = self.table.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def execute(self, title, function, receive=None, popup=True, output_dir=None, identifier=None):
        if self.busy:
            return
        try:
            client = self.context.client()
            self.context.operations.assert_output_available(output_dir)
        except ValueError as error:
            self.notice.setText(str(error))
            return
        self.busy = True
        for widget in self.buttons:
            widget.setEnabled(False)
        record = self.context.operations.start(title, lambda task: function(client, task), receive, popup=popup, output_dir=output_dir, identifier=identifier)

        def release():
            self.busy = False
            for widget in self.buttons:
                widget.setEnabled(True)

        record.worker.finished.connect(release)
        return record

    def refresh(self, checked=False, automatic=False):
        if not automatic:
            self.poll_started = time.monotonic()
        if automatic and time.monotonic() - self.poll_started >= self.context.settings.batch_timeout_s:
            self.notice.setText("Místní sledování dosáhlo časového limitu; vzdálené dávky pokračují a lze je znovu obnovit.")
            return
        key = self.context.api_key
        root = Path(self.context.settings.log_dir)

        def execute(client, task):
            remote = {row["id"]: row for row in client.list_batches() if row.get("id")}
            records = []
            if root.exists():
                for directory in root.iterdir():
                    if not directory.is_dir() or not directory.name.startswith("RUN_"):
                        continue
                    state = read_state(directory)
                    for identifier in batch_ids(state):
                        records.append({"id": identifier, "remote": remote.pop(identifier, {}), "state": state, "run_dir": str(directory)})
            records.extend({"id": identifier, "remote": value, "state": {}, "run_dir": ""} for identifier, value in remote.items())
            return records

        def receive(records):
            if key != self.context.api_key:
                return
            self.records = records
            self.last_refresh = time.time()
            self.render()
            if any(record["remote"].get("status") in CANCELLABLE for record in records):
                interval = self.context.settings.batch_poll_interval_s
                self.timer.start(max(1, int(interval * 1000)))
                self.notice.setText(f"Poslední kontrola {time.strftime('%H:%M:%S')} · další za {interval:g} sekund.")
            else:
                self.notice.setText("Dostupné stavy byly aktualizovány; převzetí výsledků je samostatná akce.")

        self.execute("Ověření pracovních dávek", execute, receive, popup=not automatic, identifier="batches.monitor")

    def render(self):
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            remote, state = record["remote"], record["state"]
            counts = remote.get("request_counts") or {}
            completed = counts.get("completed", 0)
            failed = counts.get("failed", 0)
            total = counts.get("total", 0)
            local = "Podklady nejsou v tomto počítači" if not record["run_dir"] else ("Čeká na převzetí" if record["id"] in pending_batch_ids(state) else VALUES.get(state.get("status"), "Není evidováno"))
            remote_status = "Zpracováno službou" if remote.get("status") == "completed" else VALUES.get(remote.get("status"), "Stav není dostupný")
            values = [str(state.get("project") or "") + "\n" + record["id"], remote_status,
                      f"{completed + failed} z {total} · chyb {failed}", local,
                      time.strftime("%H:%M:%S", time.localtime(self.last_refresh)) if self.last_refresh else "Není evidováno"]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeRowsToContents()

    def complete(self):
        record = self.selected()
        if not record or not record["run_dir"]:
            self.notice.setText("Pro převzetí jsou nutné místní podklady běhu.")
            return
        root = record["run_dir"]
        identifier = record["id"]
        def receive(value):
            record["state"] = read_state(root)
            self.render()
            self.notice.setText("Výsledek převzetí: " + VALUES.get(value.get("status"), "Podrobnosti jsou v evidenci"))
        self.execute("Převzetí výsledků dávky", lambda client, task: complete_saved_batch(client, root, identifier, self.context.settings, progress=task.progress_event.emit),
                     receive,
                     output_dir=record["state"].get("out_dir"))

    def cancel(self):
        record = self.selected()
        if record and record["remote"].get("status") in CANCELLABLE and confirm(self, "Zrušit dávku", "Odeslat žádost o zrušení vzdálené dávky? Dokončené úlohy mohou být účtované."):
            self.execute("Zrušení dávky", lambda client, task: client.cancel_batch(record["id"]),
                         lambda value: self.notice.setText("Žádost o zrušení byla přijata; ověřte konečný stav dávky."))

    def repeat(self, checked=False, repair=False):
        record = self.selected()
        if not record or not record["run_dir"]:
            return
        if record["state"].get("mode") == "COMIC":
            self.notice.setText("Opakování konkrétních panelů a editace jsou dostupné v Komiks → Historie a Panely.")
            return
        paths = ValueDialog("Vybrat soubory", "Relativní cesty souborů jako seznam JSON; opakování vytvoří placenou dávku", "[]", self, structured=True)
        if paths.exec() != QDialog.Accepted:
            return
        if not isinstance(paths.value, list) or not paths.value or not all(isinstance(path, str) for path in paths.value):
            self.notice.setText("Zadejte neprázdný seznam relativních cest.")
            return
        feedback = ""
        if repair:
            dialog = ValueDialog("Připomínka k opravě", "Co má oprava změnit?", parent=self)
            if dialog.exec() != QDialog.Accepted:
                return
            feedback = dialog.value
        self.execute("Opakování vybraných souborů", lambda client, task: repeat_saved_batch(client, record["run_dir"], record["id"], paths.value, feedback),
                     lambda value: self.notice.setText("Opakování bylo odesláno; podrobnosti jsou v evidenci dávky."))

    def download_raw(self):
        record = self.selected()
        if not record:
            return
        identifier = record["remote"].get("output_file_id")
        if not identifier:
            self.notice.setText("Dávka zatím nemá soubor výsledků.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Uložit původní výsledky", "vysledky.jsonl")
        if path:
            def download(client, task):
                data = client.file_content(identifier)
                Path(path).write_bytes(data)
                return path

            self.execute("Stažení původních výsledků", download)

    def details(self):
        record = self.selected()
        if record:
            DetailDialog("Podrobnosti dávky", record["id"], self, json.dumps(record, ensure_ascii=False, indent=2)).exec()
