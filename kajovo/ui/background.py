"""Izolace blokujícího I/O od vykreslovacího vlákna Qt."""

from functools import wraps
from PySide6.QtCore import QCoreApplication, QEventLoop, QThread
from ..core.openai_client import OpenAIClient as CoreClient
from ..core.retry import with_retry as core_with_retry


class _Call(QThread):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.function()
        except BaseException as exc:
            self.error = exc


def run_io(function):
    """Zachová návratovou hodnotu; čekání obsluhuje vykreslení a časovače.

    Během této krátké synchronní hranice se nepřijímají nové uživatelské
    vstupy. Dlouhé rušitelné úlohy používají vlastní worker a signály.
    """
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() != app.thread():
        return function()
    task = _Call(function)
    loop = QEventLoop()
    task.finished.connect(loop.quit)
    task.start()
    loop.exec(QEventLoop.ExcludeUserInputEvents)
    task.wait()
    if task.error is not None:
        raise task.error
    return task.result


def with_retry(function, config, breaker):
    return run_io(lambda: core_with_retry(function, config, breaker))


class OpenAIClient(CoreClient):
    """UI adaptér; core a CLI zůstávají nezávislé na životním cyklu oken."""

    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name.startswith("_") or not callable(value):
            return value

        @wraps(value)
        def call(*args, **kwargs):
            return run_io(lambda: value(*args, **kwargs))

        return call
