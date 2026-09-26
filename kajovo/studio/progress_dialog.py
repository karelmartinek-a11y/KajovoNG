"""Životní cyklus nového okna průběhu; konec potvrzuje správce pracovníka."""

from dataclasses import asdict, is_dataclass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QDialog, QGridLayout, QPushButton, QVBoxLayout

from kajovo.core.progress import ProgressClock, ProgressEvent
from .components import DetailDialog
from .progress_view import MultiProgressView, label


class MultiProgressDialog(QDialog):
    def __init__(self, title, parent=None, reduced_motion=False):
        super().__init__(parent)
        self.setObjectName("operation.progress")
        self.setWindowTitle(title)
        self.resize(1080, 820)
        self.setMinimumSize(380, 320)
        self.reduced_motion = reduced_motion
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 12)
        self.inspector = MultiProgressView(title, self)
        root.addWidget(self.inspector, 1)
        self.summary = self.inspector.activity_label
        self.stage = self.inspector.phase_label
        self.counts = self.inspector.progress_note
        self.progress = self.inspector.unit_progress
        self.log = self.inspector.log
        self.mark = self.inspector.ring
        self.lifecycle_hint = label("", "muted")
        root.addWidget(self.lifecycle_hint)
        self.notification = QCheckBox("Oznámit výsledek e-mailem")
        self.notification.setObjectName("operation.notification")
        root.addWidget(self.notification)
        self.notification.hide()
        self.actions = QGridLayout()
        root.addLayout(self.actions)
        self.action_buttons = []
        for name, button_title, callback in (
            ("close_button", "Skrýt průběh", self.hide),
            ("stop", "Zastavit", self.request_stop),
            ("details", "Podrobnosti chyby", self.show_details),
            ("result_button", "Výsledek", self.show_result),
        ):
            button = QPushButton(button_title)
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            setattr(self, name, button)
            self.actions.addWidget(button, 0, len(self.action_buttons))
            self.action_buttons.append(button)
        self.close_button.setObjectName("operation.hide")
        self.stop.setObjectName("operation.stop")
        self.details.setObjectName("operation.details")
        self.result_button.setObjectName("operation.result")
        self.setStyleSheet('''
            QDialog { background: #1e2b43; color: #f4f5fb; }
            QPushButton { color: #f4f5fb; background: #2d3c58; border: 1px solid #667693;
                border-radius: 8px; padding: 10px 12px; font-size: 15px; }
            QPushButton:focus { border: 2px solid #b6a5ff; }
            QPushButton:disabled { color: #aebbd2; }
            QCheckBox { color: #f4f5fb; }
        ''')
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.restart(title)

    def restart(self, title):
        self.clock = ProgressClock()
        self.events = []
        self.active = True
        self.stop_callback = None
        self.stop_requested = False
        self.result = self.error = None
        self.setWindowTitle(title)
        self.inspector.reset(title)
        self.lifecycle_hint.setText("Skrytí okna práci nezastaví. Zastavení čeká na potvrzení pracovního procesu.")
        self.stop.setVisible(True)
        self.stop.setEnabled(False)
        self.details.hide()
        self.result_button.hide()
        self.close_button.setText("Skrýt průběh")
        self.close_button.setAccessibleName("Skrýt průběh")
        self.close_button.setDefault(False)
        self.notification.setEnabled(True)
        self.mark.set_running(True)
        self.timer.start()
        self.layout_actions()

    def on_event(self, event):
        self.events.append(event)
        del self.events[:-2000]
        self.clock.update(event)
        self.inspector.on_event(event, self.clock)
        if self.stop_requested and self.active:
            self.tick()

    def tick(self):
        self.inspector.refresh(self.clock)
        if self.stop_requested and self.active:
            self.inspector.state_label.setText("Čekáme na potvrzení zastavení")
            self.inspector.activity_label.setText("Žádost byla předána pracovnímu procesu. Čekáme na jeho bezpečné ukončení.")

    def request_stop(self):
        if self.active and self.stop_callback and not self.stop_requested:
            self.stop_requested = True
            self.stop.setEnabled(False)
            self.stop_callback()
            # Místní žádost není potvrzení vzdáleného zrušení ani zpráva služby.
            self.tick()

    def finish(self, state, error=None):
        self.active = False
        self.error = error
        self.inspector.model.received = True
        self.inspector.model.terminal = state
        event = ProgressEvent("RUN", state, detail=error.message if error else "")
        self.on_event(event)
        self.clock.finished = event.timestamp
        self.timer.stop()
        self.mark.set_running(False)
        self.stop.hide()
        self.lifecycle_hint.setText("Okno můžete zavřít tlačítkem OK.")
        self.stop.setEnabled(False)
        self.notification.setEnabled(False)
        self.details.setVisible(error is not None)
        self.result_button.setVisible(self.result is not None)
        self.close_button.setText("OK")
        self.close_button.setAccessibleName("OK")
        self.close_button.setDefault(True)
        if self.isVisible():
            self.close_button.setFocus()
        self.layout_actions()

    def show_details(self):
        if self.error is not None:
            DetailDialog("Podrobnosti chyby", self.error.message, self,
                         self.error.detail + "\n\n" + self.error.next_step).exec()

    def show_result(self):
        value = asdict(self.result) if is_dataclass(self.result) else self.result
        DetailDialog("Výsledek operace", "Výsledek vrácený pracovním procesem", self, value).exec()

    def reject(self):
        self.hide()

    def closeEvent(self, event):
        if self.active:
            event.ignore()
            self.hide()
        else:
            event.accept()

    def showEvent(self, event):
        area = self.screen().availableGeometry()
        self.resize(min(self.width(), area.width()), min(self.height(), area.height()))
        super().showEvent(event)

    def resizeEvent(self, event):
        if hasattr(self, "active"):
            self.layout_actions()
        super().resizeEvent(event)

    def layout_actions(self):
        for button in self.action_buttons:
            self.actions.removeWidget(button)
        buttons = [self.close_button]
        if self.active:
            buttons.append(self.stop)
        if self.error is not None:
            buttons.append(self.details)
        if self.result is not None and not self.active:
            buttons.append(self.result_button)
        columns = min(len(buttons), 2 if self.width() < 720 else 4)
        for index, button in enumerate(buttons):
            span = columns if index == len(buttons) - 1 and index % columns == 0 else 1
            self.actions.addWidget(button, index // columns, index % columns, 1, span)
