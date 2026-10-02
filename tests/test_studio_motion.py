"""Pulz zachovává doložený postup a běží jen ve viditelném aktivním rozhraní."""

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QWidget

from kajovo.core.progress import TERMINAL_RUN_STATES, ProgressEvent
from kajovo.studio.motion import PulseController
from kajovo.studio.progress_dialog import MultiProgressDialog


def test_pulse_visibility_and_unchanged_configuration_preserve_cycle(qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    pulse = PulseController(widget)
    pulse.set_running(True)
    assert pulse.animation.state() == QAbstractAnimation.Stopped
    widget.show()
    assert pulse.animation.state() == QAbstractAnimation.Running
    pulse.animation.setCurrentTime(700)
    pulse.set_running(True)
    assert pulse.animation.currentTime() == 700
    widget.hide()
    assert pulse.animation.state() == QAbstractAnimation.Stopped
    widget.show()
    assert pulse.animation.state() == QAbstractAnimation.Running
    pulse.set_running(True, reduced_motion=True)
    assert pulse.animation.state() == QAbstractAnimation.Stopped
    assert pulse.value == 1.0


def test_dialog_reduced_motion_is_respected_at_start_and_restart(qtbot):
    dialog = MultiProgressDialog("Příprava", reduced_motion=True)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.on_event(ProgressEvent("A1", "active"))
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped
    dialog.finish("failed")
    dialog.restart("Další pokus")
    dialog.on_event(ProgressEvent("A1", "active"))
    assert dialog.mark.reduced_motion
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped
    dialog.set_reduced_motion(False)
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Running
    dialog.set_reduced_motion(True)
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped


def test_hidden_dialog_suspends_refresh_without_resetting_evidence_or_clock(qtbot, monkeypatch):
    now = [10.0]
    monkeypatch.setattr("kajovo.core.progress.time.monotonic", lambda: now[0])
    dialog = MultiProgressDialog("Dlouhá práce")
    qtbot.addWidget(dialog)
    assert not dialog.timer.isActive()
    dialog.show()
    assert dialog.timer.isActive()
    dialog.on_event(ProgressEvent("A1", "waiting", timestamp=11.0))
    clock, rows = dialog.clock, dialog.inspector.model.rows()
    dialog.hide()
    assert not dialog.timer.isActive()
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped
    now[0] = 26.0
    dialog.show()
    assert dialog.timer.isActive()
    assert dialog.clock is clock
    assert clock.last_activity == 11.0
    assert dialog.inspector.model.rows() == rows
    assert "Doba práce: 16 s" in dialog.inspector.time_label.text()
    assert "Poslední zpráva před 15 s" in dialog.inspector.time_label.text()


@pytest.mark.parametrize("state,provider,mode", [
    ("active", "", "heartbeat"),
    ("waiting", "in_progress", "waiting"),
    ("active", "queued", "waiting"),
    ("validating_result", "", "heartbeat"),
])
def test_motion_mode_changes_without_advancing_work(qtbot, state, provider, mode):
    dialog = MultiProgressDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.on_event(ProgressEvent("PLAN", planned_steps=("A1", "A2", "A3")))
    dialog.on_event(ProgressEvent("A1", "completed"))
    dialog.on_event(ProgressEvent("A2", state, provider_state=provider))
    rows = dialog.inspector.model.rows()
    last_activity, event_count = dialog.clock.last_activity, len(dialog.events)
    assert dialog.mark.pulse.mode == mode
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Running
    for tick in range(12):
        dialog.mark.pulse.animation.setCurrentTime(tick * 50)
        dialog.tick()
    assert dialog.mark.done == 1
    assert dialog.mark.total == 3
    assert dialog.inspector.model.rows() == rows
    assert len(dialog.events) == event_count
    assert dialog.clock.last_activity == last_activity


@pytest.mark.parametrize("state", sorted(TERMINAL_RUN_STATES))
def test_terminal_event_stops_motion_before_worker_receipt(qtbot, state):
    dialog = MultiProgressDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.on_event(ProgressEvent("A1", "active"))
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Running
    dialog.on_event(ProgressEvent("RUN", state))
    assert dialog.active
    assert not dialog.mark.running
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped
    dialog.set_reduced_motion(False)
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped
    dialog.hide()
    dialog.show()
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped
    dialog.finish(state)
    assert not dialog.timer.isActive()
    assert dialog.mark.pulse.animation.state() == QAbstractAnimation.Stopped


def test_stop_request_uses_quiet_motion_without_claiming_provider_message(qtbot):
    dialog = MultiProgressDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.on_event(ProgressEvent("A1", "active"))
    last_activity, count = dialog.clock.last_activity, len(dialog.events)
    dialog.stop_callback = lambda: None
    dialog.request_stop()
    assert dialog.mark.pulse.mode == "waiting"
    assert dialog.clock.last_activity == last_activity
    assert len(dialog.events) == count
