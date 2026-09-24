import threading
import time

import pytest
from PySide6.QtCore import QTimer
from change_v2_fixtures import run, scenario

from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.core.progress_display import build_steps, event_sentence, source_title
from kajovo.studio.components import install_theme
from kajovo.studio.operations import Task


def test_eta_requires_completed_samples_and_counts_down():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("A3", completed=0, total=6, timestamp=0))
    for i in range(1, 3):
        clock.update(ProgressEvent("A3", completed=i, total=6, timestamp=i * 10))
        assert clock.times(i * 10)[2] is None
    clock.update(ProgressEvent("A3", completed=3, total=6, timestamp=30))
    assert clock.times(30)[2] == 30
    assert clock.times(35)[2] == 25
    clock.update(ProgressEvent("Ukládání", completed=0, total=6, timestamp=40))
    assert clock.times(40)[2] is None


def test_progress_display_explains_remote_work_and_remaining_steps():
    events = [
        ProgressEvent("A1", "completed", detail="Architektonický plán dokončen.", source="api"),
        ProgressEvent("A2", detail="Ověřuji přijatou strukturu.", source="api"),
    ]
    steps = build_steps(events, mode="GENERATE", quality=True)
    states = {step.key: step.state for step in steps}
    assert states["A1"] == "done"
    assert states["A2"] == "current"
    assert any(step.key == "A2Q" and step.state == "pending" for step in steps)
    assert source_title("api") == "OpenAI Responses API"
    assert "OpenAI Responses API" in event_sentence(events[0])


def test_progress_event_metadata_is_optional_and_compatible():
    event = ProgressEvent("A3", source="disk", next_step="Kontrola výsledků")
    assert event.source == "disk"
    assert event.next_step == "Kontrola výsledků"


def test_repeated_poll_does_not_restart_unit_measurement():
    clock = ProgressClock(now=0)
    clock.update(ProgressEvent("Indexace", completed=0, total=5, timestamp=0))
    clock.update(ProgressEvent("Indexace", completed=0, total=5, timestamp=8))
    clock.update(ProgressEvent("Indexace", completed=1, total=5, timestamp=10))
    assert list(clock.samples) == [10]


def test_palette_text_and_selection_contrast(qapp):
    from PySide6.QtGui import QPalette

    install_theme(qapp)
    palette = qapp.palette()

    def luminance(color):
        values = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in values]
        return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    for foreground, background in (
        (QPalette.Text, QPalette.Base),
        (QPalette.HighlightedText, QPalette.Highlight),
    ):
        low, high = sorted((luminance(palette.color(foreground)), luminance(palette.color(background))))
        assert (high + 0.05) / (low + 0.05) >= 4.5


def test_io_runs_outside_gui_while_timer_remains_responsive(qtbot):
    main_thread = threading.get_ident()
    ticks, results, errors = [], [], []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start()
    task = Task(lambda _task: (time.sleep(0.05), threading.get_ident())[1])
    task.value.connect(results.append)
    task.start()
    qtbot.waitUntil(lambda: bool(results))
    task.wait()
    assert results[0] != main_thread and ticks
    failed = Task(lambda _task: (_ for _ in ()).throw(ValueError("fixture")))
    failed.failure.connect(errors.append)
    failed.start()
    qtbot.waitUntil(lambda: bool(errors))
    failed.wait()
    assert errors[0].message == "Operaci se nepodařilo dokončit a přesná příčina není doložena."
    timer.stop()


@pytest.mark.parametrize("fail", [False, True])
def test_file_is_counted_only_after_generation(tmp_path, monkeypatch, fail):
    worker, client, _ = scenario(tmp_path, "GENERATE")
    events = []
    worker.progress_event.connect(events.append)

    def generate(*args, **kwargs):
        assert [e.completed for e in events if e.stage == "A3" and e.completed is not None] == [0]
        if fail:
            raise ValueError("fixture")
        return "print('hello')\n", "resp_test"

    monkeypatch.setattr(worker, "_gen_file_chunks", generate)
    results, errors = run(worker, client)
    if fail:
        assert not results and errors and "fixture" in errors[0]
    else:
        assert results and not errors
    assert [e.completed for e in events if e.stage == "A3" and e.completed is not None] == (
        [0] if fail else [0, 1]
    )
