from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require_once(text: str, needle: str, label: str) -> None:
    count = text.count(needle)
    if count != 1:
        raise RuntimeError(f"{label}: očekáván 1 výskyt, nalezeno {count}")


def transform_relative_imports(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^(\s*)from \.runs\.(.+)$", line)
        if match:
            lines.append(f"{match.group(1)}from .{match.group(2)}")
            continue
        match = re.match(r"^(\s*)from \.(?!\.)(.+)$", line)
        if match:
            lines.append(f"{match.group(1)}from ..{match.group(2)}")
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def build_executor() -> None:
    source_path = ROOT / "kajovo/core/pipeline.py"
    target_path = ROOT / "kajovo/core/runs/executor.py"
    text = source_path.read_text(encoding="utf-8")

    require_once(
        text,
        "from PySide6.QtCore import QObject, Signal, QThread, QLockFile\n",
        "Qt import",
    )
    text = text.replace(
        "from PySide6.QtCore import QObject, Signal, QThread, QLockFile\n",
        "",
        1,
    )
    text = transform_relative_imports(text)

    signal_block = '''class RunWorker(QThread):
    progress = Signal(int)
    progress_event = Signal(object)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    failure_detail = Signal(object)
'''
    require_once(text, signal_block, "RunWorker signal block")
    text = text.replace(signal_block, "class RunExecutor:\n", 1)
    require_once(text, "parent: Optional[QObject] = None,", "QObject parent type")
    text = text.replace("parent: Optional[QObject] = None,", "parent: Optional[object] = None,", 1)
    require_once(text, "        super().__init__(parent)\n", "QThread constructor")
    text = text.replace("        super().__init__(parent)\n", "        del parent\n", 1)

    imports_anchor = "from .observability import emit_signal, record_event, update_state as update_run_state\n"
    require_once(text, imports_anchor, "run-local import anchor")
    text = text.replace(
        imports_anchor,
        imports_anchor
        + "from .cancellation import CancellationToken\n"
        + "from .locking import ExecutionLock\n"
        + "from .ports import EventPort\n",
        1,
    )

    init_anchor = "        self.log = run_logger\n"
    require_once(text, init_anchor, "executor init anchor")
    text = text.replace(
        init_anchor,
        init_anchor
        + "        self.progress = EventPort()\n"
        + "        self.progress_event = EventPort()\n"
        + "        self.subprogress = EventPort()\n"
        + "        self.status = EventPort()\n"
        + "        self.logline = EventPort()\n"
        + "        self.finished_ok = EventPort()\n"
        + "        self.finished_err = EventPort()\n"
        + "        self.failure_detail = EventPort()\n"
        + "        self.cancel_token = CancellationToken()\n",
        1,
    )

    old_stop = '''    def request_stop(self):
        self._stop = True
'''
    require_once(text, old_stop, "request_stop")
    text = text.replace(
        old_stop,
        '''    def request_stop(self):
        self._stop = True
        self.cancel_token.cancel()
''',
        1,
    )
    old_check = '''    def _check_stop(self):
        if self._stop or self._cancel_response:
            raise RuntimeError("STOP_REQUESTED")
'''
    require_once(text, old_check, "_check_stop")
    text = text.replace(
        old_check,
        '''    def _check_stop(self):
        if self.cancel_token.is_cancelled() or self._stop or self._cancel_response:
            raise RuntimeError("STOP_REQUESTED")
''',
        1,
    )

    run_anchor = "    def run(self):\n"
    require_once(text, run_anchor, "run method")
    text = text.replace(
        run_anchor,
        "    def execute(self):\n        self.run()\n\n    def run(self):\n",
        1,
    )

    old_lock = '''        lock = QLockFile(str(Path(self.log.paths.run_dir) / "execution.lock"))
        lock.setStaleLockTime(0)
        if not lock.tryLock(0):
'''
    require_once(text, old_lock, "QLockFile acquire")
    text = text.replace(
        old_lock,
        '''        lock = ExecutionLock(Path(self.log.paths.run_dir) / "execution.lock")
        if not lock.acquire():
''',
        1,
    )
    require_once(text, "            lock.unlock()\n", "QLockFile release")
    text = text.replace("            lock.unlock()\n", "            lock.release()\n", 1)

    forbidden = ("PySide6", "QThread", "Signal(", "QObject", "QLockFile")
    leftovers = [token for token in forbidden if token in text]
    if leftovers:
        raise RuntimeError(f"Executor stále obsahuje Qt symboly: {leftovers}")
    target_path.write_text(text, encoding="utf-8")

    source_path.write_text(
        '''"""Qt-free kompatibilní fasáda kanonické run orchestrace."""

from .runs.config import UiRunConfig
from .runs.executor import RunExecutor, split_text

__all__ = ["RunExecutor", "UiRunConfig", "split_text"]
''',
        encoding="utf-8",
    )


def write_studio_worker() -> None:
    workers = ROOT / "kajovo/studio/workers"
    workers.mkdir(parents=True, exist_ok=True)
    (workers / "__init__.py").write_text(
        '''"""Qt adaptéry nad framework-independent core orchestrací."""

from .run_worker import RunWorker

__all__ = ["RunWorker"]
''',
        encoding="utf-8",
    )
    (workers / "run_worker.py").write_text(
        '''from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from kajovo.core.runs.config import UiRunConfig
from kajovo.core.runs.executor import RunExecutor


class RunWorker(QThread):
    """Tenký Qt adaptér; business rozhodnutí vlastní výhradně RunExecutor."""

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
        cfg: UiRunConfig,
        settings: Any,
        api_key: str,
        run_logger: Any,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._executor = RunExecutor(cfg, settings, api_key, run_logger)
        self._executor.progress.connect(self.progress.emit)
        self._executor.progress_event.connect(self.progress_event.emit)
        self._executor.subprogress.connect(self.subprogress.emit)
        self._executor.status.connect(self.status.emit)
        self._executor.logline.connect(self.logline.emit)
        self._executor.finished_ok.connect(self.finished_ok.emit)
        self._executor.finished_err.connect(self.finished_err.emit)
        self._executor.failure_detail.connect(self.failure_detail.emit)

    @property
    def cfg(self) -> UiRunConfig:
        return self._executor.cfg

    @property
    def settings(self) -> Any:
        return self._executor.settings

    @property
    def api_key(self) -> str:
        return self._executor.api_key

    @property
    def log(self) -> Any:
        return self._executor.log

    @property
    def resume_generate_batch(self) -> Any:
        return getattr(self._executor, "resume_generate_batch", None)

    @resume_generate_batch.setter
    def resume_generate_batch(self, value: Any) -> None:
        self._executor.resume_generate_batch = value

    def request_stop(self) -> None:
        self._executor.request_stop()

    def request_cancel_response(self) -> None:
        self._executor.request_cancel_response()

    def run(self) -> None:
        self._executor.execute()
''',
        encoding="utf-8",
    )


def migrate_product_imports() -> None:
    replacements = {
        ROOT / "kajovo/studio/workbench.py": (
            "from kajovo.core.pipeline import RunWorker, UiRunConfig\n",
            "from kajovo.core.runs.config import UiRunConfig\nfrom kajovo.studio.workers.run_worker import RunWorker\n",
        ),
        ROOT / "kajovo/studio/history_launcher.py": (
            "from kajovo.core.pipeline import RunWorker, UiRunConfig\n",
            "from kajovo.core.runs.config import UiRunConfig\nfrom kajovo.studio.workers.run_worker import RunWorker\n",
        ),
        ROOT / "kajovo/desktop/application.py": (
            "from ..core.pipeline import UiRunConfig, RunWorker\n",
            "from ..core.runs.config import UiRunConfig\nfrom ..studio.workers.run_worker import RunWorker\n",
        ),
    }
    for path, (old, new) in replacements.items():
        text = path.read_text(encoding="utf-8")
        require_once(text, old, str(path.relative_to(ROOT)))
        path.write_text(text.replace(old, new, 1), encoding="utf-8")


def migrate_tests() -> None:
    history = ROOT / "tests/test_history_forensic.py"
    history_text = history.read_text(encoding="utf-8")
    qthread_import = "    from kajovo.core.pipeline import RunWorker\n"
    require_once(history_text, qthread_import, "history QThread RunWorker import")
    history.write_text(
        history_text.replace(
            qthread_import,
            "    from kajovo.studio.workers.run_worker import RunWorker\n",
            1,
        ),
        encoding="utf-8",
    )

    for path in sorted((ROOT / "tests").glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "from kajovo.core.pipeline import UiRunConfig, RunWorker",
            "from kajovo.core.runs.config import UiRunConfig\nfrom kajovo.core.runs.executor import RunExecutor as RunWorker",
        )
        text = text.replace(
            "from kajovo.core.pipeline import RunWorker, UiRunConfig",
            "from kajovo.core.runs.config import UiRunConfig\nfrom kajovo.core.runs.executor import RunExecutor as RunWorker",
        )
        text = text.replace(
            "from kajovo.core.pipeline import RunWorker",
            "from kajovo.core.runs.executor import RunExecutor as RunWorker",
        )
        text = text.replace(
            "from kajovo.core.pipeline import UiRunConfig",
            "from kajovo.core.runs.config import UiRunConfig",
        )
        text = text.replace(
            "kajovo.core.pipeline.OpenAIClient",
            "kajovo.core.runs.executor.OpenAIClient",
        )
        path.write_text(text, encoding="utf-8")

    adapter_test = ROOT / "tests/test_run_worker_adapter.py"
    adapter_test.write_text(
        '''from __future__ import annotations

from PySide6.QtCore import QThread

from kajovo.core.runs.executor import RunExecutor
from kajovo.studio.workers.run_worker import RunWorker


def test_qt_worker_is_adapter_and_core_executor_is_not_qthread():
    assert issubclass(RunWorker, QThread)
    assert not issubclass(RunExecutor, QThread)
    for name in ("progress", "progress_event", "subprogress", "status", "logline", "finished_ok", "finished_err", "failure_detail"):
        assert hasattr(RunWorker, name)
''',
        encoding="utf-8",
    )


def harden_architecture_test() -> None:
    path = ROOT / "tests/test_architecture_contracts.py"
    text = path.read_text(encoding="utf-8")
    marker = "\ndef test_new_runs_layer_is_qt_free():\n"
    require_once(text, marker, "architecture insertion marker")
    addition = '''
def test_pipeline_facade_is_qt_free():
    """Legacy název modulu smí zůstat jen jako Qt-free core fasáda."""
    path = ROOT / "kajovo" / "core" / "pipeline.py"
    violations = _violations([path], ("PySide6",))
    assert not violations, violations

'''
    text = text.replace(marker, "\n" + addition + "def test_new_runs_layer_is_qt_free():\n", 1)
    path.write_text(text, encoding="utf-8")


def validate_tree() -> None:
    executor = (ROOT / "kajovo/core/runs/executor.py").read_text(encoding="utf-8")
    facade = (ROOT / "kajovo/core/pipeline.py").read_text(encoding="utf-8")
    if "PySide6" in executor or "PySide6" in facade:
        raise RuntimeError("Core run orchestrace stále obsahuje PySide6.")
    for base in (ROOT / "kajovo", ROOT / "tests"):
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "from kajovo.core.pipeline import RunWorker" in text:
                raise RuntimeError(f"Legacy RunWorker import zůstal v {path.relative_to(ROOT)}")
    if "from ..core.pipeline import UiRunConfig, RunWorker" in (ROOT / "kajovo/desktop/application.py").read_text(encoding="utf-8"):
        raise RuntimeError("Legacy desktop stále importuje RunWorker z core.pipeline.")


def main() -> None:
    build_executor()
    write_studio_worker()
    migrate_product_imports()
    migrate_tests()
    harden_architecture_test()
    validate_tree()
    print("RunWorker byl oddělen od Qt-free RunExecutoru.")


if __name__ == "__main__":
    main()
