"""Vlastnictví pracovníků a průběh operací až do skutečného konce vlákna."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QListWidget, QListWidgetItem

from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.core.user_errors import UserError, describe_error
from kajovo.progress_ui import DIALOG_STYLE, ProcessInspector
from .components import BranchMark, DetailDialog, action, actions, caption, vertical


class Cancelled(Exception):
    """Ukončení v místě, kde backend garantuje bezpečné přerušení."""


class Task(QThread):
    value = Signal(object)
    failure = Signal(object)
    progress_event = Signal(object)
    status = Signal(str)
    logline = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def request_stop(self):
        self.requestInterruption()

    def check_stop(self):
        if self.isInterruptionRequested():
            raise Cancelled()

    def run(self):
        try:
            self.value.emit(self.function(self))
        except Cancelled:
            self.progress_event.emit(ProgressEvent("RUN", "cancelled"))
        except Exception as error:
            self.failure.emit(describe_error(error))


STATES = {
    "created": "Vytvořeno",
    "preparing": "Připravuje se",
    "running": "Běží",
    "active": "Pracuji",
    "waiting": "Čekám na službu",
    "validating_result": "Ověřuji výsledek",
    "repairing": "Opravuji podklad",
    "completed": "Dokončeno",
    "failed": "Operace selhala",
    "error": "Operace selhala",
    "partial": "Dokončeno s chybami",
    "cancelled": "Zastaveno",
    "stopped": "Zastaveno",
    "batch_prepared": "Dávka je připravena",
    "batch_pending": "Dávka byla předána službě",
    "response_pending": "Odpověď se stále zpracovává",
    "submission_unknown": "Výsledek odeslání není znám",
    "dry_run": "Návrh je připraven bez zápisu",
    "plan_ready": "Ověřený plán je připraven; výroba nebyla spuštěna",
    "qfile_plan_ready": "Návrh cesty QFILE čeká na potvrzení uživatele",
    "files_complete_unverified": "Soubory jsou převzaté, funkčnost nebyla ověřena",
    "ready_to_import": "Výsledek je připraven k převzetí",
    "importing": "Přebírám vzdálený výsledek",
    "cancelling": "Žádost o zrušení byla přijata, čeká se na potvrzení služby",
    "expired": "Vypršel čas služby",
    "corrupt_state": "Chyba evidence",
    "unknown": "Neznámý stav",
}

STAGES = {
    "RUN": "Pracovní běh",
    "A0": "Příprava dlouhého zadání",
    "A0R": "Upřesnění požadavků",
    "A1": "Návrh řešení",
    "A2": "Struktura projektu",
    "A2Q": "Nezávislá kontrola návrhu",
    "A3": "Vytváření souborů",
    "B0R": "Upřesnění požadovaných změn",
    "B1": "Plán změn",
    "B2": "Struktura změn",
    "B2Q": "Nezávislá kontrola změn",
    "B3": "Úprava souborů",
    "Upload": "Nahrávání podkladů",
    "BATCH": "Zpracování dávky",
}


@dataclass
class Operation:
    identifier: str
    title: str
    worker: QThread
    dialog: "OperationDialog | None"
    result: object = None
    error: UserError | None = None
    terminal: str = ""
    events: list = field(default_factory=list)


class OperationDialog(QDialog):
    """Studio progress dialog používající stejný procesní inspektor jako desktop."""

    def __init__(self, title, parent=None, reduced_motion=False):
        super().__init__(parent)
        self.setObjectName("operation.progress")
        self.setWindowTitle(title)
        self.resize(1080, 740)
        self.setMinimumSize(760, 560)
        self.setStyleSheet(DIALOG_STYLE)
        self.clock = ProgressClock()
        self.events = []
        self.active = True
        self.reduced_motion = reduced_motion
        self.stop_callback = None
        self.error = None
        self.result = None
        root = vertical(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.mark = BranchMark()
        self.mark.hide()
        self.inspector = ProcessInspector(title)
        root.addWidget(self.inspector, 1)
        self.summary = self.inspector.activity_label
        self.stage = self.inspector.phase_label
        self.source = self.inspector.source_label
        self.plan = self.inspector.meta_label
        self.next_step = self.inspector.next_label
        self.progress = self.inspector.unit_progress
        self.counts = self.inspector.progress_note
        self.times = self.inspector.time_label
        self.log = self.inspector.log
        self.notification = QCheckBox("Oznámit výsledek elektronickou poštou")
        self.notification.setObjectName("operation.notification")
        self.notification.hide()
        self.stop = action("operation.stop", "Zastavit", self.request_stop, "danger")
        self.stop.setEnabled(False)
        self.details = action("operation.details", "Podrobnosti chyby", self.show_details)
        self.details.hide()
        self.result_button = action("operation.result", "Výsledek", self.show_result)
        self.result_button.hide()
        self.close_button = action("operation.hide", "Skrýt průběh", self.hide)
        self.close_button.setAutoDefault(False)
        controls = actions(
            self.notification,
            self.close_button,
            self.stop,
            self.details,
            self.result_button,
        )
        controls.setStyleSheet(DIALOG_STYLE)
        root.addWidget(controls)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def showEvent(self, event):
        bounds = self.screen().availableGeometry()
        self.resize(min(self.width(), bounds.width()), min(self.height(), bounds.height()))
        super().showEvent(event)
        self.mark.set_running(self.active, self.reduced_motion)

    def on_event(self, event):
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        self.clock.update(event)
        self.inspector.on_event(event, self.clock)
        if event.stage == "RUN" and event.state in STATES and event.state not in {"active", "waiting"}:
            self.summary.setText(STATES[event.state])
        self.tick()

    def _refresh_plan(self):
        self.inspector.refresh(self.clock)

    def tick(self):
        self.inspector.refresh(self.clock)

    def request_stop(self):
        if self.active and self.stop_callback:
            self.stop.setEnabled(False)
            event = ProgressEvent("RUN", "cancelling", detail="Čekám na bezpečné ukončení operace")
            self.events.append(event)
            self.clock.update(event)
            self.inspector.on_event(event, self.clock)
            self.stop_callback()

    def finish(self, state, error=None):
        self.active = False
        self.error = error
        self.timer.stop()
        self.mark.set_running(False)
        self.stop.setEnabled(False)
        self.stop.hide()
        self.close_button.setText("OK")
        self.close_button.setAccessibleName("OK")
        self.close_button.setDefault(True)
        self.close_button.setFocus()
        self.notification.setEnabled(False)
        event = ProgressEvent("RUN", state, detail=error.message if error else STATES.get(state, state))
        self.events.append(event)
        self.clock.update(event)
        self.inspector.on_event(event, self.clock)
        if self.progress.maximum() == 0:
            self.progress.hide()
        elif self.counts.text() and self.progress.isVisible():
            self.counts.setText("Poslední doložený postup: " + self.counts.text())
        self.details.setVisible(error is not None)
        self.result_button.setVisible(self.result is not None)
        self.tick()

    def show_details(self):
        if self.error:
            DetailDialog(
                "Podrobnosti chyby",
                self.error.message,
                self,
                self.error.detail + "\n\n" + self.error.next_step,
            ).exec()

    def show_result(self):
        from dataclasses import asdict, is_dataclass

        value = asdict(self.result) if is_dataclass(self.result) else self.result
        DetailDialog(
            "Výsledek operace",
            "Úplné vrácené podklady jsou dostupné v technických podrobnostech.",
            self,
            value,
        ).exec()

    def closeEvent(self, event):
        if self.active:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)


class Operations(QObject):
    changed = Signal()
    completed = Signal(str, object)
    _output_reservations = {}

    def __init__(self, parent, reduced_motion=False):
        super().__init__(parent)
        self.records = {}
        self.reduced_motion = reduced_motion
        self.overview = None
        self.changed.connect(self.refresh_overview)

    @property
    def active(self):
        return [record for record in self.records.values() if not record.terminal]

    def assert_output_available(self, directory):
        if not directory:
            return
        path = Path(directory).expanduser().resolve()
        if any(
            path == other or path in other.parents or other in path.parents
            for other in self._output_reservations.values()
        ):
            raise ValueError("Do tohoto adresáře nebo jeho části již zapisuje jiná operace.")

    def start(
        self,
        title,
        function,
        receive=None,
        cancellable=False,
        popup=True,
        output_dir=None,
        identifier=None,
    ):
        worker = Task(function, self)
        return self.adopt(
            title,
            worker,
            receive,
            cancellable=cancellable,
            popup=popup,
            output_dir=output_dir,
            identifier=identifier,
        )

    def start_read(self, title, function, receive=None, *, popup=False, identifier=None):
        """Krátké lokální čtení bez konstrukce a uchovávání progresového dialogu.

        Skutečné pracovní běhy používají nadále start/adopt, progres a zámky.
        Čtení zůstává vlastněno managerem až do dokončení vlákna.
        """
        identifier = identifier or uuid4().hex
        if identifier in self.records and not self.records[identifier].terminal:
            return self.records[identifier]
        worker = Task(function, self)
        record = Operation(identifier, title, worker, None)
        self.records[identifier] = record
        worker.value.connect(lambda value: setattr(record, "result", value))
        worker.failure.connect(lambda error: setattr(record, "error", error))

        def finish():
            record.terminal = "failed" if record.error else "completed"
            if not record.error and receive:
                try:
                    receive(record.result)
                except Exception as error:
                    record.error = describe_error(error)
                    record.terminal = "failed"
            if self.records.get(identifier) is record:
                self.records.pop(identifier, None)
            self.completed.emit(identifier, record.result)
            record.result = None
            worker.deleteLater()
            if record.error:
                dialog = record.dialog or OperationDialog(title, self.parent(), self.reduced_motion)
                dialog.finish("failed", record.error)
                dialog.setAttribute(Qt.WA_DeleteOnClose)
                dialog.show()
            elif record.dialog:
                record.dialog.finish("completed")
                record.dialog.close()
                record.dialog.deleteLater()
            self.changed.emit()

        worker.finished.connect(finish)
        worker.start()
        self.changed.emit()
        return record

    def adopt(
        self,
        title,
        worker,
        receive=None,
        *,
        cancellable=True,
        popup=True,
        identifier=None,
        output_dir=None,
    ):
        self.assert_output_available(output_dir)
        identifier = identifier or uuid4().hex
        previous = self.records.get(identifier)
        if previous and not previous.terminal:
            raise ValueError("Operace s tímto identifikátorem již existuje.")
        if previous:
            dialog = previous.dialog
            dialog.clock = ProgressClock()
            dialog.events = []
            dialog.inspector.events = []
            dialog.active = True
            dialog.error = None
            dialog.result = None
            dialog.result_button.hide()
            dialog.stop_callback = None
            dialog.stop.setEnabled(False)
            dialog.stop.show()
            dialog.close_button.setText("Skrýt průběh")
            dialog.close_button.setAccessibleName("Skrýt průběh")
            dialog.close_button.setDefault(False)
            dialog.notification.setEnabled(True)
            dialog.details.hide()
            dialog.summary.setText("Ověřuji aktuální stav")
            dialog.counts.clear()
            dialog.stage.setText("Čekám na zprávu služby")
            dialog.progress.setRange(0, 0)
            dialog.progress.show()
            dialog.timer.start()
            dialog.mark.set_running(dialog.isVisible(), self.reduced_motion)
            dialog.inspector.refresh(dialog.clock)
        else:
            dialog = OperationDialog(title, self.parent(), self.reduced_motion)
        cfg = getattr(worker, "cfg", None)
        if cfg is not None:
            dialog.inspector.set_context(
                kind=str(getattr(cfg, "mode", "") or ""),
                run_id=str(getattr(cfg, "run_id", "") or identifier),
                model=str(getattr(cfg, "model", "") or ""),
                project=str(getattr(cfg, "project", "") or ""),
            )
        record = Operation(identifier, title, worker, dialog)
        self.records[identifier] = record
        if output_dir:
            self._output_reservations[(id(self), identifier)] = Path(output_dir).expanduser().resolve()
        if cancellable and hasattr(worker, "request_stop"):
            dialog.stop_callback = worker.request_stop
            dialog.stop.setEnabled(True)
        if hasattr(worker, "progress_event"):
            worker.progress_event.connect(lambda event: self._event(record, event))
        if hasattr(worker, "status"):
            worker.status.connect(dialog.summary.setText)
        if hasattr(worker, "logline"):
            worker.logline.connect(dialog.inspector.append_log)
        success = worker.value if isinstance(worker, Task) else worker.finished_ok
        failure = (
            worker.failure
            if isinstance(worker, Task)
            else getattr(worker, "failure_detail", worker.finished_err)
        )
        success.connect(lambda value: setattr(record, "result", value))
        failure.connect(
            lambda error: setattr(
                record,
                "error",
                error if isinstance(error, UserError) else describe_error(RuntimeError(str(error))),
            )
        )
        worker.finished.connect(lambda: self._finished(record, receive))
        if popup:
            dialog.show()
        worker.start()
        self.changed.emit()
        return record

    def _event(self, record, event):
        record.events.append(event)
        record.events[:] = record.events[-2000:]
        record.dialog.on_event(event)

    def _finished(self, record, receive):
        self._output_reservations.pop((id(self), record.identifier), None)
        terminal_events = [
            e.state
            for e in record.events
            if e.stage == "RUN" and e.state in STATES and e.state not in {"active", "waiting"}
        ]
        result_status = record.result.get("status") if isinstance(record.result, dict) else None
        if isinstance(record.result, dict) and record.result.get("batch_id") and not result_status:
            result_status = "batch_pending"
        elif getattr(record.result, "batch_id", ""):
            status = getattr(record.result, "status", "")
            result_status = {
                "downloaded": "completed",
                "partial": "partial",
                "failed": "failed",
                "cancelled": "cancelled",
            }.get(status, "batch_pending")
        record.terminal = (
            "failed"
            if record.error
            else (terminal_events[-1] if terminal_events else result_status or "completed")
        )
        if (
            not record.error
            and receive
            and record.terminal not in {"cancelled", "response_pending", "submission_unknown"}
        ):
            try:
                receive(record.result)
            except Exception as error:
                record.error = describe_error(error)
                record.terminal = "failed"
        record.dialog.result = record.result
        record.dialog.finish(record.terminal, record.error)
        record.worker.deleteLater()
        self.changed.emit()
        self.completed.emit(record.identifier, record.result)

    def show_all(self):
        if self.overview is None:
            self.overview = QDialog(self.parent())
            self.overview.setWindowTitle("Přehled operací")
            self.overview.resize(620, 460)
            root = vertical(self.overview)
            root.addWidget(caption("Místní operace této relace", "section"))
            self.listing = QListWidget()
            self.listing.setWordWrap(True)
            self.listing.setAccessibleName("Běžící a dokončené operace")
            root.addWidget(self.listing, 1)
            root.addWidget(
                actions(
                    action("operations.detail", "Otevřít průběh", self.open_selected),
                    action("operations.close", "Zavřít přehled", self.overview.hide),
                )
            )
            self.listing.itemDoubleClicked.connect(self.open_selected)
        self.refresh_overview()
        self.overview.show()
        self.overview.raise_()

    def refresh_overview(self):
        if self.overview is None:
            return
        selected = (
            self.listing.currentItem().data(Qt.UserRole) if self.listing.currentItem() else None
        )
        self.listing.clear()
        for record in reversed(list(self.records.values())):
            item = QListWidgetItem(
                record.title + "\n" + STATES.get(record.terminal or "active", record.terminal)
            )
            item.setData(Qt.UserRole, record.identifier)
            self.listing.addItem(item)
            if record.identifier == selected:
                self.listing.setCurrentItem(item)
        if self.listing.currentRow() < 0 and self.listing.count():
            self.listing.setCurrentRow(0)

    def open_selected(self, *_):
        item = self.listing.currentItem()
        if item:
            record = self.records.get(item.data(Qt.UserRole))
            if record is None:
                return
            if record.dialog is None:
                record.dialog = OperationDialog(record.title, self.parent(), self.reduced_motion)
            dialog = record.dialog
            dialog.show()
            dialog.raise_()
