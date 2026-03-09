__all__ = []
__version__ = '1.0.0'

import sys
import types

def _ensure_qt_stub():
    # If PySide6 already importable, bail early
    try:
        import PySide6  # noqa
        return
    except ModuleNotFoundError:
        pass

    # If we already stubbed, bail
    if 'PySide6' in sys.modules:
        return

    # Create minimal stub for headless/test environments
    qt_stub = types.ModuleType("PySide6")
    qt_core_stub = types.ModuleType("PySide6.QtCore")

    class _DummySignal:
        def __init__(self, *args, **kwargs):
            pass
        def connect(self, *args, **kwargs):
            pass
        def emit(self, *args, **kwargs):
            pass

    class QObject:
        def __init__(self, *args, **kwargs):
            pass

    class QThread:
        def __init__(self, *args, **kwargs):
            pass

    def Signal(*args, **kwargs):
        return _DummySignal()

    qt_core_stub.Signal = Signal
    qt_core_stub.QObject = QObject
    qt_core_stub.QThread = QThread
    qt_stub.QtCore = qt_core_stub
    sys.modules["PySide6"] = qt_stub
    sys.modules["PySide6.QtCore"] = qt_core_stub

_ensure_qt_stub()
