"""Dialogy se společným vzhledem a bezpečnou životností probíhající práce."""

import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QMessageBox,
    QFileDialog,
    QInputDialog,
    QTextBrowser,
)
from ..core.progress import ProgressClock, ProgressEvent, TERMINAL_RUN_STATES
from ..core.progress_display import state_title
from .design import column, label, button, row, FitDialog
from ..progress_ui import DIALOG_STYLE, ProcessInspector


PREPARATION_STAGES = {
    "A0": "Analýza zadání",
    "A0R": "Profesionální requirements",
    "A1": "Architektonický plán",
    "A2": "Implementační struktura",
    "A2Q": "Quality gate",
    "A3": "Generování souborů",
    "B0R": "Change requirements",
    "B1": "Plán změny",
    "B2": "Implementační struktura změny",
    "B2Q": "Quality gate",
    "B3": "Generování změněných souborů",
}


def stage_label(stage):
    return PREPARATION_STAGES.get(stage.split("_", 1)[0], stage)


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
    "created": "Vytvořeno",
    "preparing": "Připravuje se",
    "running": "Běží",
    "active": "Probíhá",
    "waiting": "Čeká na odpověď služby",
    "validating_result": "Ověřuje výsledek",
    "repairing": "Opravuje podklad",
    "response_pending": "Čeká na odpověď",
    "batch_prepared": "BATCH připraven",
    "batch_pending": "BATCH běží",
    "importing": "Přebírá se",
    "ready_to_import": "K převzetí",
    "completed": "Dokončeno",
    "closed": "Dokončeno / uzavřeno",
    "dry_run": "Dry-run / návrh bez zápisu",
    "partial": "Částečně dokončeno",
    "files_complete_unverified": "Soubory převzaty, funkčnost neověřena",
    "unfinished_record": "Konec fáze nezapsán",
    "cancelled": "Zrušeno",
    "stopped": "Zastaveno",
    "cancelling": "Ruší se",
    "failed": "Chyba",
    "error": "Chyba",
    "submission_unknown": "Neznámý výsledek odeslání",
    "corrupt_state": "Chyba evidence",
    "unknown": "Neznámý stav",
    "expired": "Vypršel čas služby",
    "not_started": "Ještě nezačalo",
    "blocked": "Blokováno",
    "skipped": "Přeskočeno",
    "validating": "Ověřování dávky",
    "in_progress": "Zpracovává se",
    "finalizing": "Dokončuje se",
}


def _run_terminal(event):
    return event.stage == "RUN" and event.state in TERMINAL_RUN_STATES


class ProgressDialog(FitDialog):
    """Hlavní GENERATE/MODIFY dialog jako živý procesní inspektor."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Průběh běhu")
        self.resize(1120, 760)
        self.setMinimumSize(820, 600)
        self.setStyleSheet(DIALOG_STYLE)
        self.clock = ProgressClock()
        self.events = []
        layout = column(self, 0)
        layout.setSpacing(0)
        self.inspector = ProcessInspector("Práce na projektu")
        layout.addWidget(self.inspector, 1)
        self.lbl_status = self.inspector.activity_label
        self.lbl_stage = self.inspector.phase_label
        self.lbl_source = self.inspector.source_label
        self.lbl_plan = self.inspector.meta_label
        self.lbl_next = self.inspector.next_label
        self.lbl_time = self.inspector.time_label
        self.pb = self.inspector.run_progress
        self.pb_sub = self.inspector.unit_progress
        self.txt_log = self.inspector.log
        self.chk_bzz = QCheckBox("Upozornit po dokončení")
        self.btn_stop = button("Zastavit běh", role="Danger")
        self.btn_stop.setToolTip(
            "Požádá o bezpečné zastavení. Terminální stav se zobrazí až po potvrzení backendem."
        )
        self.btn_cancel_response = button("Zrušit generování", role="Danger")
        self.btn_cancel_response.hide()
        self.btn_close = button("Skrýt průběh", self.hide)
        controls = row(self.chk_bzz, self.btn_stop, self.btn_cancel_response, self.btn_close)
        controls.setStyleSheet(DIALOG_STYLE)
        layout.addWidget(controls)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_eta)
        self.timer.start(1000)
        self._update_eta()

    def setWindowTitle(self, title):
        super().setWindowTitle(title)
        if hasattr(self, "inspector"):
            run_id = title.split("·", 1)[1].strip() if "·" in title else ""
            if run_id:
                self.inspector.set_context(run_id=run_id)

    def set_progress(self, value):
        # Starší procentní signál neprokazuje dokončení operace.
        return None

    def set_subprogress(self, value):
        return None

    def set_status(self, value):
        self.inspector.set_status_text(value)

    def add_log(self, value):
        self.inspector.append_log(value)

    def on_progress_event(self, event):
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        self.clock.update(event)
        self.inspector.on_event(event, self.clock)
        self.btn_cancel_response.setEnabled(event.state == "waiting")
        self.lbl_stage = self.inspector.phase_label
        if _run_terminal(event):
            terminal_label = "Celý běh"
            if event.state == "batch_pending":
                terminal_label = "BATCH"
            elif event.state == "dry_run":
                terminal_label = "RUN · dry-run"
            self.inspector.phase_label.setText(f"{terminal_label} · {state_title(event.state)}")
            if self.pb_sub.maximum() == 0:
                self.pb_sub.setRange(0, max(1, self.clock.total or 1))
                self.pb_sub.setValue(self.clock.completed)
            self.btn_stop.setEnabled(False)
            self.btn_stop.hide()
            self.btn_cancel_response.hide()
            self.chk_bzz.hide()
            self.btn_close.setText("OK")
            self.btn_close.setDefault(True)
            self.timer.stop()
        self._update_eta()

    def _refresh_plan(self, event=None):
        self.inspector.refresh(self.clock)

    def _update_eta(self):
        self.inspector.refresh(self.clock)

    def reject(self):
        self.hide()


class TaskProgressDialog(FitDialog):
    """Společný progress dialog pro P03-P12 a podpůrné asynchronní operace."""

    def __init__(self, title, parent=None, show_subprogress=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1040, 720)
        self.setMinimumSize(760, 560)
        self.setStyleSheet(DIALOG_STYLE)
        self._done = False
        self.terminal_state = None
        self.clock = ProgressClock()
        self.events = []
        layout = column(self, 0)
        layout.setSpacing(0)
        self.inspector = ProcessInspector(title)
        layout.addWidget(self.inspector, 1)
        self.lbl_status = self.inspector.activity_label
        self.lbl_current = self.inspector.phase_label
        self.lbl_source = self.inspector.source_label
        self.lbl_plan = self.inspector.meta_label
        self.lbl_next = self.inspector.next_label
        self.activity = self.inspector.time_label
        self.pb = self.inspector.run_progress
        self.pb_sub = self.inspector.unit_progress
        self.pb_sub.setVisible(show_subprogress and self.pb_sub.isVisible())
        self.txt_log = self.inspector.log
        self.btn_close = button("Zavřít", self.accept)
        self.btn_close.setEnabled(False)
        self.btn_cancel = button("Zrušit operaci", self._handle_cancel, "Danger")
        self.btn_cancel.hide()
        self._cancel = None
        self._cancelled = False
        controls = row(self.btn_cancel, self.btn_close)
        controls.setStyleSheet(DIALOG_STYLE)
        layout.addWidget(controls)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self._tick()

    def _tick(self):
        self.inspector.refresh(self.clock)

    def set_status(self, value):
        self.inspector.set_status_text(value)
        if not self._done:
            event = ProgressEvent("Operace", detail=value)
            self.clock.update(event)

    def set_current(self, value):
        self.inspector.activity_label.setText(value)

    def set_progress(self, value):
        if self._done:
            return
        self.pb.hide()
        self.pb_sub.show()
        self.pb_sub.setRange(0, 100)
        self.pb_sub.setValue(value)
        self.pb_sub.setFormat("%p %")
        self.inspector.progress_note.setText(f"{value} % · měřeno volajícím procesem")
        self.clock.update(ProgressEvent("Operace"))

    def set_subprogress(self, value):
        if self._done:
            return
        self.pb_sub.show()
        self.pb_sub.setRange(0, 100)
        self.pb_sub.setValue(value)

    def on_progress_event(self, event):
        if self._done:
            return
        self.clock.update(event)
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        self.inspector.on_event(event, self.clock)
        self._tick()

    def _refresh_plan(self):
        self.inspector.refresh(self.clock)

    def add_log(self, value):
        self.inspector.append_log(value)
        if not self._done:
            self.clock.update(ProgressEvent("Operace"))

    def _mark_terminal(self, state, text, *, success=False):
        if self._done:
            return
        self._done = True
        self.terminal_state = state
        event = ProgressEvent("RUN", state, detail=text)
        self.clock.update(event)
        self.events.append(event)
        self.inspector.on_event(event, self.clock)
        self.pb.setRange(0, 100)
        self.pb.setValue(100 if success else 0)
        self.pb.show()
        if self.pb_sub.maximum() == 0:
            self.pb_sub.setRange(0, 1)
            self.pb_sub.setValue(0)
        self.btn_close.setEnabled(True)
        self.btn_close.setText("OK")
        self.btn_close.setDefault(True)
        self.btn_close.setFocus()
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.hide()
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
        event = ProgressEvent("RUN", "cancelling", detail="Čekám na bezpečné ukončení operace")
        self.clock.update(event)
        self.events.append(event)
        self.inspector.on_event(event, self.clock)
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
        self.inspector.set_context(kind="ZDROJE")
        self.btn_cancel.show()
