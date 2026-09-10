"""Čas a stáří poslední doložené události pro pomocné operace."""

import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QLabel


class ActivityLine(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.started = self.last = time.monotonic()
        self.ended = None
        self.setWordWrap(True)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def touch(self):
        self.last = time.monotonic()
        self.refresh()

    def finish(self):
        self.ended = time.monotonic()
        self.timer.stop()
        self.refresh()

    def refresh(self):
        now = self.ended if self.ended is not None else time.monotonic()
        self.setText(
            f"Trvání: {int(now - self.started)} s · Poslední událost před {int(now - self.last)} s"
            + (" · ETA nelze určit · Obnovování UI: 1 s" if self.ended is None else "")
        )
