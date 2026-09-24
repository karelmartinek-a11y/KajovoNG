from __future__ import annotations

import difflib
import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..contracts import ContractError, validate_paths
from ..orchestration.verification import candidate_verification_report
from ..progress import ProgressEvent
from ..utils import atomic_write_text, ensure_dir, safe_join_under_root, sha256_file
from .config import UiRunConfig


class EmitSignal(Protocol):
    def __call__(self, value: Any) -> None: ...


@dataclass(frozen=True)
class DeliveryContext:
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
    expected_target_hashes: Mapping[str, str | None] | None = None
    additional_staged: Mapping[str, dict[str, Any]] | None = None
    originals: Mapping[str, str] | None = None


def _expected_target_hash(
    context: DeliveryContext,
    relative: str,
    incoming_hash: str,
) -> str | None:
    cfg = context.cfg
    out_dir = str(cfg.out_dir or "")
    if not out_dir:
        return None
    destination = safe_join_under_root(out_dir, relative)
    current_hash = sha256_file(destination) if os.path.isfile(destination) else None

    frozen = context.expected_target_hashes
    if frozen is not None:
        if relative not in frozen:
            raise ContractError(
                f"Chybí původní očekávaný stav cíle: {relative}"
            )
        expected = frozen[relative]
        if current_hash != expected:
            raise ContractError(
                f"OUT se během generování změnil; publikace je zablokována: {relative}"
            )
        return expected

    if cfg.mode == "MODIFY" and context.overwrite_guard_enabled:
        guards = context.overwrite_hashes or {}
        if relative not in guards:
            raise ContractError(
                f"Chybí původní hash MODIFY cíle: {relative}"
            )
        expected = guards[relative]
        if current_hash != expected:
            raise ContractError(
                f"OUT se během generování změnil; publikace je zablokována: {relative}"
            )
        return expected

    if cfg.mode == "GENERATE" and current_hash is not None:
        prior = (cfg.completed_hashes or {}).get(relative)
        if current_hash not in {prior, incoming_hash}:
            raise ContractError(
                f"Existující soubor nepatří ověřenému výstupu; zachován: {relative}"
            )
    return current_hash


def _write_staged(
    root: Path,
    relative: str,
    content: str,
) -> tuple[str, str, int]:
    destination = safe_join_under_root(str(root), relative)
    ensure_dir(os.path.dirname(destination))
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if os.path.isfile(destination):
        actual = sha256_file(destination)
        if actual != expected:
            raise ContractError(
                f"Immutable staging už obsahuje jiný artefakt: {relative}"
            )
    else:
        atomic_write_text(destination, content)
    actual = sha256_file(destination)
    if actual != expected:
        raise ContractError(f"Staging hash neodpovídá obsahu: {relative}")
    return destination, actual, os.path.getsize(destination)


def save_out_files(
    context: DeliveryContext,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stage immutable outputs first. Publishing to OUT is a separate action."""
    cfg = context.cfg
    validate_paths(files)
    for row in files:
        if not isinstance(row.get("content"), str):
            raise ContractError("Obsah výstupního souboru musí být text.")
        if cfg.out_dir:
            safe_join_under_root(cfg.out_dir, row["path"])

    run_root = Path(context.log.paths.run_dir).resolve()
    staging_root = run_root / "staging" / (
        "dry_run" if cfg.mode == "MODIFY" and cfg.dry_run else "candidate"
    )
    generated_root = staging_root / "generated"
    ensure_dir(str(generated_root))

    staged: list[dict[str, Any]] = []
    diff_parts: list[str] = []
    context.progress_emit(
        ProgressEvent(
            "Ukládání",
            completed=0,
            total=len(files),
            unit="souborů",
            detail="Ukládám do immutable stagingu; OUT zatím neměním.",
        )
    )

    for index, item in enumerate(files, 1):
        context.check_stop()
        relative = item["path"]
        content = item["content"]
        incoming_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        expected_target_hash = _expected_target_hash(
            context, relative, incoming_hash
        )
        destination, digest, size = _write_staged(
            generated_root, relative, content
        )

        source_text = ""
        if cfg.mode == "MODIFY" and cfg.in_dir:
            source_path = safe_join_under_root(cfg.in_dir, relative)
            if context.originals is not None:
                source_text = context.originals.get(relative, "")
            elif os.path.isfile(source_path):
                try:
                    source_text = Path(source_path).read_text(encoding="utf-8")
                except UnicodeDecodeError as exc:
                    raise ContractError(
                        f"MODIFY staging diff vyžaduje textový zdroj: {relative}"
                    ) from exc
            diff_parts.extend(
                difflib.unified_diff(
                    source_text.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )

        artifact = context.log.bundle.archive_artifact(
            destination,
            role="staged_output",
            kind="output_file",
            step_id=getattr(context, "_delivery_step_id", ""),
            reconstruction_role=relative,
            reusable=True,
            metadata={
                "relative_path": relative,
                "sha256": digest,
                "expected_target_hash": expected_target_hash,
                "publication": "not_published",
            },
        )
        staged.append(
            {
                "path": relative,
                "staged_path": Path(destination).relative_to(run_root).as_posix(),
                "bytes": size,
                "sha256": digest,
                "expected_target_hash": expected_target_hash,
                "action": item.get(
                    "action", "modify" if source_text else "add"
                ),
                "purpose": item.get("purpose", ""),
                "artifact_id": artifact.get("artifact_id"),
            }
        )
        context.progress_emit(
            ProgressEvent(
                "Ukládání",
                completed=index,
                total=len(files),
                unit="souborů",
                detail=relative,
            )
        )
        context.subprogress_emit(int(index * 100 / max(1, len(files))))

    additional = {
        str(path): dict(row)
        for path, row in (context.additional_staged or {}).items()
    }
    combined = dict(additional)
    for row in staged:
        path = str(row["path"])
        if path in combined and combined[path].get("sha256") != row.get("sha256"):
            raise ContractError(
                f"Staging obsahuje kolidující resource/text artefakt: {path}"
            )
        combined[path] = row
    combined_staged = [combined[path] for path in sorted(combined)]

    diff_text = "".join(diff_parts)
    manifest = {
        "version": 2,
        "run_id": context.log.run_id,
        "mode": cfg.mode,
        "dry_run": bool(cfg.mode == "MODIFY" and cfg.dry_run),
        "publication": (
            "blocked_dry_run"
            if cfg.mode == "MODIFY" and cfg.dry_run
            else "awaiting_verification_or_explicit_take"
        ),
        "staging_root": staging_root.relative_to(run_root).as_posix(),
        "files": combined_staged,
    }
    atomic_write_text(
        str(staging_root / "manifest.json"),
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    if cfg.mode == "MODIFY":
        atomic_write_text(str(staging_root / "changes.diff"), diff_text)

    verification = candidate_verification_report(
        run_root,
        combined_staged,
        mode=cfg.mode,
        target_id=f"{context.log.run_id}:{cfg.mode}",
        profile_ids=list(cfg.verification_profile_ids or []),
    )
    atomic_write_text(
        str(staging_root / "verification.json"),
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
    )
    context.log.save_json("manifests", "staged_outputs_v2", manifest)
    context.log.save_json(
        "manifests", "verification_report_v3", verification
    )
    if diff_text:
        context.log.save_json(
            "manifests", "staged_output_diff", {"diff": diff_text}
        )

    dry_run = bool(cfg.mode == "MODIFY" and cfg.dry_run)
    context.log.update_state(
        {
            "dry_run": dry_run,
            "written_files": [],
            "staged_files": combined_staged,
            "staging_root": manifest["staging_root"],
            "verification_evidence": verification,
            "publication_state": manifest["publication"],
            "published_files": [],
        }
    )
    context.finish_delivery(
        files,
        saved=[],
        staged=combined_staged,
        dry_run=dry_run,
    )
    return {
        "saved": [],
        "staged": combined_staged,
        "published": False,
        "dry_run": dry_run,
        "diff": diff_text,
        "verification": verification,
        "publication_state": manifest["publication"],
    }
