"""Asynchronní úlohy; GUI vlastní vlákno až do skutečného ukončení."""

from PySide6.QtCore import QThread, Signal, QObject
from .dialogs import TaskProgressDialog, UploadProgressDialog, msg_warning


class JobCancelled(RuntimeError):
    """Kooperativní zrušení vyžádané uživatelem, nikoli chyba operace."""


class Job(QThread):
    result = Signal(object)
    error = Signal(str)
    cancelled = Signal(str)
    status = Signal(str)
    progress = Signal(int)
    progress_event = Signal(object)
    logline = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_requested = False

    def request_stop(self):
        self.cancel_requested = True

    def check_stop(self):
        if self.cancel_requested:
            raise JobCancelled("Operace byla zastavena.")

    def run(self):
        try:
            self.result.emit(self.operation(self))
        except JobCancelled as exc:
            self.cancelled.emit(str(exc))
        except Exception as exc:
            self.error.emit(str(exc))


class Jobs(QObject):
    busy_changed = Signal(bool)

    def __init__(self, parent):
        super().__init__(parent)
        self.active = set()

    def start(
        self,
        title,
        operation,
        receive=lambda result: None,
        popup=True,
        cancellable=False,
        upload=False,
        on_finished=None,
    ):
        job = Job(operation, self)
        self.active.add(job)
        self.busy_changed.emit(True)
        dialog = (
            (
                UploadProgressDialog(title, self.parent())
                if upload
                else TaskProgressDialog(title, self.parent(), False)
            )
            if popup
            else None
        )
        if dialog:
            job.status.connect(dialog.set_status)
            job.progress.connect(dialog.set_progress)
            job.progress_event.connect(dialog.on_progress_event)
            job.logline.connect(dialog.add_log)
            if cancellable:
                dialog.set_cancel_handler(job.request_stop)
            dialog.show()
        outcome = {}
        job.result.connect(lambda result: outcome.update(result=result))
        job.error.connect(lambda message: outcome.update(error=message))
        job.cancelled.connect(lambda message: outcome.update(cancelled=message))

        def finished():
            self.active.discard(job)
            try:
                if "cancelled" in outcome:
                    if dialog:
                        dialog.mark_cancelled(outcome["cancelled"] or "Operace byla zastavena.")
                elif "error" in outcome:
                    if dialog:
                        dialog.add_log(outcome["error"])
                        dialog.mark_failed("Operace selhala; podrobnosti jsou uvedeny v logu.")
                    msg_warning(self.parent(), title, outcome["error"])
                elif "result" in outcome:
                    if dialog:
                        dialog.mark_success("Operace skončila; výsledek je uveden v přehledu nebo logu.")
                    receive(outcome["result"])
                else:
                    message = "Operace skončila bez výsledku a bez chybového stavu."
                    if dialog:
                        dialog.add_log(message)
                        dialog.mark_failed(message)
                    msg_warning(self.parent(), title, message)
            finally:
                try:
                    if on_finished:
                        on_finished()
                finally:
                    self.busy_changed.emit(bool(self.active))
                    job.deleteLater()

        job.finished.connect(finished)
        job.start()
        return job
