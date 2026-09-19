from __future__ import annotations

import difflib
import hashlib
import json
import os
import time
from pathlib import Path
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
        if not cfg.dry_run:
            safe_join_under_root(out_dir, row["path"])
        if not isinstance(row.get("content"), str):
            raise ContractError("Obsah výstupního souboru musí být text.")

    if cfg.mode == "MODIFY" and cfg.dry_run:
        staging_root = Path(context.log.paths.run_dir) / "staging" / "dry_run"
        generated_root = staging_root / "generated"
        ensure_dir(str(generated_root))
        staged: list[dict[str, Any]] = []
        diff_parts: list[str] = []
        for item in files:
            context.check_stop()
            rel = item["path"]
            content = item["content"]
            dst = safe_join_under_root(str(generated_root), rel)
            ensure_dir(os.path.dirname(dst))
            atomic_write_text(dst, content)
            digest = sha256_file(dst)
            expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if digest != expected:
                raise ContractError(f"Dry-run staging neodpovídá ověřenému obsahu: {rel}")
            source_text = ""
            source_path = safe_join_under_root(cfg.in_dir, rel) if cfg.in_dir else ""
            if source_path and os.path.isfile(source_path):
                try:
                    source_text = Path(source_path).read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise ContractError(f"Dry-run diff vyžaduje textový zdroj: {rel}") from exc
            diff_parts.extend(difflib.unified_diff(
                source_text.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            ))
            staged.append({
                "path": rel,
                "staged_path": str(Path(dst).relative_to(Path(context.log.paths.run_dir))),
                "bytes": os.path.getsize(dst),
                "sha256": digest,
                "action": item.get("action", "modify" if source_text else "add"),
            })

        diff_text = "".join(diff_parts)
        manifest = {
            "version": 1,
            "mode": "MODIFY",
            "dry_run": True,
            "publication": "blocked",
            "staging_root": str(staging_root.relative_to(Path(context.log.paths.run_dir))),
            "files": staged,
        }
        verification = {
            "version": 1,
            "status": "passed",
            "technical_validation": "passed",
            "content_acceptance": "not_claimed",
            "published": False,
            "checks": ["relative_paths", "utf8_content", "staged_sha256", "diff_generated"],
            "files": [{"path": row["path"], "sha256": row["sha256"]} for row in staged],
        }
        atomic_write_text(str(staging_root / "changes.diff"), diff_text)
        atomic_write_text(str(staging_root / "manifest.json"), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(str(staging_root / "verification.json"), json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
        context.log.save_json("manifests", "modify_dry_run", manifest)
        context.log.save_json("manifests", "modify_dry_run_diff", {"diff": diff_text})
        context.log.save_json("manifests", "modify_dry_run_verification", verification)
        context.log.update_state({
            "dry_run": True,
            "written_files": [],
            "staged_files": staged,
            "dry_run_staging": manifest["staging_root"],
            "verification_evidence": verification,
        })
        context.finish_delivery(files, saved=staged, dry_run=True)
        return {
            "saved": [], "staged": staged, "dry_run": True,
            "diff": diff_text, "verification": verification,
        }

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
