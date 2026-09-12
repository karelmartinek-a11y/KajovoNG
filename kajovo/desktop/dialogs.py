"""Dialogy se společným vzhledem a bezpečnou životností probíhající práce."""

import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QProgressBar,
    QCheckBox,
    QMessageBox,
    QFileDialog,
    QInputDialog,
    QTextBrowser,
)
from ..core.progress import ProgressClock, ProgressEvent
from .design import column, label, button, row, editor, card, FitDialog


class DetailDialog(FitDialog):
    def __init__(self, title, content, parent=None, details=None, question=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 520)
        layout = column(self, 16)
        layout.setSpacing(8)
        layout.addWidget(label(title, "Heading"))
        self.browser = QTextBrowser()
        self.browser.setPlainText(str(content))
        self.browser.setOpenExternalLinks(False)
        layout.addWidget(self.browser, 1)
        if details is not None:
            raw = (
                details
                if isinstance(details, str)
                else json.dumps(details, ensure_ascii=False, indent=2)
            )
            self.technical = button("Technické podklady")
            self.technical.setCheckable(True)

            def toggle(checked):
                if checked:
                    self._summary = self.browser.toHtml()
                    self.browser.setPlainText(raw)
                else:
                    self.browser.setHtml(self._summary)
                self.technical.setText("Zobrazit souhrn" if checked else "Technické podklady")

            self.technical.toggled.connect(toggle)
            layout.addWidget(self.technical)
        choices = QDialogButtonBox(
            QDialogButtonBox.Yes | QDialogButtonBox.No if question else QDialogButtonBox.Close
        )
        if question:
            choices.button(QDialogButtonBox.Yes).setText("Potvrdit")
            choices.button(QDialogButtonBox.No).setText("Zrušit")
        else:
            choices.button(QDialogButtonBox.Close).setText("Zavřít")
        choices.accepted.connect(self.accept)
        choices.rejected.connect(self.reject)
        layout.addWidget(choices)


def msg_info(parent, title, message, details=None):
    return DetailDialog(title, message, parent, details).exec()


def msg_warning(parent, title, message, details=None):
    return msg_info(parent, title, message, details)


def msg_critical(parent, title, message, details=None):
    return msg_info(parent, title, message, details)


def msg_question(parent, title, message, details=None):
    result = DetailDialog(title, message, parent, details, True).exec()
    return QMessageBox.Yes if result == QDialog.Accepted else QMessageBox.No


class FilePicker(QFileDialog):
    def __init__(self, parent=None, title="", directory="", filters="All (*.*)"):
        super().__init__(parent, title, directory, filters)
        self.setOption(QFileDialog.DontUseNativeDialog, True)
        self.setLabelText(QFileDialog.LookIn, "Umístění")
        self.setLabelText(QFileDialog.FileName, "Název")
        self.setLabelText(QFileDialog.FileType, "Typ souboru")
        self.setLabelText(QFileDialog.Reject, "Zrušit")

    def showEvent(self, event):
        self.setLabelText(
            QFileDialog.Accept,
            "Uložit" if self.acceptMode() == QFileDialog.AcceptSave else "Vybrat",
        )
        available = (
            self.parentWidget().window().size()
            if self.parentWidget()
            else self.screen().availableGeometry().size()
        )
        self.resize(
            min(self.width(), available.width() - 16), min(self.height(), available.height() - 16)
        )
        super().showEvent(event)


def dialog_open_file(parent, title, directory="", filters="All (*.*)"):
    dialog = FilePicker(parent, title, directory, filters)
    dialog.setFileMode(QFileDialog.ExistingFile)
    if dialog.exec() == QDialog.Accepted and dialog.selectedFiles():
        return dialog.selectedFiles()[0], dialog.selectedNameFilter()
    return "", ""


def dialog_save_file(parent, title, directory="", filters="All (*.*)"):
    dialog = FilePicker(parent, title, directory, filters)
    dialog.setAcceptMode(QFileDialog.AcceptSave)
    if dialog.exec() == QDialog.Accepted and dialog.selectedFiles():
        return dialog.selectedFiles()[0], dialog.selectedNameFilter()
    return "", ""


def dialog_select_dir(parent, title, directory=""):
    dialog = FilePicker(parent, title, directory)
    dialog.setFileMode(QFileDialog.Directory)
    dialog.setOption(QFileDialog.ShowDirsOnly, True)
    if dialog.exec() == QDialog.Accepted and dialog.selectedFiles():
        return dialog.selectedFiles()[0]
    return ""


class TextInputDialog(QInputDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOkButtonText("Potvrdit")
        self.setCancelButtonText("Zrušit")

    def showEvent(self, event):
        available = (
            self.parentWidget().window().size()
            if self.parentWidget()
            else self.screen().availableGeometry().size()
        )
        self.resize(
            min(self.width(), available.width() - 16), min(self.height(), available.height() - 16)
        )
        super().showEvent(event)


def dialog_input_text(parent, title, message, value=""):
    dialog = TextInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(message)
    dialog.setOption(QInputDialog.UsePlainTextEditForTextInput, True)
    dialog.setTextValue(value)
    dialog.setOkButtonText("Potvrdit")
    dialog.setCancelButtonText("Zrušit")
    accepted = dialog.exec() == QDialog.Accepted
    return dialog.textValue(), accepted


STATES = {
    "active": "Probíhá",
    "waiting": "Čekání na API",
    "completed": "Dokončeno",
    "failed": "Chyba",
    "cancelled": "Zastaveno",
    "batch_pending": "Odesláno do fronty BATCH",
    "files_complete_unverified": "Soubory uloženy · funkčnost neověřena",
    "partial": "Částečné výsledky · vyžadují kontrolu",
    "validating": "Ověřování dávky",
    "in_progress": "Zpracovává se",
    "finalizing": "Dokončuje se",
    "cancelling": "Ruší se zpracování",
    "expired": "Vypršel čas dávky",
    "preflight_pending": "Čeká na ověření dávky",
}


class ProgressDialog(FitDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Průběh běhu")
        self.resize(760, 580)
        self.clock = ProgressClock()
        layout = column(self, 16)
        layout.setSpacing(8)
        layout.addWidget(label("Průběh běhu", "Heading"))
        surface, body = card()
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(6)
        self.lbl_status = label("Připravuji běh…")
        self.lbl_stage = label("Příprava · Probíhá")
        self.lbl_time = label("", "Hint")
        self.pb, self.pb_sub = QProgressBar(), QProgressBar()
        self.pb.setRange(0, 0)
        self.pb_sub.setRange(0, 0)
        for widget in (self.lbl_status, self.lbl_stage, self.pb, self.pb_sub, self.lbl_time):
            body.addWidget(widget)
        layout.addWidget(surface)
        self.txt_log = editor(readonly=True, height=80)
        self.txt_log.setMaximumBlockCount(2000)
        layout.addWidget(self.txt_log, 1)
        self.chk_bzz = QCheckBox("Upozornit po dokončení")
        self.btn_stop = button("Zastavit běh", role="Danger")
        self.btn_close = button("Skrýt průběh", self.hide)
        layout.addWidget(row(self.chk_bzz, self.btn_stop, self.btn_close))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_eta)
        self.timer.start(1000)
        self._update_eta()

    def set_progress(self, value):
        # Starší procentní signál neprokazuje dokončení operace.
        return None

    def set_subprogress(self, value):
        return None

    def set_status(self, value):
        self.lbl_status.setText(value)

    def add_log(self, value):
        self.txt_log.appendPlainText(value)

    def on_progress_event(self, event):
        self.clock.update(event)
        self.lbl_stage.setText(f"{event.stage} · {STATES.get(event.state, event.state)}")
        if event.detail:
            self.set_status(event.detail)
        if self.clock.total:
            self.pb_sub.setRange(0, self.clock.total)
            self.pb_sub.setValue(self.clock.completed)
            self.pb_sub.setFormat(f"%v / %m {self.clock.unit}")
        else:
            self.pb_sub.setRange(0, 0)
        if self.clock.finished is not None:
            self.pb.setRange(0, 100)
            self.pb.setValue(100 if event.state == "completed" else 0)
            self.pb_sub.setRange(0, max(1, self.clock.total or 1))
            self.pb_sub.setValue(self.clock.completed)
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.chk_bzz.hide()
            self.btn_close.setText("OK")
            self.btn_close.setDefault(True)
            self.timer.stop()
        self._update_eta()

    def _update_eta(self):
        elapsed, age, eta = self.clock.times()
        remaining = f"{int(eta)} s" if eta is not None else "nelze určit"
        self.lbl_time.setText(
            f"Uplynulo {int(elapsed)} s · Odhad zbývající doby: {remaining}\nPoslední aktivita před {int(age)} s"
        )

    def reject(self):
        self.hide()


class TaskProgressDialog(FitDialog):
    def __init__(self, title, parent=None, show_subprogress=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 500)
        self._done = False
        self.terminal_state = None
        self.clock = ProgressClock()
        layout = column(self, 16)
        layout.setSpacing(8)
        layout.addWidget(label(title, "Heading"))
        self.lbl_status = label("Připravuji…")
        self.lbl_current = label("")
        self.activity = label("", "Hint")
        self.pb, self.pb_sub = QProgressBar(), QProgressBar()
        self.pb.setRange(0, 0)
        self.pb_sub.setVisible(show_subprogress)
        self.txt_log = editor(readonly=True, height=80)
        self.txt_log.setMaximumBlockCount(2000)
        for widget in (self.lbl_status, self.lbl_current, self.activity, self.pb, self.pb_sub):
            layout.addWidget(widget)
        layout.addWidget(self.txt_log, 1)
        self.btn_close = button("Zavřít", self.accept)
        self.btn_close.setEnabled(False)
        self.btn_cancel = button("Zrušit operaci", self._handle_cancel, "Danger")
        self.btn_cancel.hide()
        self._cancel = None
        self._cancelled = False
        layout.addWidget(row(self.btn_cancel, self.btn_close))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()

    def _tick(self):
        elapsed, age, _ = self.clock.times()
        self.activity.setText(
            f"Uplynulo {int(elapsed)} s · Poslední aktivita před {int(age)} s · ETA nelze určit"
        )

    def set_status(self, value):
        self.lbl_status.setText(value)
        if not self._done:
            self.clock.update(ProgressEvent("Operace", detail=value))

    def set_current(self, value):
        self.lbl_current.setText(value)

    def set_progress(self, value):
        if self._done:
            return
        self.pb.setRange(0, 100)
        self.pb.setValue(value)
        self.clock.update(ProgressEvent("Operace"))

    def set_subprogress(self, value):
        if self._done:
            return
        self.pb_sub.setRange(0, 100)
        self.pb_sub.setValue(value)

    def on_progress_event(self, event):
        if self._done:
            return
        self.clock.update(event)
        if event.detail:
            self.lbl_status.setText(event.detail)
        self.lbl_current.setText(f"{event.stage} · {STATES.get(event.state, event.state)}")
        if event.total:
            self.pb_sub.setVisible(True)
            self.pb_sub.setRange(0, event.total)
            self.pb_sub.setValue(event.completed)
            self.pb_sub.setFormat(f"%v / %m {event.unit}" if event.unit else "%v / %m")
        self._tick()

    def add_log(self, value):
        self.txt_log.appendPlainText(value)
        if not self._done:
            self.clock.update(ProgressEvent("Operace"))

    def _mark_terminal(self, state, text, *, success=False):
        if self._done:
            return
        self._done = True
        self.terminal_state = state
        self.lbl_status.setText(text)
        self.clock.update(ProgressEvent("RUN", state))
        if success:
            self.pb.setRange(0, 100)
            self.pb.setValue(100)
        elif self.pb.minimum() == 0 and self.pb.maximum() == 0:
            # Chyba/zrušení ukončí animaci, ale nesmí implikovat 100% úspěch.
            self.pb.setRange(0, 100)
            self.pb.setValue(0)
        self.btn_close.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.timer.stop()
        self._tick()

    def mark_success(self, text="Dokončeno."):
        self._mark_terminal("completed", text, success=True)

    def mark_failed(self, text="Operace selhala."):
        self._mark_terminal("failed", text)

    def mark_cancelled(self, text="Operace byla zastavena."):
        self._mark_terminal("cancelled", text)

    def mark_done(self, text="Dokončeno."):
        """Kompatibilní alias; nové volající mají používat explicitní terminální větev."""
        self.mark_success(text)

    def set_cancel_handler(self, handler):
        self._cancel = handler
        self.btn_cancel.show()

    def set_cancel_enabled(self, enabled):
        self.btn_cancel.setEnabled(enabled and not self._cancelled and not self._done)

    def _handle_cancel(self):
        if self._done or self._cancelled or not self.btn_cancel.isEnabled():
            return
        self._cancelled = True
        self.btn_cancel.setEnabled(False)
        self.set_status("Ruším; čekám na potvrzení dokončení aktuální operace.")
        if self._cancel:
            self._cancel()

    def reject(self):
        if self._done:
            super().reject()
        else:
            self._handle_cancel()

    def closeEvent(self, event):
        if self._done:
            event.accept()
        else:
            self._handle_cancel()
            event.ignore()


class UploadProgressDialog(TaskProgressDialog):
    def __init__(self, title="Nahrávání souborů", parent=None):
        super().__init__(title, parent, False)
        self.btn_cancel.show()
