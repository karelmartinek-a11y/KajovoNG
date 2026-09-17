from kajovo.core.progress import ProgressEvent
from kajovo.progress_ui import ProcessInspector


def _state_for(inspector, title):
    return dict(inspector._process_steps())[title]


def test_terminal_failure_marks_last_active_macro_step_as_error(qtbot):
    inspector = ProcessInspector("Práce na projektu", kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.on_event(ProgressEvent("A1", "active", source="api"))
    inspector.on_event(ProgressEvent("RUN", "failed"))

    assert _state_for(inspector, "Architektonický plán") == "error"


def test_unknown_submission_blocks_last_active_macro_step(qtbot):
    inspector = ProcessInspector("Práce na projektu", kind="GENERATE")
    qtbot.addWidget(inspector)
    inspector.on_event(ProgressEvent("A1", "waiting", source="api"))
    inspector.on_event(ProgressEvent("RUN", "submission_unknown"))

    assert _state_for(inspector, "Architektonický plán") == "blocked"
