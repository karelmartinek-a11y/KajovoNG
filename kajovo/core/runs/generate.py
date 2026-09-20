from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from ..contracts import (
    ContractError,
    validate_paths,
)
from ..delivery_preparation import (
    prepare_delivery,
)
from ..generate_batch import build_manifest as build_batch_manifest
from ..openai_client import OpenAIClient
from ..progress import ProgressEvent
from ..utils import safe_join_under_root, sha256_file, ts_code

if TYPE_CHECKING:
    from .context import RunContext

def _run_v3_generate_production(
    self: RunContext,
    client: OpenAIClient,
    diag_file_ids: list[str],
    plan: dict[str, Any],
    struct: dict[str, Any],
    resp2_id: str | None,
    a3_model: str,
) -> dict[str, Any]:
    from ..orchestration.resource_delivery import (
        dispatch_resource_target,
        prepare_production_scope,
    )
    from ..orchestration.waves import build_execution_dag

    selected, completed, excluded = prepare_production_scope(self, struct)
    files_by_path = {
        str(row["path"]): row for row in struct["spine"]["files"]
    }
    dag = build_execution_dag(struct)
    self._delivery_verified_artifacts = {}
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
                if set(files_by_path[path].get("dependencies", [])) <= completed
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
            status = (
                "waiting_manual_resource"
                if resource_pending or pending_resources
                else "files_complete_unverified"
            )
            return {
                "mode": "GENERATE",
                "plan": plan,
                "structure": struct,
                "saved": saved_map,
                "status": status,
                "excluded_paths": excluded,
                "resource_pending": resource_pending,
                "response_id": resp2_id,
                "last_response_id": self._final_response_id or resp2_id,
            }

        ready_text = {
            path
            for path in text_scope
            if set(files_by_path[path].get("dependencies", [])) <= completed
        }
        if not ready_text:
            saved_map = self._save_out_files([])
            waiting = sorted(
                set(text_scope) | pending_resources
            )
            self.log.update_state(
                {
                    "status": "waiting_manual_resource",
                    "resource_pending_paths": waiting,
                }
            )
            return {
                "mode": "GENERATE",
                "plan": plan,
                "structure": struct,
                "saved": saved_map,
                "status": "waiting_manual_resource",
                "resource_pending": resource_pending,
                "blocked_paths": waiting,
                "excluded_paths": excluded,
                "response_id": resp2_id,
                "last_response_id": self._final_response_id or resp2_id,
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
            a3_model,
            (
                self.cfg.temperature
                if self._model_caps(a3_model).get(
                    "supports_temperature", False
                )
                else None
            ),
            text_scope,
            requirements=self._delivery_snapshot["requirements"],
            maximum_quality=self.cfg.maximum_quality,
            recovery_instruction=self.cfg.recovery_instruction,
            run_config=self.cfg,
            expected_target_hashes=expected,
            approved_paths=text_scope,
            completed_targets=completed,
        )
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
    total = max(1, len(pending))
    completed_count = 0
    while pending:
        ready = sorted(
            path
            for path in pending
            if set(files_by_path[path].get("dependencies", [])) <= completed
        )
        if not ready:
            break
        for path in ready:
            self._check_stop()
            target = files_by_path[path]
            pending.remove(path)
            self._progress_stage = "A3"
            self.progress_event.emit(
                ProgressEvent(
                    "A3",
                    completed=completed_count,
                    total=total,
                    unit="cílů",
                    detail=path,
                )
            )
            if target.get("kind") == "text":
                content, _last_response_id = self._gen_file_chunks(
                    client,
                    prev_id="",
                    contract="A3_FILE",
                    path=path,
                    action=None,
                    diag_file_ids=diag_file_ids,
                    tools=self._fs_tools,
                    model_override=a3_model,
                )
                generated_text[path] = content
                out_files.append(
                    {
                        "path": path,
                        "content": content,
                        "purpose": target.get("purpose", ""),
                    }
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
                    "A3",
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
    missing_report = self._write_missing_files_report(
        [
            {
                "path": row["path"],
                "purpose": files_by_path[row["path"]].get("purpose", "")
                if row["path"] in files_by_path
                else "",
                "reason": row["status"],
            }
            for row in resource_pending
        ]
    )
    status = (
        "waiting_manual_resource"
        if any(row.get("status") == "waiting_manual" for row in resource_pending)
        else "partial"
        if resource_pending
        else "files_complete_unverified"
    )
    self.log.update_state(
        {
            "resource_pending": resource_pending,
            "excluded_paths": excluded,
        }
    )
    return {
        "mode": "GENERATE",
        "plan": plan,
        "structure": struct,
        "saved": saved_map,
        "status": status,
        "no_changes": not out_files and not getattr(
            self, "_resource_staged_files", {}
        ),
        "response_id": resp2_id,
        "last_response_id": self._final_response_id or resp2_id,
        "missing_files_report": missing_report,
        "missing_deliverables": [
            row["path"] for row in resource_pending
        ],
        "resource_pending": resource_pending,
        "excluded_paths": excluded,
    }


def _run_a_generate(
    self: RunContext,
    client: OpenAIClient,
    diag_file_ids: list[str],
    base_prev_id: str | None,
) -> dict[str, Any]:
    plan: dict[str, Any] = {}
    a3_model = self._generate_model("A3")

    # Legacy read path: it can replay the historical text-only structure but
    # must not pretend to understand the new resource producer contract.
    if self.cfg.resume_files and not self.cfg.preparation_snapshot:
        if self.cfg.send_as_c:
            raise ContractError(
                "Starý ReRun neobsahuje společnou V3 specifikaci. "
                "Opakujte dávku v panelu BATCH nebo spusťte novou přípravu."
            )
        self._set(
            10,
            0,
            "ReRun: používám uloženou legacy strukturu A2; A1/A2 se neopakují.",
            stage="ReRun",
        )
        struct: dict[str, Any] = {
            "contract": "A2_STRUCTURE",
            "files": self.cfg.resume_files,
        }
        resp2_id = self.cfg.resume_prev_id or self.cfg.response_id or None
        self.log.save_json(
            "manifests",
            f"resume_structure_{ts_code()}",
            {
                "resume_files": self.cfg.resume_files,
                "resume_prev_id": resp2_id,
                "legacy_text_only": True,
            },
        )
        if self.cfg.stop_after_plan:
            return {
                "mode": "GENERATE",
                "plan": plan,
                "structure": struct,
                "status": "plan_ready",
                "checkpoint": "plan_ready",
                "response_id": resp2_id,
                "last_response_id": self._final_response_id or resp2_id,
            }

        files_raw = list(struct.get("files") or [])
        if not files_raw:
            raise ContractError(
                "GENERATE ReRun: uložená struktura neobsahuje žádný výstupní soubor."
            )
        validate_paths(files_raw)
        files: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in files_raw:
            path = str(row.get("path") or "")
            if not path:
                continue
            suffix = os.path.splitext(path)[1].lower()
            if path in (self.cfg.skip_paths or []) or suffix in (self.cfg.skip_exts or []):
                skipped.append(
                    {
                        "path": path,
                        "purpose": row.get("purpose", ""),
                        "reason": "explicitně vynecháno uživatelem",
                    }
                )
                continue
            if row.get("kind") != "text":
                skipped.append(
                    {
                        "path": path,
                        "purpose": row.get("purpose", ""),
                        "reason": (
                            "legacy struktura nemá doložený resource producer; "
                            "netextový cíl nelze bezpečně vyrobit"
                        ),
                    }
                )
                continue
            files.append(row)

        # Legacy FileContext is already frozen by recovery code when this path is
        # allowed. Target state is nevertheless captured before the first A3 call.
        self._delivery_expected_target_hashes = {}
        for row in files:
            target = safe_join_under_root(self.cfg.out_dir, row["path"])
            self._delivery_expected_target_hashes[row["path"]] = (
                sha256_file(target) if os.path.isfile(target) else None
            )
        self._delivery_verified_artifacts = {}

        out_files: list[dict[str, Any]] = []
        for index, row in enumerate(files, 1):
            path = row["path"]
            self._check_stop()
            self.progress_event.emit(
                ProgressEvent(
                    "A3",
                    completed=index - 1,
                    total=len(files),
                    unit="souborů",
                    detail=path,
                )
            )
            content_value, _response_id = self._gen_file_chunks(
                client,
                prev_id="",
                contract="A3_FILE",
                path=path,
                action=None,
                diag_file_ids=diag_file_ids,
                tools=self._fs_tools,
                model_override=a3_model,
            )
            out_files.append(
                {
                    "path": path,
                    "content": content_value,
                    "purpose": row.get("purpose", ""),
                }
            )

        self._verify_completed_files()
        saved_map = self._save_out_files(out_files)
        missing_report = self._write_missing_files_report(skipped)
        missing = [row["path"] for row in skipped]
        return {
            "mode": "GENERATE",
            "plan": plan,
            "structure": struct,
            "saved": saved_map,
            "status": "partial" if missing else "files_complete_unverified",
            "no_changes": not out_files and not missing,
            "response_id": resp2_id,
            "last_response_id": self._final_response_id or resp2_id,
            "missing_files_report": missing_report,
            "missing_deliverables": missing,
        }

    a1_text = self.cfg.prompt or ""
    if self.cfg.recovery_instruction:
        a1_text += self._recovery_suffix()
    a1_text = self._with_diag_text(
        self._append_io_reference(
            a1_text,
            self._files_with_in_dir(
                self.cfg.attached_file_ids + diag_file_ids
            ),
        )
    )
    note = self._in_dir_fallback_note()
    if note:
        a1_text += "\n\n" + note
    input_files, input_images = self._build_input_attachments(
        client, self._input_file_ids()
    )
    plan, struct, resp2_id = prepare_delivery(
        self,
        client,
        "GENERATE",
        base_prev_id,
        a1_text,
        input_files,
        input_images,
        self._fs_tools,
    )
    if self.cfg.stop_after_plan:
        self.progress_event.emit(
            ProgressEvent(
                "A2Q" if self.cfg.maximum_quality else "A2",
                detail="Ověřená příprava je hotová; A3 nebylo spuštěno.",
            )
        )
        return {
            "mode": "GENERATE",
            "plan": plan,
            "structure": struct,
            "status": "plan_ready",
            "checkpoint": "plan_ready",
            "response_id": resp2_id,
            "last_response_id": self._final_response_id or resp2_id,
        }

    self.log.save_json(
        "manifests",
        f"resume_structure_{ts_code()}",
        {
            "resume_files": list((struct.get("spine") or {}).get("files") or []),
            "resume_prev_id": resp2_id,
        },
    )
    return _run_v3_generate_production(
        self,
        client,
        diag_file_ids,
        plan,
        struct,
        resp2_id,
        a3_model,
    )


class GenerateExecutor:
    """Samostatné workflow GENERATE nad společnými službami běhu."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_a_generate(
            context,
            context.client,
            context.diag_file_ids,
            context.base_prev_id,
        )
