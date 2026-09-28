"""Kanonické vlastnictví kruhového průběhu pro všech 182 referenčních variant."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressCoverageFamily:
    key: str
    first: int
    last: int
    strategy: str
    owners: tuple[str, ...]
    required_markers: tuple[str, ...]

    def contains(self, variant_id: int) -> bool:
        return self.first <= variant_id <= self.last


REFERENCE_PROGRESS_FAMILIES = (
    ProgressCoverageFamily(
        "generate_modify",
        1,
        39,
        "dedicated_backend_events",
        (
            "kajovo/core/runs/executor.py",
            "kajovo/core/runs/progress_plan.py",
            "kajovo/core/orchestration/preparation.py",
            "kajovo/core/runs/generate.py",
            "kajovo/core/runs/modify.py",
            "kajovo/core/orchestration/resource_delivery.py",
            "kajovo/core/runs/delivery.py",
        ),
        (
            "RUN_START",
            "RUN_CONFIG",
            "TARGET_SCOPE",
            "TARGET_READY",
            "RESOURCE_TARGET",
            "OUTPUT_VALIDATE",
            "RUN_FINALIZE",
        ),
    ),
    ProgressCoverageFamily(
        "qa_qfile",
        40,
        49,
        "dedicated_backend_events",
        (
            "kajovo/core/runs/qa.py",
            "kajovo/core/runs/qfile.py",
        ),
        ("QA_INPUT", "QA_RESPONSE", "QA_VALIDATION", "QFILE"),
    ),
    ProgressCoverageFamily(
        "history_branching",
        50,
        64,
        "managed_operation_with_backend_events",
        (
            "kajovo/studio/history_launcher.py",
            "kajovo/studio/operations.py",
            "kajovo/core/runs/recovery.py",
        ),
        ("Převzetí výsledku", "progress_event"),
    ),
    ProgressCoverageFamily(
        "cascade",
        65,
        83,
        "dedicated_backend_events",
        ("kajovo/core/cascade_pipeline.py",),
        (
            "CASCADE_CHECK",
            "CASCADE_INPUTS",
            "CASCADE_RESTORE",
            "CASCADE_PUBLISH",
            "Uložení bodu pokračování",
        ),
    ),
    ProgressCoverageFamily(
        "batch",
        84,
        95,
        "dedicated_backend_events",
        (
            "kajovo/core/runs/batch_execution.py",
            "kajovo/core/batch_completion.py",
            "kajovo/core/generate_batch.py",
        ),
        (
            "BATCH_PREPARE",
            "BATCH_VALIDATE",
            "BATCH_UPLOAD",
            "BATCH_SUBMIT",
            "Kontrola stavu dávky",
            "Parsování odpovědí",
            "Validace kontraktů",
        ),
    ),
    ProgressCoverageFamily(
        "photo",
        96,
        105,
        "dedicated_backend_events",
        (
            "kajovo/core/photo_batch.py",
            "kajovo/studio/photos.py",
        ),
        (
            "PHOTO_VALIDATE",
            "PHOTO_UPLOAD_SOURCES",
            "PHOTO_SUBMIT",
            "PHOTO_RECOVERY",
            "PHOTO_DOWNLOAD",
            "PHOTO_VALIDATE_RESULTS",
            "PHOTO_WRITE",
        ),
    ),
    ProgressCoverageFamily(
        "comic",
        106,
        122,
        "dedicated_backend_events",
        (
            "kajovo/core/comic_service.py",
            "kajovo/studio/comics.py",
        ),
        (
            "COMIC_CHECK",
            "COMIC_PREPARING",
            "COMIC_SUBMITTING",
            "COMIC_RETRIEVING",
            "COMIC_SAVE",
        ),
    ),
    ProgressCoverageFamily(
        "resources",
        123,
        138,
        "managed_operation_with_item_events",
        (
            "kajovo/studio/resources.py",
            "kajovo/studio/operations.py",
        ),
        ("operations.start", "Nahrávání souborů"),
    ),
    ProgressCoverageFamily(
        "versions",
        139,
        151,
        "managed_operation",
        (
            "kajovo/studio/versions.py",
            "kajovo/studio/operations.py",
        ),
        ("operations.start", "Příprava operace"),
    ),
    ProgressCoverageFamily(
        "converter",
        152,
        155,
        "dedicated_adapter_events",
        (
            "kajovo/studio/converter.py",
            "kajovo/studio/operations.py",
        ),
        ("progress_event.emit", "Převod"),
    ),
    ProgressCoverageFamily(
        "history_reads_and_actions",
        156,
        177,
        "managed_operation_or_read",
        (
            "kajovo/studio/history.py",
            "kajovo/studio/history_artifacts.py",
            "kajovo/studio/history_details.py",
            "kajovo/studio/history_composer.py",
            "kajovo/studio/operations.py",
        ),
        ("start_read", "Příprava čtení"),
    ),
    ProgressCoverageFamily(
        "model_catalog",
        178,
        178,
        "managed_operation",
        (
            "kajovo/studio/context.py",
            "kajovo/studio/operations.py",
        ),
        ("Načtení katalogu modelů", "Příprava operace"),
    ),
    ProgressCoverageFamily(
        "smtp_test",
        179,
        179,
        "managed_operation",
        (
            "kajovo/studio/settings.py",
            "kajovo/studio/operations.py",
        ),
        ("Odeslání zkušební zprávy", "Příprava operace"),
    ),
    ProgressCoverageFamily(
        "result_notification",
        180,
        180,
        "managed_operation",
        (
            "kajovo/studio/workbench.py",
            "kajovo/studio/operations.py",
        ),
        ("Odeslání oznámení o výsledku", "Příprava operace"),
    ),
    ProgressCoverageFamily(
        "windows_repair",
        181,
        181,
        "managed_operation",
        (
            "kajovo/studio/history.py",
            "kajovo/studio/operations.py",
        ),
        ("operations.start", "Příprava operace"),
    ),
    ProgressCoverageFamily(
        "remote_repair",
        182,
        182,
        "managed_operation",
        (
            "kajovo/studio/history_launcher.py",
            "kajovo/studio/operations.py",
        ),
        ("operations.start", "Příprava operace"),
    ),
)


def coverage_for_variant(variant_id: int) -> ProgressCoverageFamily:
    matches = [
        family
        for family in REFERENCE_PROGRESS_FAMILIES
        if family.contains(variant_id)
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Referenční varianta {variant_id} nemá právě jednoho vlastníka průběhu."
        )
    return matches[0]


def covered_variant_ids() -> tuple[int, ...]:
    return tuple(
        variant_id
        for family in REFERENCE_PROGRESS_FAMILIES
        for variant_id in range(family.first, family.last + 1)
    )
