from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter, QPixmap, QColor, QLinearGradient, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QGraphicsOpacityEffect


class SplashScreen(QWidget):
    """Lightweight, frameless splash to show the new brand on startup."""

    def __init__(self, *, title: str = "Kájovo NG", subtitle: str = "Neural console", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.SplashScreen | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(860, 520)

        v = QVBoxLayout(self)
        v.setContentsMargins(28, 28, 28, 28)
        v.setSpacing(12)

        self._logo = QLabel()
        self._logo.setAlignment(Qt.AlignCenter)

        base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "resources"))
        logo_path = os.path.join(base, "Kajovo_new.png")
        pm = QPixmap(logo_path) if os.path.exists(logo_path) else QPixmap()
        if not pm.isNull():
            self._logo.setPixmap(pm.scaled(170, 170, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        v.addStretch(1)
        v.addWidget(self._logo)

        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setStyleSheet("font-size: 26px; font-weight: 800;")
        v.addWidget(self._title)

        self._sub = QLabel(subtitle)
        self._sub.setAlignment(Qt.AlignCenter)
        self._sub.setStyleSheet("color: #B69FDD; font-size: 12px;")
        v.addWidget(self._sub)

        self._hint = QLabel("Loading workspace…")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setStyleSheet("color: #D6DDE7; font-size: 11px;")
        v.addWidget(self._hint)
        v.addStretch(1)

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(0.0)

        self._fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_in.setDuration(320)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)

        self._fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_out.setDuration(260)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self.close)

        QTimer.singleShot(0, self._fade_in.start)

    def set_status(self, text: str) -> None:
        self._hint.setText(text)

    def finish(self) -> None:
        if self._fade_out.state() != QPropertyAnimation.Running:
            self._fade_out.start()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # rounded card background with a subtle gradient (colors from the new logo)
        rect = self.rect().adjusted(6, 6, -6, -6)
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, QColor("#0F111A"))
        grad.setColorAt(0.55, QColor("#141826"))
        grad.setColorAt(1.0, QColor("#101423"))

        p.setPen(QColor(61, 184, 192, 140))
        p.setBrush(grad)
        p.drawRoundedRect(rect, 22, 22)

        # inner border
        inner = rect.adjusted(1, 1, -1, -1)
        p.setPen(QColor(141, 121, 196, 120))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(inner, 22, 22)

        p.end()
