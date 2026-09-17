from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor


class CascadeRunWorker(QThread):
    """Tenký Qt adaptér; cascade business logiku vlastní CascadeRunExecutor."""

    progress = Signal(int)
    progress_event = Signal(object)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    failure_detail = Signal(object)

    def __init__(
        self,
        cfg: CascadeRunConfig,
        settings: Any,
        api_key: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._executor = CascadeRunExecutor(cfg, settings, api_key)
        self._executor.progress.connect(self.progress.emit)
        self._executor.progress_event.connect(self.progress_event.emit)
        self._executor.subprogress.connect(self.subprogress.emit)
        self._executor.status.connect(self.status.emit)
        self._executor.logline.connect(self.logline.emit)
        self._executor.finished_ok.connect(self.finished_ok.emit)
        self._executor.finished_err.connect(self.finished_err.emit)
        self._executor.failure_detail.connect(self.failure_detail.emit)

    @property
    def cfg(self) -> CascadeRunConfig:
        return self._executor.cfg

    @property
    def settings(self) -> Any:
        return self._executor.settings

    @property
    def api_key(self) -> str:
        return self._executor.api_key

    @property
    def logger(self) -> Any:
        return self._executor.logger

    def request_stop(self) -> None:
        self._executor.request_stop()

    def run(self) -> None:
        self._executor.execute()
