from PySide6.QtCore import QThread

from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
from kajovo.core.cascade_types import CascadeDefinition
from kajovo.core.config import AppSettings
from kajovo.studio.workers.cascade_worker import CascadeRunWorker


def test_cascade_worker_is_thin_qthread_adapter():
    config = CascadeRunConfig("test", CascadeDefinition("test"), "", "out")
    worker = CascadeRunWorker(config, AppSettings(), "test")
    assert isinstance(worker, QThread)
    assert isinstance(worker._executor, CascadeRunExecutor)
    assert worker.cfg is worker._executor.cfg
    assert worker.settings is worker._executor.settings
    assert worker.api_key == "test"


def test_cascade_worker_forwards_stop_request():
    config = CascadeRunConfig("test", CascadeDefinition("test"), "", "out")
    worker = CascadeRunWorker(config, AppSettings(), "test")
    worker.request_stop()
    assert worker._executor._stop is True
