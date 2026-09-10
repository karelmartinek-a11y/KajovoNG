from __future__ import annotations

import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout
from ..core.progress import ProgressClock, ProgressEvent
from .theme import DARK_STYLESHEET
from .widgets import style_progress_bar


class ProgressDialog(QDialog):
    """Stav backendu a měřené jednotky; čekání na API nemá fiktivní procenta."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Průběh běhu")
        self.setModal(False)
        self.resize(760, 480)
        self.setStyleSheet(DARK_STYLESHEET)
        self.clock = ProgressClock()
        layout = QVBoxLayout(self)
        self.lbl = QLabel("Příprava běhu")
        self.lbl_file = QLabel("")
        self.lbl_time = QLabel("")
        for label in (self.lbl, self.lbl_file, self.lbl_time):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(label)
        self.pb, self.pb_sub = QProgressBar(), QProgressBar()
        style_progress_bar(self.pb, indeterminate=True)
        style_progress_bar(self.pb_sub, indeterminate=True)
        layout.addWidget(self.pb)
        layout.addWidget(self.pb_sub)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        layout.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.btn_stop = QPushButton("Zastavit")
        self.chk_bzz = QCheckBox("Upozornit po skončení")
        self.btn_close = QPushButton("Skrýt")
        for widget in (self.btn_stop, self.chk_bzz, self.btn_close):
            row.addWidget(widget)
        layout.addLayout(row)
        self.btn_close.clicked.connect(self.hide)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._update_eta)
        self.timer.start()
        self._update_eta()

    def set_progress(self, value):
        # Pevně vážená procenta nejsou důkazem dokončení operace.
        pass

    def set_subprogress(self, value):
        pass

    def set_status(self, text):
        self.lbl.setText(str(text))

    def add_log(self, line):
        if line:
            self.log.appendPlainText(str(line))

    def on_progress_event(self, event: ProgressEvent):
        self.clock.update(event)
        if event.detail:
            self.lbl.setText(event.detail)
        names = {"waiting": "Čeká na poskytovatele", "active": "Probíhá", "completed": "Dokončeno",
                 "approval": "Čeká na potvrzení ceny",
                 "failed": "Chyba", "cancelled": "Zrušeno", "batch_pending": "Dávka čeká na zpracování"}
        self.lbl_file.setText(f"{event.stage} · {names.get(event.state, event.state)}")
        if self.clock.total is not None:
            total = self.clock.total
            self.pb_sub.setRange(0, max(1, total))
            self.pb_sub.setValue(self.clock.completed)
            self.pb_sub.setTextVisible(True)
            self.pb_sub.setFormat(f"Dokončeno {self.clock.completed}/{total} {self.clock.unit}")
        else:
            style_progress_bar(self.pb_sub, indeterminate=True)
        if event.stage == "RUN" and event.state in ("completed", "failed", "cancelled", "batch_pending"):
            self.lbl.setText(event.detail or names[event.state])
            self.pb.setRange(0, 100)
            self.pb.setValue(100 if event.state == "completed" else 0)
            self.pb.setTextVisible(True)
            self.pb.setFormat(names[event.state])
            self.btn_stop.setEnabled(False)
            self.pb_sub.setRange(0, 100)
            self.pb_sub.setValue(100 if event.state == "completed" else 0)
            self.pb_sub.setTextVisible(False)
            self.timer.stop()
        self._update_eta()

    def _update_eta(self):
        elapsed, age, eta = self.clock.times()
        duration = time.strftime("%H:%M:%S", time.gmtime(elapsed))
        estimate = f"asi {int(eta)} s v této etapě" if eta is not None else "nelze určit"
        heartbeat = "Obnovování zobrazení: 1 s" if self.clock.finished is None else "Měření ukončeno"
        self.lbl_time.setText(f"Trvání {duration} · Odhad zbývajícího času: {estimate}\n"
                              f"Poslední událost backendu před {int(age)} s · {heartbeat}")
