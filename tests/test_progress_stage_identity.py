"""Průběh vychází z identity skutečného kroku, nikoli z odhadu UI."""
import pytest

from change_v2_fixtures import run, scenario
from kajovo.core.progress import ProgressEvent
from kajovo.core.progress_display import stage_title
from kajovo.progress_ui import ProcessInspector


@pytest.mark.parametrize("mode,prefix", [("GENERATE", "A"), ("MODIFY", "B")])
@pytest.mark.parametrize("batch", [False, True])
def test_preparation_announces_real_steps_before_transport(tmp_path, mode, prefix, batch):
    worker, client, _ = scenario(tmp_path, mode, batch=batch)
    events = []
    worker.progress_event.connect(events.append)
    result, errors = run(worker, client)
    assert result and not errors
    stages = [prefix + name for name in ("0R", "1", "2_SPINE", "2_DETAIL")]
    for stage in stages:
        relevant = [event for event in events if event.stage == stage]
        assert relevant[0].state == "preparing"
        assert any(event.state == "waiting" and event.source == "api" for event in relevant)
        assert relevant[-1].state == "completed"
    assert not any(event.source == "api" and event.stage == "Lokální validace" for event in events)


def test_inspector_orders_actual_preparation_and_does_not_invent_micro_completion(qtbot):
    inspector = ProcessInspector(kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.set_context(maximum_quality=True)
    inspector.on_event(ProgressEvent("A1", "completed"))
    waiting = ProgressEvent("A2_SPINE", "waiting", source="api", provider_state="in_progress")
    inspector.on_event(waiting)
    steps = inspector._process_steps()
    titles = [title for title, _ in steps]
    assert titles.index(stage_title("A1")) < titles.index(stage_title("A2_SPINE"))
    assert titles.index(stage_title("A2_SPINE")) < titles.index(stage_title("A2_DETAIL"))
    assert stage_title("A2Q") in titles
    assert inspector.phase_label.text() == stage_title("A2_SPINE")
    assert inspector.next_label.text() == stage_title("A2_DETAIL")
    assert stage_title("A1") in inspector.last_label.text()
    assert not any(state == "done" for _, state in inspector._micro_steps(waiting))
    assert "Vzdálené zpracování" in inspector.provider_label.text()


def test_latest_log_is_first_and_phase_labels_wrap(qtbot):
    inspector = ProcessInspector(kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.append_log("STARŠÍ")
    inspector.append_log("NOVĚJŠÍ")
    assert "NOVĚJŠÍ" in inspector.log.toPlainText().splitlines()[0]
    assert inspector.log.textCursor().position() == 0
    assert inspector.flow.layout().rowCount() > 1
    for index in range(inspector.flow.layout().count()):
        assert inspector.flow.layout().itemAt(index).widget().wordWrap()


def test_provider_state_does_not_leak_into_different_stage(qtbot):
    inspector = ProcessInspector(kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.on_event(ProgressEvent("A1", "waiting", source="api", provider_state="in_progress"))
    inspector.on_event(ProgressEvent("A2_SPINE", "preparing"))
    assert inspector.provider_label.text() == "—"


def test_completed_step_is_not_completed_run_and_next_does_not_rewind(qtbot):
    inspector = ProcessInspector(kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.on_event(ProgressEvent("A1", "completed", source="validation"))
    assert inspector.run_progress.maximum() == 0
    assert inspector.next_label.text() == stage_title("A2_SPINE")


def test_short_viewport_scrolls_instead_of_compressing_phase_details(qtbot):
    inspector = ProcessInspector(kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.set_context(maximum_quality=True)
    inspector.resize(760, 400)
    inspector.on_event(ProgressEvent("A2_SPINE", "waiting", source="api"))
    inspector.show()
    qtbot.waitUntil(lambda: inspector.scroll.verticalScrollBar().maximum() > 0)
    assert inspector.source_label.height() >= inspector.source_label.fontMetrics().height()
