from __future__ import annotations

import logging
import os
import shutil
import time
from typing import TYPE_CHECKING, Any

from ..utils import atomic_write_text, is_versing_snapshot_dir
from .contracts import RunStatus
from .delivery import DeliveryContext, save_out_files

if TYPE_CHECKING:
    from .context import RunContext

def _create_snapshot(self: RunContext, root: str) -> str:
    root = os.path.abspath(root)
    root_name = os.path.basename(root)
    snap_name = f"{root_name}{time.strftime('%d%m%Y%H%M%S')}"
    snap_dir = os.path.join(root, snap_name)
    deny = {"venv", ".venv", "LOG", snap_name}

    def ignore(dirpath, names):
        ignored = set()
        for n in names:
            if n in deny or is_versing_snapshot_dir(n, root_name):
                ignored.add(n)
        return ignored

    shutil.copytree(root, snap_dir, ignore=ignore, symlinks=True)
    try:
        self.log.event("versing.snapshot.created", {"snap_dir": snap_dir})
    except Exception as evidence_error:
        logging.getLogger(__name__).warning(
            "Zápis pomocné evidence selhal: %s", evidence_error
        )
    return snap_dir


def _save_out_files(self: RunContext, files: list[dict[str, Any]]) -> dict[str, Any]:
    if self.lifecycle_status is not RunStatus.CREATED:
        self.transition(RunStatus.DELIVERING)
    # Tenký Qt adaptér. Vlastní validace, hash guardy a durable zápis jsou
    # v Qt-nezávislé doménové vrstvě core.runs.delivery.
    context = DeliveryContext(
        cfg=self.cfg,
        settings=self.settings,
        log=self.log,
        set_progress=self._set,
        create_snapshot=self._create_snapshot,
        check_stop=self._check_stop,
        finish_delivery=self._finish_file_delivery,
        progress_emit=self.progress_event.emit,
        subprogress_emit=self.subprogress.emit,
        overwrite_guard_enabled=hasattr(self, "_delivery_overwrite_hashes"),
        overwrite_hashes=getattr(self, "_delivery_overwrite_hashes", None),
        expected_target_hashes=getattr(
            self, "_delivery_expected_target_hashes", None
        ),
    )
    self._progress_stage = "Ukládání"
    return save_out_files(context, files)


def _finish_file_delivery(
    self: RunContext,
    files,
    *,
    saved=(),
    staged=(),
    dry_run=False,
):
    step_id = getattr(self, "_delivery_step_id", "")
    if not step_id:
        return
    record = self.log.record_validation(
        step_id=step_id,
        target_type="delivery",
        target_id=self.log.run_id,
        validator="staging_and_publication_boundary",
        status="passed",
        evidence={
            "expected": [f["path"] for f in files],
            "written_to_out": list(saved),
            "staged": list(staged),
            "dry_run": dry_run,
            "functionality_verified": False,
            "publication_requires_explicit_boundary": not dry_run,
        },
    )
    self.log.bundle.update_step(
        step_id,
        status="dry_run" if dry_run else "completed",
        finished_at=record["timestamp"],
        progress=100,
    )


def _write_missing_files_report(
    self: RunContext,
    skipped_files: list[dict[str, Any]],
) -> str | None:
    if not skipped_files:
        return None
    # Diagnostický report je evidence běhu, nikoli výjimka z publish boundary.
    # OUT se před verification/explicit take nikdy nemění.
    report_path = os.path.join(self.log.paths.misc_dir, "MISSINGFILES.md")
    lines: list[str] = [
        "# MISSINGFILES",
        "",
        "Tyto soubory byly součástí výstupní struktury, ale Kájovo NG je v A3 automaticky nedodává.",
        "",
    ]
    for item in skipped_files:
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        purpose = str(item.get("purpose") or "").strip() or "N/A"
        reason = str(
            item.get("reason") or "typ výstupu není automaticky generován"
        ).strip()
        lines.append(f"- path: `{path}`")
        lines.append(f"  - expected_content: {purpose}")
        lines.append(f"  - důvod: {reason}")
    atomic_write_text(report_path, "\n".join(lines).rstrip() + "\n")
    self.log.bundle.archive_artifact(
        report_path,
        role="diagnostic",
        kind="missing_files_report",
        reconstruction_role="MISSINGFILES.md",
        metadata={
            "publication": "not_published",
            "missing_count": len(skipped_files),
        },
    )
    self._log_debug(
        f"A3: archived missing files report -> {report_path} "
        f"({len(skipped_files)} entries)"
    )
    return report_path
