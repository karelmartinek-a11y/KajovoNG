from __future__ import annotations

from PySide6.QtCore import QThread

from kajovo.core.runs.executor import RunExecutor
from kajovo.studio.workers.run_worker import RunWorker


def test_qt_worker_is_adapter_and_core_executor_is_not_qthread():
    assert issubclass(RunWorker, QThread)
    assert not issubclass(RunExecutor, QThread)
    for name in ("progress", "progress_event", "subprogress", "status", "logline", "finished_ok", "finished_err", "failure_detail"):
        assert hasattr(RunWorker, name)
