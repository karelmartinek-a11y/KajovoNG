from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
    validate_paths,
)
from ..delivery_preparation import (
    prepare_delivery,
    validate_modify_sources,
)
from ..generate_batch import build_manifest as build_batch_manifest
from ..openai_client import OpenAIClient
from ..progress import ProgressEvent
from ..utils import safe_join_under_root, sha256_file

if TYPE_CHECKING:
    from .context import RunContext

from .attachments import prepare_modify_inputs


def _run_v3_modify_production(
    self: RunContext,
    client: OpenAIClient,
    diag_file_ids: list[str],
    plan: dict[str, Any],
    struct: dict[str, Any],
    resp2_id: str | None,
    root: str,
    up_items: list[Any],
    tools,
    supports_fs: bool,
    vs_id: str | None,
) -> dict[str, Any]:
    from ..orchestration.preparation import _inventory
    from ..orchestration.resource_delivery import (
        dispatch_resource_target,
        prepare_production_scope,
    )
    selected, completed, excluded = prepare_production_scope(self, struct)
    files_by_path = {
        str(row["path"]): row for row in struct["spine"]["files"]
    }
    source_items = {item.rel_path: item for item in up_items}
    _, archived_originals = _inventory(self)
    archived_by_path = {row["path"]: row for row in archived_originals}
    originals: dict[str, str] = {}
    for path, target in selected.items():
        if target.get("kind") != "text":
            continue
        if target.get("action") == "modify" and path not in source_items:
            raise ContractError(
                f"B3: měněný soubor není dostupný ve schváleném IN: {path}"
            )
        if target.get("action") == "add" and os.path.lexists(
            safe_join_under_root(root, path)
        ):
            raise ContractError(f"B3: přidávaný soubor již existuje v IN: {path}")
        for source in {path, *target.get("dependencies", [])}:
            item = source_items.get(source)
            if item is None:
                continue
            source_path = safe_join_under_root(root, source)
            if sha256_file(source_path) != item.sha256:
                raise ContractError(f"IN se od skenu změnil: {source}")
            archived = archived_by_path.get(source)
            if archived is None:
                if source == path:
                    raise ContractError(
                        f"B3: textový modify target chybí v archivu UTF-8: {path}"
                    )
                continue
            if archived["sha256"] != item.sha256:
                raise ContractError(f"B3: archivovaný originál neodpovídá IN: {source}")
            originals[source] = archived["content"]

    self._delivery_originals = originals
    self._delivery_overwrite_hashes = dict(
        self._delivery_expected_target_hashes
    )
    generated_text: dict[str, str] = {}
    resource_pending: list[dict[str, Any]] = []

    if self.cfg.send_as_c:
        pending_resources = {
            path
            for path, row in selected.items()
            if row.get("kind") != "text"
        }
        while pending_resources:
            ready = sorted(
                path
                for path in pending_resources
                if set(files_by_path[path].get("content_dependencies", [])) <= completed
            )
            if not ready:
                break
            for path in ready:
                result = dispatch_resource_target(
                    self,
                    client,
                    struct,
                    path,
                    generated_text=generated_text,
                )
                pending_resources.remove(path)
                if result["status"] == "completed_unverified":
                    completed.add(path)
                else:
                    resource_pending.append(result)

        text_scope = sorted(
            path
            for path, row in selected.items()
            if row.get("kind") == "text"
        )
        if not text_scope:
            saved_map = self._save_out_files([])
            return {
                "mode": "MODIFY",
                "plan": plan,
                "structure": struct,
                "saved": saved_map,
                "response_id": resp2_id,
                "vector_store_id": vs_id,
                "supports_file_search": supports_fs,
                "status": (
                    "waiting_manual_resource"
                    if resource_pending or pending_resources
                    else "dry_run"
                    if saved_map.get("dry_run")
                    else "files_complete_unverified"
                ),
                "dry_run": bool(saved_map.get("dry_run")),
                "resource_pending": resource_pending,
                "excluded_paths": excluded,
            }

        ready_text = {
            path
            for path in text_scope
            if set(files_by_path[path].get("content_dependencies", [])) <= completed
        }
        if not ready_text:
            saved_map = self._save_out_files([])
            blocked = sorted(set(text_scope) | pending_resources)
            self.log.update_state(
                {
                    "status": "waiting_manual_resource",
                    "resource_pending_paths": blocked,
                }
            )
            return {
                "mode": "MODIFY",
                "plan": plan,
                "structure": struct,
                "saved": saved_map,
                "status": "waiting_manual_resource",
                "dry_run": bool(self.cfg.dry_run),
                "blocked_paths": blocked,
                "resource_pending": resource_pending,
                "excluded_paths": excluded,
            }

        expected = {
            path: self._delivery_expected_target_hashes[path]
            for path in text_scope
        }
        manifest = build_batch_manifest(
            self.log.run_id,
            self.cfg.prompt,
            plan,
            struct,
            self.cfg.model,
            self.cfg.temperature,
            text_scope,
            requirements=self._delivery_snapshot["requirements"],
            source_segments=self._delivery_snapshot.get("source_segments"),
            requirements_wrapper=self._delivery_snapshot.get("requirements_wrapper"),
            maximum_quality=self.cfg.maximum_quality,
            mode="MODIFY",
            originals=originals,
            recovery_instruction=self.cfg.recovery_instruction,
            run_config=self.cfg,
            expected_target_hashes=expected,
            approved_paths=text_scope,
            completed_targets=completed,
            verified_artifacts=self._delivery_verified_artifacts,
        )
        manifest["overwrite_hashes"] = expected
        manifest["dry_run"] = bool(self.cfg.dry_run)
        manifest["versing"] = bool(self.cfg.versing)
        manifest["resource_completed_paths"] = sorted(
            path
            for path in completed
            if path in selected and selected[path].get("kind") != "text"
        )
        manifest["resource_pending_paths"] = sorted(pending_resources)
        manifest["resource_expected_target_hashes"] = {
            path: self._delivery_expected_target_hashes[path]
            for path, row in selected.items()
            if row.get("kind") != "text"
        }
        manifest["excluded_scope"] = excluded
        return self._submit_generate_batch(client, manifest)

    pending = set(selected)
    out_files: list[dict[str, Any]] = []
    chain_prev_id = ""
    total = max(1, len(pending))
    completed_count = 0
    while pending:
        ready = sorted(
            path
            for path in pending
            if set(files_by_path[path].get("content_dependencies", [])) <= completed
        )
        if not ready:
            break
        for path in ready:
            self._check_stop()
            target = files_by_path[path]
            pending.remove(path)
            action = str(target.get("action") or "modify")
            self._progress_stage = "B3"
            self.progress_event.emit(
                ProgressEvent(
                    "B3",
                    completed=completed_count,
                    total=total,
                    unit="cílů",
                    detail=path,
                )
            )
            if target.get("kind") == "text":
                content, last_response_id = self._gen_file_chunks(
                    client,
                    prev_id=chain_prev_id,
                    contract="B3_FILE",
                    path=path,
                    action=action,
                    diag_file_ids=diag_file_ids,
                    tools=tools if supports_fs else None,
                )
                if last_response_id:
                    chain_prev_id = last_response_id
                generated_text[path] = content
                out_files.append(
                    {"path": path, "content": content, "action": action}
                )
                completed.add(path)
            else:
                result = dispatch_resource_target(
                    self,
                    client,
                    struct,
                    path,
                    generated_text=generated_text,
                )
                if result["status"] == "completed_unverified":
                    completed.add(path)
                else:
                    resource_pending.append(result)
            completed_count += 1
            self.progress_event.emit(
                ProgressEvent(
                    "B3",
                    completed=completed_count,
                    total=total,
                    unit="cílů",
                    detail=path,
                )
            )

    blocked = sorted(pending)
    if blocked:
        resource_pending.extend(
            {
                "path": path,
                "status": "waiting_dependency",
                "dependencies": list(
                    files_by_path[path].get("dependencies", [])
                ),
            }
            for path in blocked
        )
    self._verify_completed_files()
    saved_map = self._save_out_files(out_files)
    status = (
        "waiting_manual_resource"
        if any(row.get("status") == "waiting_manual" for row in resource_pending)
        else "partial"
        if resource_pending
        else "dry_run"
        if saved_map.get("dry_run")
        else "files_complete_unverified"
    )
    self.log.update_state(
        {
            "resource_pending": resource_pending,
            "excluded_paths": excluded,
        }
    )
    return {
        "mode": "MODIFY",
        "plan": plan,
        "structure": struct,
        "saved": saved_map,
        "response_id": resp2_id,
        "vector_store_id": vs_id,
        "supports_file_search": supports_fs,
        "status": status,
        "dry_run": bool(saved_map.get("dry_run")),
        "missing_deliverables": [
            row["path"] for row in resource_pending
        ],
        "resource_pending": resource_pending,
        "excluded_paths": excluded,
    }


def _run_b_modify(self: RunContext, client: OpenAIClient, diag_file_ids: list[str], base_prev_id: str | None) -> dict[str, Any]:
    (root, items, up_items, tools, supports_fs, vs_id,
     _b_text, b_input_files, b_input_images) = prepare_modify_inputs(self, client, diag_file_ids)
    plan, struct, resp2_id = prepare_delivery(
        self, client, "MODIFY", base_prev_id,
        {"recovery_instruction": self.cfg.recovery_instruction,
         "diagnostics": self._diag_text if self._should_inline_diag_text() else "",
         "input_inventory_note": self._in_dir_fallback_note(),
         "reference_file_ids": self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)},
        b_input_files, b_input_images,
        tools if supports_fs else None)

    if self.cfg.stop_after_plan:
        self.progress_event.emit(
            ProgressEvent("B2Q" if self.cfg.maximum_quality else "B2", detail="Ověřená příprava je hotová; B3 nebylo spuštěno.")
        )
        return {
            "mode": "MODIFY", "plan": plan, "structure": struct,
            "status": "plan_ready", "checkpoint": "plan_ready",
            "response_id": resp2_id, "last_response_id": self._final_response_id or resp2_id,
            "dry_run": bool(self.cfg.dry_run),
        }

    spine_files = list((struct.get("spine") or {}).get("files") or [])
    touched_raw = [
        row for row in spine_files if row.get("action") in {"add", "modify"}
    ]
    preserved_raw = [
        row for row in spine_files if row.get("action") == "preserve"
    ]
    del preserved_raw
    validate_paths(spine_files)
    validate_modify_sources(struct, root, items, self.cfg.skip_paths or [])
    if any(item.get("action") not in ("add", "modify") for item in touched_raw):
        raise ContractError("B2: výrobní action musí být add nebo modify.")
    if not touched_raw:
        self._verify_completed_files()
        self.log.update_state({"no_changes": True, "written_files": []})
        self.progress_event.emit(ProgressEvent("B2", detail="Nebyla navržena žádná změna; do OUT se nebude zapisovat."))
        return {
            "mode": "MODIFY", "plan": plan, "structure": struct,
            "saved": {"saved": []}, "written_files": [], "no_changes": True,
            "status": "dry_run" if self.cfg.dry_run else "completed",
            "dry_run": bool(self.cfg.dry_run),
            "response_id": resp2_id, "last_response_id": self._final_response_id or resp2_id,
        }
    return _run_v3_modify_production(
        self,
        client,
        diag_file_ids,
        plan,
        struct,
        resp2_id,
        root,
        up_items,
        tools,
        supports_fs,
        vs_id,
    )

# Režim QA.


class ModifyExecutor:
    """Samostatné workflow MODIFY nad společnými službami běhu."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_b_modify(context, context.client, context.diag_file_ids, context.base_prev_id)
