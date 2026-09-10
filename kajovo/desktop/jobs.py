"""Asynchronní úlohy; GUI vlastní vlákno až do skutečného ukončení."""

from PySide6.QtCore import QThread, Signal, QObject
from .dialogs import TaskProgressDialog, UploadProgressDialog, msg_warning


class Job(QThread):
    result = Signal(object)
    error = Signal(str)
    status = Signal(str)
    progress = Signal(int)
    logline = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancelled = False

    def request_stop(self):
        self.cancelled = True

    def check_stop(self):
        if self.cancelled:
            raise RuntimeError("Operace byla zastavena.")

    def run(self):
        try:
            self.result.emit(self.operation(self))
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
            job.logline.connect(dialog.add_log)
            if cancellable:
                dialog.set_cancel_handler(job.request_stop)
            dialog.show()
        outcome = {}
        job.result.connect(lambda result: outcome.update(result=result))
        job.error.connect(lambda message: outcome.update(error=message))

        def finished():
            if dialog:
                dialog.mark_done("Operace skončila; výsledek je uveden v přehledu nebo logu.")
            self.active.discard(job)
            try:
                if "error" in outcome:
                    if dialog:
                        dialog.add_log(outcome["error"])
                    msg_warning(self.parent(), title, outcome["error"])
                elif "result" in outcome:
                    receive(outcome["result"])
            finally:
                self.busy_changed.emit(bool(self.active))
                job.deleteLater()

        job.finished.connect(finished)
        job.start()
        return job
