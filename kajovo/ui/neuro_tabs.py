from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QObject, QEvent, QRect, QPointF, Property, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QTabWidget, QWidget, QGraphicsOpacityEffect


@dataclass
class _AnimState:
    active_index: int = 0
    progress: float = 0.0  # 0..1 during switch
    pulse: float = 0.0     # subtle breathing


class NeuralThreadsOverlay(QWidget):
    """Paints "threads" from the app logo to each tab.

    This widget is transparent for mouse events and is meant to be placed as a child
    of QTabBar (same geometry), so it naturally tracks tab movement and resizing.
    """

    def __init__(self, tab_widget: QTabWidget, logo_widget: QWidget):
        super().__init__(tab_widget.tabBar())
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._tabs = tab_widget
        self._logo = logo_widget

        self._state = _AnimState(active_index=tab_widget.currentIndex())

        self._switch_anim = QPropertyAnimation(self, b"switchProgress", self)
        self._switch_anim.setDuration(340)
        self._switch_anim.setEasingCurve(QEasingCurve.InOutCubic)

        self._pulse_anim = QPropertyAnimation(self, b"pulse", self)
        self._pulse_anim.setDuration(2200)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.start()

        tab_widget.currentChanged.connect(self._on_tab_changed)
        tab_widget.tabBar().installEventFilter(self)
        tab_widget.installEventFilter(self)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() in (QEvent.Resize, QEvent.Move, QEvent.LayoutRequest, QEvent.Show):
            self.setGeometry(self.parentWidget().rect())
            self.update()
        return super().eventFilter(obj, ev)

    def _on_tab_changed(self, idx: int) -> None:
        self._state.active_index = idx
        self._switch_anim.stop()
        self._switch_anim.setStartValue(0.0)
        self._switch_anim.setEndValue(1.0)
        self._switch_anim.start()

    def _logo_point_local(self) -> QPointF:
        # Map logo center to this overlay coordinate space.
        p = self._logo.mapToGlobal(self._logo.rect().center())
        return QPointF(self.mapFromGlobal(p))

    def _tab_center(self, idx: int) -> QPointF:
        tb = self._tabs.tabBar()
        r: QRect = tb.tabRect(idx)
        # slightly under the tab label, looks more "wired"
        return QPointF(r.center().x(), r.bottom() - 2)

    def paintEvent(self, _ev) -> None:
        if self._tabs.count() <= 0:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        origin = self._logo_point_local()

        teal = QColor("#3DB8C0")
        lilac = QColor("#B69FDD")
        ink = QColor("#0F111A")
        coral = QColor("#E04050")

        # A subtle breathing width.
        pulse = 0.65 + 0.35 * (1.0 - abs(0.5 - self._state.pulse) * 2.0)

        for i in range(self._tabs.count()):
            dst = self._tab_center(i)

            # Control points create a "neural" arc.
            mid_x = (origin.x() + dst.x()) * 0.5
            c1 = QPointF(mid_x, origin.y() + 6)
            c2 = QPointF(mid_x, dst.y() - 14)

            path = QPainterPath(origin)
            path.cubicTo(c1, c2, dst)

            active = (i == self._state.active_index)
            base_alpha = 140 if active else 64
            glow_alpha = 110 if active else 0

            # Background "shadow" to separate lines from tabs.
            shadow_pen = QPen(ink)
            shadow_pen.setWidthF(3.8 * pulse)
            shadow_pen.setCapStyle(Qt.RoundCap)
            shadow_pen.setJoinStyle(Qt.RoundJoin)
            shadow_pen.setColor(QColor(ink.red(), ink.green(), ink.blue(), 140 if active else 90))
            p.setPen(shadow_pen)
            p.drawPath(path)

            # Main thread line.
            pen = QPen(teal if not active else lilac)
            pen.setWidthF((2.0 if not active else 2.6) * pulse)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setColor(QColor(pen.color().red(), pen.color().green(), pen.color().blue(), base_alpha))
            p.setPen(pen)
            p.drawPath(path)

            # Active pulse accent (a small "spark").
            if active:
                t = self._state.progress
                spark = path.pointAtPercent(0.15 + 0.78 * t)
                spark_pen = QPen(coral)
                spark_pen.setWidthF(3.2)
                spark_pen.setCapStyle(Qt.RoundCap)
                spark_pen.setColor(QColor(coral.red(), coral.green(), coral.blue(), glow_alpha))
                p.setPen(spark_pen)
                p.drawPoint(spark)

        p.end()

    def _get_switch(self) -> float:
        return self._state.progress

    def _set_switch(self, v: float) -> None:
        self._state.progress = float(v)
        self.update()

    switchProgress = Property(float, _get_switch, _set_switch)

    def _get_pulse(self) -> float:
        return self._state.pulse

    def _set_pulse(self, v: float) -> None:
        self._state.pulse = float(v)
        self.update()

    pulse = Property(float, _get_pulse, _set_pulse)


class NeuroTabWidget(QTabWidget):
    """QTabWidget with branded "neural" transitions."""

    def __init__(self, *, logo_widget: QWidget, parent=None):
        super().__init__(parent)
        self._logo_widget = logo_widget
        self._overlay = NeuralThreadsOverlay(self, logo_widget)
        self._overlay.setGeometry(self.tabBar().rect())

        self._last_index = self.currentIndex()
        self.currentChanged.connect(self._animate_page)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._overlay.setGeometry(self.tabBar().rect())

    def _animate_page(self, idx: int) -> None:
        try:
            w = self.widget(idx)
            if not w:
                return

            eff = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(eff)
            eff.setOpacity(0.0)

            anim = QPropertyAnimation(eff, b"opacity", w)
            anim.setDuration(260)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)

            # A light "reveal" shift (keeps layout intact).
            start_pos = w.pos()
            w.move(start_pos.x() + 10, start_pos.y())
            slide = QPropertyAnimation(w, b"pos", w)
            slide.setDuration(260)
            slide.setStartValue(w.pos())
            slide.setEndValue(start_pos)
            slide.setEasingCurve(QEasingCurve.OutCubic)

            def _cleanup():
                # Do not keep effects forever; reduces rendering overhead.
                w.setGraphicsEffect(None)

            anim.finished.connect(_cleanup)
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            slide.start(QPropertyAnimation.DeleteWhenStopped)
        finally:
            self._last_index = idx
