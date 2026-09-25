"""Nová mapa smí zobrazit jen zprávy, které pracovní proces skutečně vydal."""

from kajovo.core.progress import ProgressEvent
from kajovo.multiprogress import MultiProgressView, step_states


def test_no_steps_are_invented_from_operation_title(qtbot):
    view = MultiProgressView("Tvorba projektu")
    qtbot.addWidget(view)
    assert view.steps.count() == 0
    assert view.ring.total == 0
    view.on_event(ProgressEvent("A1", "active"))
    assert view.steps.count() == 1
    assert view.ring.done == 0


def test_completion_requires_event_for_same_stage(qtbot):
    view = MultiProgressView()
    qtbot.addWidget(view)
    view.on_event(ProgressEvent("A1", "completed"))
    view.on_event(ProgressEvent("A2", "waiting", source="api", provider_state="in_progress"))
    assert view.ring.done == 1
    assert view.ring.total == 2
    assert "odpověď služby" in view.activity_label.text()
    assert "vzdálené služby" in view.provider_label.text()
    view.on_event(ProgressEvent("RUN", "failed"))
    assert step_states(view.events) == [("A1", "done"), ("A2", "error")]
    assert view.ring.done == 1


def test_unknown_submission_stays_unconfirmed(qtbot):
    view = MultiProgressView()
    qtbot.addWidget(view)
    view.on_event(ProgressEvent("A1", "waiting", source="api"))
    view.on_event(ProgressEvent("RUN", "submission_unknown"))
    assert step_states(view.events) == [("A1", "blocked")]
    assert view.ring.done == 0
    assert "znovu neposíláme" in view.activity_label.text()
    assert "Odeslání" in view.phase_label.text()


def test_provider_state_does_not_bleed_into_next_step(qtbot):
    view = MultiProgressView()
    qtbot.addWidget(view)
    view.on_event(ProgressEvent("A1", "waiting", provider_state="in_progress"))
    view.on_event(ProgressEvent("A2", "preparing"))
    assert view.provider_label.text() == ""
