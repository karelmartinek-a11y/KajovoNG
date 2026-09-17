from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "kajovo" / "core" / "cascade_pipeline.py"
STUDIO = ROOT / "kajovo" / "studio" / "cascades.py"
HISTORY = ROOT / "kajovo" / "studio" / "history_launcher.py"
DESKTOP = ROOT / "kajovo" / "desktop" / "application.py"
WORKER = ROOT / "kajovo" / "studio" / "workers" / "cascade_worker.py"
TEST_CASCADE = ROOT / "tests" / "test_cascade.py"
TEST_CASCADE_V2 = ROOT / "tests" / "test_cascade_v2.py"
TEST_WORKFLOWS = ROOT / "tests" / "test_workflows.py"
TEST_ARCH = ROOT / "tests" / "test_architecture_contracts.py"
TEST_ADAPTER = ROOT / "tests" / "test_cascade_worker_adapter.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        if text.count(old) != 1:
            raise RuntimeError(f"Nejednoznačná migrace v {path}: {old!r}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return
    if new in text:
        return
    raise RuntimeError(f"Chybí očekávaný migrační vzor v {path}: {old!r}")


def transform_core() -> None:
    text = CORE.read_text(encoding="utf-8")
    old_import = "from PySide6.QtCore import QObject, QThread, Signal\n"
    new_import = "from .runs.ports import EventPort\n"
    if old_import in text:
        text = text.replace(old_import, new_import, 1)
    elif new_import not in text:
        raise RuntimeError("Cascade core nemá očekávaný Qt import ani EventPort import.")

    if "import contextlib\n" not in text:
        if "import copy\n" not in text:
            raise RuntimeError("Cascade core nemá očekávaný import copy pro vložení contextlib.")
        text = text.replace("import copy\n", "import contextlib\nimport copy\n", 1)

    old_header = '''class CascadeRunWorker(QThread):
    progress = Signal(int)
    progress_event = Signal(object)
    subprogress = Signal(int)
    status = Signal(str)
    logline = Signal(str)
    finished_ok = Signal(dict)
    finished_err = Signal(str)
    failure_detail = Signal(object)

    STEP_ATTEMPTS = 3
'''
    new_header = '''class CascadeRunExecutor:
    """Qt-free executor kaskády; thread lifecycle vlastní pouze UI adaptér."""

    STEP_ATTEMPTS = 3
'''
    if old_header in text:
        text = text.replace(old_header, new_header, 1)
    elif new_header not in text:
        raise RuntimeError("CascadeRunWorker class header neodpovídá očekávanému tvaru.")

    old_init = '''        api_key: str,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.cfg = copy.deepcopy(cfg)
'''
    new_init = '''        api_key: str,
    ) -> None:
        self.progress = EventPort()
        self.progress_event = EventPort()
        self.subprogress = EventPort()
        self.status = EventPort()
        self.logline = EventPort()
        self.finished_ok = EventPort()
        self.finished_err = EventPort()
        self.failure_detail = EventPort()
        self.cfg = copy.deepcopy(cfg)
'''
    if old_init in text:
        text = text.replace(old_init, new_init, 1)
    elif new_init not in text:
        raise RuntimeError("Cascade executor __init__ neodpovídá očekávanému tvaru.")

    old_run = "    def run(self):\n"
    new_run = "    def execute(self) -> None:\n"
    if old_run in text:
        if text.count(old_run) != 1:
            raise RuntimeError("Cascade core obsahuje více run metod.")
        text = text.replace(old_run, new_run, 1)
    elif new_run not in text:
        raise RuntimeError("Cascade executor nemá očekávanou execute metodu.")

    text = text.replace("NOVÁ VĚTEV –", "NOVÁ VĚTEV -")

    old_cleanup = '''            for temp_path in temp_paths:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
'''
    new_cleanup = '''            for temp_path in temp_paths:
                with contextlib.suppress(OSError):
                    os.remove(temp_path)
'''
    if old_cleanup in text:
        text = text.replace(old_cleanup, new_cleanup, 1)
    elif new_cleanup not in text:
        raise RuntimeError("Cascade core nemá očekávaný cleanup blok dočasných souborů.")

    if "PySide6" in text or "QThread" in text or "Signal(" in text or "QObject" in text:
        raise RuntimeError("Po extrakci zůstal v cascade core Qt symbol.")
    if "class CascadeRunWorker" in text:
        raise RuntimeError("Po extrakci zůstal v core CascadeRunWorker.")
    CORE.write_text(text, encoding="utf-8")


def write_worker() -> None:
    content = '''from __future__ import annotations

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
'''
    WORKER.write_text(content, encoding="utf-8")


def transform_imports() -> None:
    replace_once(
        STUDIO,
        "from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker\n",
        "from kajovo.core.cascade_pipeline import CascadeRunConfig\nfrom .workers.cascade_worker import CascadeRunWorker\n",
    )
    replace_once(
        HISTORY,
        "from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker\n",
        "from kajovo.core.cascade_pipeline import CascadeRunConfig\n",
    )
    history = HISTORY.read_text(encoding="utf-8")
    marker = "from kajovo.studio.workers.run_worker import RunWorker\n"
    insertion = "from kajovo.studio.workers.cascade_worker import CascadeRunWorker\n" + marker
    if insertion not in history:
        if marker not in history:
            raise RuntimeError("History launcher nemá očekávaný RunWorker import.")
        history = history.replace(marker, insertion, 1)
        HISTORY.write_text(history, encoding="utf-8")

    replace_once(
        DESKTOP,
        "from ..core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker\n",
        "from ..core.cascade_pipeline import CascadeRunConfig\nfrom ..studio.workers.cascade_worker import CascadeRunWorker\n",
    )


def transform_business_tests(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker",
        "from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor",
    )
    text = text.replace("CascadeRunWorker(", "CascadeRunExecutor(")
    text = text.replace(".run()", ".execute()")
    if "CascadeRunWorker" in text:
        raise RuntimeError(f"Business cascade test stále používá Qt worker: {path}")
    path.write_text(text, encoding="utf-8")


def transform_workflow_test() -> None:
    text = TEST_WORKFLOWS.read_text(encoding="utf-8")
    old = '''def test_custom_cascade_records_each_step(tmp_path):
    from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
    from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-4o-mini", input_text="test")])
    cfg = CascadeRunConfig("project", definition, "", str(tmp_path / "out"), run_id="RUN_090920261200_TEST")
    worker = CascadeRunWorker(cfg, settings, "test")
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.run()
'''
    new = '''def test_custom_cascade_records_each_step(tmp_path):
    from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunExecutor
    from kajovo.core.cascade_types import CascadeDefinition, CascadeStep
    settings = AppSettings(log_dir=str(tmp_path / "LOG"))
    definition = CascadeDefinition("test", steps=[CascadeStep(model="gpt-4o-mini", input_text="test")])
    cfg = CascadeRunConfig("project", definition, "", str(tmp_path / "out"), run_id="RUN_090920261200_TEST")
    worker = CascadeRunExecutor(cfg, settings, "test")
    client = Mock()
    client.create_response.return_value = response(1, "answer")
    results, errors = [], []
    worker.finished_ok.connect(results.append)
    worker.finished_err.connect(errors.append)
    with patch("kajovo.core.cascade_pipeline.OpenAIClient", return_value=client):
        worker.execute()
'''
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise RuntimeError("Workflow cascade test neodpovídá očekávanému tvaru.")
    TEST_WORKFLOWS.write_text(text, encoding="utf-8")


def add_architecture_contract() -> None:
    text = TEST_ARCH.read_text(encoding="utf-8")
    block = '''\n\ndef test_cascade_pipeline_is_qt_free():
    """Cascade orchestrace musí zůstat v core bez QThread/Signal závislosti."""
    path = ROOT / "kajovo" / "core" / "cascade_pipeline.py"
    violations = _violations([path], ("PySide6",))
    assert not violations, violations
    source = path.read_text(encoding="utf-8")
    assert "class CascadeRunExecutor" in source
    assert "class CascadeRunWorker" not in source
    assert "QThread" not in source
'''
    if "def test_cascade_pipeline_is_qt_free():" not in text:
        text = text.rstrip() + block + "\n"
        TEST_ARCH.write_text(text, encoding="utf-8")


def write_adapter_test() -> None:
    content = '''from PySide6.QtCore import QThread

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
'''
    TEST_ADAPTER.write_text(content, encoding="utf-8")


def verify_source_ownership() -> None:
    core = CORE.read_text(encoding="utf-8")
    studio = STUDIO.read_text(encoding="utf-8")
    history = HISTORY.read_text(encoding="utf-8")
    if "PySide6" in core or "CascadeRunWorker" in core:
        raise RuntimeError("Core stále vlastní Qt cascade worker.")
    if "from .workers.cascade_worker import CascadeRunWorker" not in studio:
        raise RuntimeError("Studio cascade panel nepoužívá nový Qt adaptér.")
    if "kajovo.studio.workers.cascade_worker import CascadeRunWorker" not in history:
        raise RuntimeError("History launcher nepoužívá nový Qt adaptér.")


def main() -> None:
    transform_core()
    write_worker()
    transform_imports()
    transform_business_tests(TEST_CASCADE)
    transform_business_tests(TEST_CASCADE_V2)
    transform_workflow_test()
    add_architecture_contract()
    write_adapter_test()
    verify_source_ownership()
    print("Cascade executor extraction prepared.")


if __name__ == "__main__":
    main()
