"""Regrese významu kruhové mapy na skutečných událostech a životnosti workeru."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from kajovo.core.progress import ProgressClock, ProgressEvent
from kajovo.core.progress_model import ProgressModel
from kajovo.core.runs.progress_plan import run_progress_plan
from kajovo.studio.operations import Operations
from kajovo.studio.progress_dialog import MultiProgressDialog
from kajovo.studio.progress_view import MultiProgressView


def test_plan_is_not_evidence_of_work():
    model = ProgressModel()
    model.update(ProgressEvent("PLAN", planned_steps=("A1", "A2")))
    model.update(ProgressEvent("A1", "completed"))
    model.update(ProgressEvent("RUN", "completed"))
    assert model.rows() == [("A1", "done"), ("A2", "not_run")]


def test_polling_log_limit_does_not_erase_confirmed_steps(qtbot):
    view = MultiProgressView()
    qtbot.addWidget(view)
    view.on_event(ProgressEvent("A1", "completed"))
    for i in range(2010):
        view.on_event(ProgressEvent("A2", "waiting", timestamp=i))
    assert len(view.events) == 2000
    assert view.ring.done == 1
    assert view.ring.total == 2


@pytest.mark.parametrize("state", ["failed", "submission_unknown", "response_pending", "cancelled"])
def test_worker_error_signal_does_not_reclassify_backend_terminal(qtbot, state):
    class Worker(QThread):
        progress_event = Signal(object)
        finished_err = Signal(str)
        finished_ok = Signal(object)

        def run(self):
            self.progress_event.emit(ProgressEvent("QA_RESPONSE", "waiting"))
            self.progress_event.emit(ProgressEvent("RUN", state))
            self.finished_err.emit("Doložená příčina")

    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    receive = Mock()
    record = manager.adopt("Práce", Worker(), receive, popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == state
    assert record.dialog.inspector.model.terminal == state
    receive.assert_not_called()


@pytest.mark.parametrize("announces_completion", [False, True])
def test_thread_without_result_cannot_claim_success(qtbot, announces_completion):
    class Worker(QThread):
        progress_event = Signal(object)
        finished_err = Signal(str)
        finished_ok = Signal(object)

        def run(self):
            if announces_completion:
                self.progress_event.emit(ProgressEvent("RUN", "completed"))

    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    receive = Mock()
    record = manager.adopt("Práce", Worker(), receive, popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == "unknown"
    receive.assert_not_called()


def test_restart_removes_all_old_evidence(qtbot):
    dialog = MultiProgressDialog("První pokus")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("A3", completed=8, total=10, provider_state="in_progress"))
    dialog.finish("failed")
    dialog.restart("Druhý pokus")
    assert not dialog.inspector.provider_label.text()
    assert dialog.progress.isHidden()
    assert dialog.inspector.model.measurement is None
    assert dialog.inspector.steps.count() == 0
    assert dialog.inspector.ring.done == 0
    assert dialog.inspector.ring.total == 0
    assert dialog.inspector.title_label.text() == "Druhý pokus"


def test_terminal_event_does_not_claim_worker_has_finished(qtbot):
    dialog = MultiProgressDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("RUN", "completed"))
    assert "Čekáme na ukončení pracovníka" in dialog.summary.text()
    assert dialog.active
    dialog.finish("completed")
    assert "Pracovní proces skončil" in dialog.summary.text()
    assert "Poslední zpráva před" not in dialog.inspector.time_label.text()


def test_stop_is_idempotent_and_not_provider_activity(qtbot):
    dialog = MultiProgressDialog("Práce")
    qtbot.addWidget(dialog)
    dialog.on_event(ProgressEvent("A1", "waiting"))
    last = dialog.clock.last_activity
    callback = Mock()
    dialog.stop_callback = callback
    dialog.request_stop()
    dialog.tick()
    dialog.request_stop()
    callback.assert_called_once()
    assert dialog.clock.last_activity == last
    dialog.on_event(ProgressEvent("A1", "waiting", provider_state="in_progress"))
    assert "potvrzení zastavení" in dialog.inspector.state_label.text()


@pytest.mark.parametrize("mode", ["GENERATE", "MODIFY"])
@pytest.mark.parametrize("stop,batch,quality", [(True, False, False), (False, True, True), (False, False, False)])
def test_backend_declares_only_selected_main_path(mode, stop, batch, quality):
    cfg = SimpleNamespace(mode=mode, stop_after_plan=stop, send_as_c=batch, maximum_quality=quality)
    plan = run_progress_plan(cfg)
    prefix = "A" if mode == "GENERATE" else "B"
    assert (prefix + "2Q" in plan) == quality
    assert (prefix + "3" in plan) == (not stop and not batch)
    assert ("BATCH_SUBMIT" in plan) == (not stop and batch)


def test_unknown_provider_state_never_claims_processing(qtbot):
    view = MultiProgressView()
    qtbot.addWidget(view)
    view.on_event(ProgressEvent("A1", "waiting", provider_state="new_failure_code"))
    assert "neznámý stav" in view.provider_label.text()
    assert "new_failure_code" in view.log.toPlainText()


def test_transport_alias_is_one_functional_step():
    model = ProgressModel()
    model.update(ProgressEvent("PLAN", planned_steps=("A2Q", "RUN_FINALIZE")))
    model.update(ProgressEvent("A2Q_QUALITY_GATE", "waiting"))
    model.update(ProgressEvent("A2Q", "completed"))
    assert model.rows() == [("A2Q", "done"), ("RUN_FINALIZE", "pending")]


def test_discovered_step_precedes_remaining_plan():
    model = ProgressModel()
    model.update(ProgressEvent("PLAN", planned_steps=("A1", "RUN_FINALIZE")))
    model.update(ProgressEvent("A1", "completed"))
    model.update(ProgressEvent("Dodatečná kontrola", "active"))
    assert [key for key, _ in model.rows()] == ["A1", "Dodatečná kontrola", "RUN_FINALIZE"]


@pytest.mark.parametrize("status,batch_id,expected", [
    ("completed", "batch_fixture", "ready_to_import"),
    ("downloaded", "batch_fixture", "completed"),
    ("submission_unknown", "", "submission_unknown"),
    ("expired", "batch_fixture", "expired"),
])
def test_photo_result_preserves_remote_local_boundary(qtbot, status, batch_id, expected):
    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    record = manager.start("Fotografie", lambda task: SimpleNamespace(status=status, batch_id=batch_id), popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == expected
    assert record.dialog.inspector.model.terminal == expected


def test_window_title_is_not_replaced_by_action_label(qtbot):
    dialog = MultiProgressDialog("Tvorba projektu")
    qtbot.addWidget(dialog)
    assert dialog.inspector.title_label.text() == "Tvorba projektu"


@pytest.mark.parametrize("completed,total,unit", [(0, 6, "souborů"), (3, 12, "souborů"), (3, 6, "částí")])
def test_eta_does_not_mix_different_work_scopes(completed, total, unit):
    clock = ProgressClock(now=0)
    for index in range(4):
        clock.update(ProgressEvent("A3", completed=index, total=6, unit="souborů", timestamp=index * 10))
    assert clock.times(30)[2] == 30
    clock.update(ProgressEvent("A3", completed=completed, total=total, unit=unit, timestamp=40))
    assert clock.times(40)[2] is None


def test_narrow_window_prioritizes_current_state(qtbot):
    dialog = MultiProgressDialog("Tvorba projektu s dlouhým názvem")
    qtbot.addWidget(dialog)
    dialog.resize(480, 640)
    dialog.on_event(ProgressEvent("A1", "waiting"))
    dialog.show()
    qtbot.waitUntil(lambda: dialog.inspector._narrow)
    view = dialog.inspector
    assert view.columns.itemAt(0).widget() is view.card
    viewport = view.scroll.viewport()
    bottom = view.state_label.mapTo(viewport, view.state_label.rect().bottomRight()).y()
    assert 0 < bottom < viewport.height()


def test_narrow_completed_actions_have_no_hidden_gaps(qtbot):
    dialog = MultiProgressDialog("Výsledek")
    qtbot.addWidget(dialog)
    dialog.resize(480, 640)
    dialog.result = {"status": "completed"}
    dialog.finish("completed")
    assert dialog.actions.getItemPosition(dialog.actions.indexOf(dialog.close_button))[:2] == (0, 0)
    assert dialog.actions.getItemPosition(dialog.actions.indexOf(dialog.result_button))[:2] == (0, 1)


@pytest.mark.parametrize("state", ["failed", "partial", "submission_unknown"])
def test_returned_failure_is_not_a_completed_step(qtbot, state):
    parent = QWidget()
    qtbot.addWidget(parent)
    manager = Operations(parent)
    record = manager.start("Kontrola", lambda task: {"status": state}, popup=False)
    qtbot.waitUntil(lambda: bool(record.terminal))
    assert record.terminal == state
    assert dict(record.dialog.inspector.model.rows())["OPERATION"] != "done"
