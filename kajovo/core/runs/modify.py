from __future__ import annotations

import json
import os
from pathlib import Path
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


def _run_b_modify(self: RunContext, client: OpenAIClient, diag_file_ids: list[str], base_prev_id: str | None) -> dict[str, Any]:
    (root, items, up_items, tools, supports_fs, vs_id,
     b_text, b_input_files, b_input_images) = prepare_modify_inputs(self, client, diag_file_ids)
    plan, struct, resp2_id = prepare_delivery(
        self, client, "MODIFY", base_prev_id, b_text, b_input_files, b_input_images,
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
    touched = []
    omitted = []
    for tf in touched_raw:
        path = tf.get("path", "")
        if not path:
            continue
        if path in (self.cfg.skip_paths or []):
            continue
        ext = os.path.splitext(path)[1].lower()
        if tf.get("kind") != "text" or ext in (self.cfg.skip_exts or []):
            self._log_debug(f"B3: skipping due to extension {ext} ({path})")
            omitted.append(path)
            continue
        if path in (self.cfg.skip_paths or []):
            self._log_debug(f"B3: skipping already completed {path}")
            continue
        touched.append(tf)

    originals = {}
    source_items = {item.rel_path: item for item in up_items}
    overwrite_hashes = {}
    for file in touched:
        path = file["path"]
        if file["action"] == "modify" and path not in source_items:
            raise ContractError(f"B3: měněný soubor není dostupný ve schváleném IN: {path}")
        if file["action"] == "add" and os.path.lexists(safe_join_under_root(root, path)):
            raise ContractError(f"B3: přidávaný soubor již existuje v IN: {path}")
        relevant = {path, *file.get("dependencies", [])}
        for source in relevant:
            if source in (self.cfg.skip_paths or []):
                continue
            item = source_items.get(source)
            if item is None:
                continue
            source_path = safe_join_under_root(root, source)
            if sha256_file(source_path) != item.sha256:
                raise ContractError(f"IN se od skenu změnil: {source}")
            with open(source_path, encoding="utf-8") as stream:
                originals[source] = stream.read()
        target = safe_join_under_root(self.cfg.out_dir, path)
        if os.path.isfile(target):
            overwrite_hashes[path] = sha256_file(target)
    originals_path = self.log.find_json("manifests", "response_modify_originals") if self._response_journal else None
    if originals_path:
        saved_originals = json.loads(Path(originals_path).read_text(encoding="utf-8"))
        originals, overwrite_hashes = saved_originals["originals"], saved_originals["overwrite_hashes"]
    elif self._response_journal:
        self.log.save_json("manifests", "response_modify_originals", {
            "originals": originals, "overwrite_hashes": overwrite_hashes,
        })
    self._delivery_originals = originals
    self._delivery_overwrite_hashes = overwrite_hashes
    if self.cfg.send_as_c and touched:
        manifest = build_batch_manifest(
            self.log.run_id, self.cfg.prompt, plan, struct, self.cfg.model,
            self.cfg.temperature, [file["path"] for file in touched],
            requirements=self._delivery_snapshot["requirements"],
            maximum_quality=self.cfg.maximum_quality, mode="MODIFY", originals=originals,
            recovery_instruction=self.cfg.recovery_instruction,
            run_config=self.cfg, expected_target_hashes=overwrite_hashes)
        manifest["overwrite_hashes"] = overwrite_hashes
        manifest["dry_run"] = bool(self.cfg.dry_run)
        manifest["versing"] = bool(self.cfg.versing)
        return self._submit_generate_batch(client, manifest)

    total_files = len(touched)
    chain_prev_id = str(resp2_id or "")
    out_files: list[dict[str, Any]] = []
    generation_order = [
        row for row in touched_raw
        if row in touched or row["path"] in (self.cfg.skip_paths or [])
    ]
    for i, tf in enumerate(generation_order, start=1):
        self._check_stop()
        path = tf.get("path", "")
        if path in (self.cfg.skip_paths or []):
            if self._response_journal and path in self._response_file_ids:
                chain_prev_id = self._response_file_ids[path]
            continue
        action = tf.get("action", "modify")
        self._progress_stage = "B3"
        self.progress_event.emit(ProgressEvent("B3", completed=i - 1, total=total_files, unit="souborů", detail=str(path)))
        self._set(50 + int(35 * (i - 1) / max(1, len(touched))), 0, f"B3: {'upravuji' if action == 'modify' else 'přidávám'} {path} ({i}/{total_files})")
        content, last_resp_id = self._gen_file_chunks(
            client,
            prev_id=chain_prev_id,
            contract="B3_FILE",
            path=path,
            action=action,
            diag_file_ids=diag_file_ids,
            tools=tools if supports_fs else None,
        )
        if last_resp_id:
            chain_prev_id = last_resp_id
        out_files.append({"path": path, "content": content, "action": action})
        self.subprogress.emit(int(i * 100 / max(1, total_files)))
        self.progress_event.emit(ProgressEvent("B3", completed=i, total=total_files, unit="souborů", detail=str(path)))

    self._verify_completed_files()
    saved_map = self._save_out_files(out_files)
    if omitted:
        self.log.update_state({"missing_deliverables": omitted})
    return {"mode": "MODIFY", "plan": plan, "structure": struct, "saved": saved_map,
            "response_id": resp2_id, "vector_store_id": vs_id, "supports_file_search": supports_fs,
            "status": "partial" if omitted else "dry_run" if saved_map.get("dry_run") else "files_complete_unverified",
            "dry_run": bool(saved_map.get("dry_run")), "missing_deliverables": omitted}

# Režim QA.


class ModifyExecutor:
    """Samostatné workflow MODIFY nad společnými službami běhu."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_b_modify(context, context.client, context.diag_file_ids, context.base_prev_id)
