"""Úplnost kruhového průběhu přes referenční varianty a společný správce operací."""

import json
import threading
from pathlib import Path

from PySide6.QtWidgets import QWidget

from kajovo.core.progress_catalog import coverage_for_variant, covered_variant_ids
from kajovo.core.runs.progress_plan import run_progress_plan
from kajovo.studio.operations import Operations


def test_reference_progress_catalog_covers_all_182_variants_and_1284_steps():
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads(
        (root / "docs" / "progress" / "reference_inventory_182.json").read_text(
            encoding="utf-8"
        )
    )
    assert list(covered_variant_ids()) == list(range(1, 183))
    assert inventory["variant_count"] == 182
    assert inventory["step_count"] == 1284
    assert sum(len(row["steps"]) for row in inventory["variants"]) == 1284
    for variant in inventory["variants"]:
        family = coverage_for_variant(variant["id"])
        assert family.owners
        assert family.strategy


def test_managed_operation_reports_prepare_execute_and_receive(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    manager = Operations(host)
    received = []

    record = manager.start(
        "Provedení testovací operace",
        lambda task: {"status": "completed", "value": 7},
        lambda value: received.append(value["value"]),
        popup=False,
    )
    qtbot.waitUntil(lambda: bool(record.terminal))

    plan = next(event for event in record.events if event.stage == "PLAN")
    assert plan.planned_steps == (
        "Příprava operace",
        "Provedení testovací operace",
        "Převzetí výsledku",
    )
    states = {(event.stage, event.state) for event in record.events}
    assert ("Příprava operace", "completed") in states
    assert ("Provedení testovací operace", "completed") in states
    assert ("Převzetí výsledku", "completed") in states
    assert received == [7]


def test_short_read_keeps_real_progress_evidence_without_popup(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    manager = Operations(host)
    release = threading.Event()

    def read(task):
        release.wait(3)
        return {"value": "hotovo"}

    record = manager.start_read(
        "Porovnání verzí souboru",
        read,
        lambda _value: None,
    )
    qtbot.waitUntil(lambda: len(record.events) >= 3)
    assert record.dialog is None
    assert record.events[0].stage == "PLAN"
    assert record.events[0].planned_steps == (
        "Příprava čtení",
        "Porovnání verzí souboru",
        "Převzetí výsledku",
    )
    release.set()
    qtbot.waitUntil(lambda: not manager.active)
    states = {(event.stage, event.state) for event in record.events}
    assert ("Příprava čtení", "completed") in states
    assert ("Porovnání verzí souboru", "completed") in states
    assert ("Převzetí výsledku", "completed") in states


class _Cfg:
    mode = "GENERATE"
    stop_after_plan = False
    send_as_c = False
    maximum_quality = True
    qfile_output_path = ""


def test_generate_progress_plan_contains_internal_runtime_boundaries():
    cfg = _Cfg()
    live = run_progress_plan(cfg)
    for stage in (
        "UI_VALIDATE",
        "RUN_START",
        "RUN_CHECK",
        "RUN_CONFIG",
        "RUN_INPUT",
        "A0R",
        "A1",
        "A2_SPINE",
        "A2_DETAIL",
        "A2",
        "A2Q",
        "TARGET_SCOPE",
        "TARGET_READY",
        "A3",
        "RESOURCE_TARGET",
        "OUTPUT_VALIDATE",
        "Ukládání",
        "RUN_FINALIZE",
    ):
        assert stage in live

    cfg.send_as_c = True
    batch = run_progress_plan(cfg)
    for stage in (
        "BATCH_PREPARE",
        "BATCH_VALIDATE",
        "BATCH_UPLOAD",
        "BATCH_SUBMIT",
        "BATCH_STATE",
    ):
        assert stage in batch
    assert "A3" not in batch
