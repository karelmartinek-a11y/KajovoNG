"""Klidný pohyb místního rozhraní bez odvozování postupu nebo aktivity služby."""

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QVariantAnimation, Signal


class PulseController(QObject):
    """Sdílený pulz; skrytý widget a omezený pohyb nezatěžují animační smyčku."""

    changed = Signal(float)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.value = 1.0
        self.running = False
        self.reduced_motion = False
        self.mode = "heartbeat"
        self.animation = QVariantAnimation(self)
        self.animation.setLoopCount(-1)
        self.animation.setEasingCurve(QEasingCurve.InOutSine)
        self.animation.valueChanged.connect(self._set_value)
        owner.installEventFilter(self)
        self._configure()

    def _set_value(self, value):
        self.value = float(value)
        self.changed.emit(self.value)

    def _configure(self):
        if self.mode == "waiting":
            self.animation.setDuration(2800)
            self.animation.setKeyValues([(0.0, 0.22), (0.5, 0.72), (1.0, 0.22)])
        else:
            self.animation.setDuration(2000)
            self.animation.setKeyValues([
                (0.0, 0.22), (0.12, 1.0), (0.24, 0.28),
                (0.36, 0.80), (0.55, 0.22), (1.0, 0.22),
            ])

    def set_running(self, active, reduced_motion=False, mode="heartbeat"):
        mode = "waiting" if mode == "waiting" else "heartbeat"
        if mode != self.mode:
            self.animation.stop()
            self.mode = mode
            self._configure()
        self.running = bool(active)
        self.reduced_motion = bool(reduced_motion)
        self.sync_visibility()

    def sync_visibility(self):
        animate = self.running and not self.reduced_motion and self.owner.isVisible()
        if animate:
            if self.animation.state() != QVariantAnimation.Running:
                self.animation.start()
        else:
            self.animation.stop()
            self._set_value(1.0)

    def eventFilter(self, watched, event):
        if watched is self.owner:
            if event.type() in {QEvent.Hide, QEvent.HideToParent, QEvent.Close}:
                self.animation.stop()
                self._set_value(1.0)
            elif event.type() in {QEvent.Show, QEvent.ShowToParent}:
                self.sync_visibility()
        return super().eventFilter(watched, event)
