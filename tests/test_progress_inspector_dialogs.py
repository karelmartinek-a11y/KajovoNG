from kajovo.core.progress import ProgressEvent
from kajovo.progress_ui import PROCESS_PRESETS, STATE_META, ProcessInspector, infer_kind


def test_all_specified_progress_dialog_kinds_have_process_presets():
    assert set(PROCESS_PRESETS) == {
        "GENERATE",
        "MODIFY",
        "QA",
        "QFILE",
        "KASKÁDA",
        "BATCH",
        "FOTOGRAFIE",
        "COMIC",
        "ZDROJE",
        "OBNOVA",
        "LOKÁLNÍ",
        "SERVIS",
    }
    assert all(PROCESS_PRESETS.values())


def test_master_state_inventory_contains_uncertain_and_cancel_states():
    for state in (
        "created",
        "preparing",
        "running",
        "active",
        "waiting",
        "repairing",
        "response_pending",
        "batch_pending",
        "ready_to_import",
        "completed",
        "partial",
        "files_complete_unverified",
        "cancelling",
        "cancelled",
        "failed",
        "submission_unknown",
        "corrupt_state",
        "unknown",
        "expired",
        "blocked",
        "skipped",
    ):
        assert state in STATE_META


def test_kind_is_derived_from_real_domain_stages():
    assert infer_kind(events=[ProgressEvent("A1")]) == "GENERATE"
    assert infer_kind(events=[ProgressEvent("B2")]) == "MODIFY"
    assert infer_kind("Komiks · panelová dávka") == "COMIC"
    assert infer_kind("Nahrávání zdrojů") == "ZDROJE"


def test_measured_units_are_preserved_after_terminal_event(qtbot):
    inspector = ProcessInspector("Převod")
    qtbot.addWidget(inspector)
    inspector.on_event(ProgressEvent("Soubory", completed=3, total=8, unit="souborů"))
    inspector.on_event(ProgressEvent("RUN", "partial"))
    assert inspector.unit_progress.maximum() == 8
    assert inspector.unit_progress.value() == 3
    assert "3 z 8" in inspector.progress_note.text()


def test_batch_keeps_provider_and_local_state_separate(qtbot):
    inspector = ProcessInspector("BATCH", kind="BATCH")
    qtbot.addWidget(inspector)
    inspector.on_event(
        ProgressEvent(
            "BATCH",
            "waiting",
            source="batch_api",
            provider_state="in_progress",
            detail="Čekám na vzdálenou dávku",
        )
    )
    inspector.show()
    assert inspector.parallel.isVisible()
    assert "Vzdálené zpracování" in inspector.provider_label.text()
    assert inspector.stage_state_label.text() == "Čeká na odpověď služby"
