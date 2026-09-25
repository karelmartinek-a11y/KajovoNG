"""Samostatné okno kruhové mapy a ovládání životního cyklu pracovní operace."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QPushButton, QWidget, QVBoxLayout

from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.multiprogress import MultiProgressView
from kajovo.studio.components import DetailDialog


class ActivityFlag:
    def __init__(self):
        self.running = False

    def set_running(self, active, reduced_motion=False):
        self.running = bool(active)


class MultiProgressDialog(QDialog):
    """Veřejné ovládání operace je oddělené od zpráv, které potvrzuje backend."""

    def __init__(self, title, parent=None, reduced_motion=False):
        super().__init__(parent)
        self.setObjectName("operation.progress")
        self.setWindowTitle(title)
        self.resize(1000, 730)
        self.setMinimumSize(680, 520)
        self.setStyleSheet("""
            QDialog { background: #101a2a; color: #f2f6fc; }
            QWidget#progressActions { background: #101a2a; }
            QPushButton { background: #263a53; color: #f2f6fc; border: 1px solid #7186a1;
                border-radius: 8px; min-height: 32px; padding: 5px 14px; font-size: 15px; }
            QPushButton:disabled { color: #9aaabe; background: #1c2d43; }
            QCheckBox { color: #f2f6fc; font-size: 15px; }
        """)
        self.clock = ProgressClock()
        self.events = []
        self.active = True
        self.reduced_motion = reduced_motion
        self.stop_callback = None
        self.error = None
        self.result = None
        self.mark = ActivityFlag()

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.inspector = MultiProgressView(title)
        body.addWidget(self.inspector, 1)
        self.summary = self.inspector.activity_label
        self.stage = self.inspector.phase_label
        self.progress = self.inspector.unit_progress
        self.counts = self.inspector.progress_note
        self.log = self.inspector.log

        footer = QWidget()
        footer.setObjectName("progressActions")
        buttons = QHBoxLayout(footer)
        self.notification = QCheckBox("Oznámit výsledek elektronickou poštou")
        self.notification.setObjectName("operation.notification")
        self.notification.hide()
        buttons.addWidget(self.notification)
        self.close_button = QPushButton("Skrýt průběh")
        self.close_button.setObjectName("operation.hide")
        self.close_button.setAccessibleName("Skrýt průběh")
        self.close_button.clicked.connect(self.hide)
        buttons.addWidget(self.close_button)
        self.stop = QPushButton("Zastavit")
        self.stop.setObjectName("operation.stop")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.request_stop)
        buttons.addWidget(self.stop)
        self.details = QPushButton("Podrobnosti chyby")
        self.details.setObjectName("operation.details")
        self.details.hide()
        self.details.clicked.connect(self.show_details)
        buttons.addWidget(self.details)
        self.result_button = QPushButton("Výsledek")
        self.result_button.setObjectName("operation.result")
        self.result_button.hide()
        self.result_button.clicked.connect(self.show_result)
        buttons.addWidget(self.result_button)
        buttons.addStretch()
        body.addWidget(footer)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def showEvent(self, event):
        bounds = self.screen().availableGeometry()
        self.resize(min(self.width(), bounds.width()), min(self.height(), bounds.height()))
        self.mark.set_running(self.active, self.reduced_motion)
        super().showEvent(event)

    def on_event(self, event):
        self.events.append(event)
        self.events[:] = self.events[-2000:]
        self.clock.update(event)
        self.inspector.on_event(event, self.clock)

    def tick(self):
        self.inspector.refresh(self.clock)

    def request_stop(self):
        if not self.active or not self.stop_callback:
            return
        self.stop.setEnabled(False)
        self.on_event(ProgressEvent("RUN", "cancelling"))
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
        self.on_event(ProgressEvent("RUN", state, detail=error.message if error else ""))
        self.details.setVisible(error is not None)
        self.result_button.setVisible(self.result is not None)

    def show_details(self):
        if self.error:
            DetailDialog("Podrobnosti chyby", self.error.message, self,
                         self.error.detail + "\n\n" + self.error.next_step).exec()

    def show_result(self):
        result = asdict(self.result) if is_dataclass(self.result) else self.result
        DetailDialog("Výsledek operace", "Vrácené podklady jsou v technických podrobnostech.",
                     self, result).exec()

    def closeEvent(self, event):
        if self.active:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)
