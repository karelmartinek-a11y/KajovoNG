from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts import ContractError, validate_paths
from ..progress import ProgressEvent
from ..utils import atomic_write_text, ensure_dir, safe_join_under_root, sha256_file
from .config import UiRunConfig


class EmitSignal(Protocol):
    def __call__(self, value: Any) -> None: ...


@dataclass(frozen=True)
class DeliveryContext:
    """Porty potřebné pro bezpečný souborový delivery krok.

    Kontext neobsahuje žádný Qt typ. UI worker pouze předá callables/signály jako
    funkce. Tím zůstává přesná dnešní semantika zápisu testovatelná mimo Qt.
    """

    cfg: UiRunConfig
    settings: Any
    log: Any
    set_progress: Callable[..., None]
    create_snapshot: Callable[[str], str]
    check_stop: Callable[[], None]
    finish_delivery: Callable[..., None]
    progress_emit: EmitSignal
    subprogress_emit: EmitSignal
    overwrite_guard_enabled: bool
    overwrite_hashes: Mapping[str, str | None] | None = None


def save_out_files(context: DeliveryContext, files: list[dict[str, Any]]) -> dict[str, Any]:
    """Validuje a durable uloží textové výstupy se zachováním původních guardů."""
    cfg = context.cfg
    out_dir = cfg.out_dir
    validate_paths(files)
    for row in files:
        safe_join_under_root(out_dir, row["path"])
        if not isinstance(row.get("content"), str):
            raise ContractError("Obsah výstupního souboru musí být text.")

    if cfg.mode == "MODIFY" and context.settings.dry_run_modify:
        context.log.save_json("manifests", "modify_dry_run", {"files": files})
        context.log.update_state({"dry_run": True, "written_files": []})
        context.finish_delivery(files, dry_run=True)
        return {"saved": [], "dry_run": True}

    ensure_dir(out_dir)
    if cfg.versing and files:
        context.set_progress(80, 0, "Vytvářím snapshot před zápisem…", stage="VERSING")
        context.create_snapshot(out_dir)

    saved: list[dict[str, Any]] = []
    context.progress_emit(ProgressEvent("Ukládání", completed=0, total=len(files), unit="souborů"))
    for i, item in enumerate(files):
        context.check_stop()
        rel = item["path"]
        content = item["content"]
        dst = safe_join_under_root(out_dir, rel)
        ensure_dir(os.path.dirname(dst))

        if cfg.mode == "MODIFY" and context.overwrite_guard_enabled:
            current_hash = sha256_file(dst) if os.path.isfile(dst) else None
            expected_hash = (context.overwrite_hashes or {}).get(rel)
            if current_hash != expected_hash:
                raise ContractError(f"OUT se během generování změnil; soubor zachován: {rel}")

        before_size = os.path.getsize(dst) if os.path.exists(dst) else None
        before = sha256_file(dst) if os.path.exists(dst) else None
        if cfg.mode == "GENERATE" and before is not None:
            expected = (cfg.completed_hashes or {}).get(rel)
            incoming = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if before not in {expected, incoming}:
                raise ContractError(f"Existující soubor nepatří ověřenému výstupu; zachován: {rel}")

        atomic_write_text(dst, content)
        after_size = os.path.getsize(dst)
        after = sha256_file(dst)
        expected_after = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if after != expected_after:
            raise ContractError(f"Zápis neodpovídá ověřenému obsahu: {rel}")

        context.log.record_fs_change(
            "write",
            src=rel,
            dst=dst,
            before=before,
            after=after,
            before_size=before_size,
            after_size=after_size,
        )
        entry = {
            "path": rel,
            "dst": dst,
            "bytes": after_size,
            "sha256": after,
            "written_at": time.time(),
            "run_id": context.log.run_id,
            "purpose": item.get("purpose", ""),
        }
        saved.append(entry)
        # Multi-file zápis není transakce: evidence musí být durable po každém
        # jednotlivém atomickém zápisu ještě před zahájením dalšího souboru.
        context.log.save_json(
            "manifests",
            "out_write_journal",
            {"saved": saved, "out_dir": out_dir},
        )
        context.progress_emit(
            ProgressEvent(
                "Ukládání",
                completed=i + 1,
                total=len(files),
                unit="souborů",
                detail=rel,
            )
        )
        context.subprogress_emit(int((i + 1) * 100 / max(1, len(files))))

    context.log.save_json("manifests", "out_saved_map", {"saved": saved, "out_dir": out_dir})
    context.finish_delivery(files, saved=saved)
    return {"saved": saved}
