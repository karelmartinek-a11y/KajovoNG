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


def _run_a_generate(self: RunContext, client: OpenAIClient, diag_file_ids: list[str], base_prev_id: str | None) -> dict[str, Any]:
    # ReRun se známou strukturou přeskočí A1 a A2.
    plan: dict[str, Any] = {}
    struct: dict[str, Any]
    a3_model = self._generate_model("A3")
    files: list[dict[str, Any]] = []
    skipped_a3_deliverables: list[dict[str, Any]] = []
    auto_skip_image_exts = {".png", ".jpg", ".jpeg"}
    if self.cfg.resume_files and not self.cfg.preparation_snapshot:
        return _run_v3_generate_production(
            self,
            client,
            diag_file_ids,
            plan,
            struct,
            resp2_id,
            a3_model,
        )

    from ..context_compiler import ContextCompiler
    compiler = ContextCompiler(self._delivery_snapshot)
    wave_rank = {
        path: wave_index
        for wave_index, wave in enumerate(compiler.graph.get("waves") or [])
        for path in wave
    }
    files.sort(key=lambda row: (wave_rank.get(row["path"], 10**9), row["path"]))
    self._delivery_verified_artifacts = {}
    self._delivery_expected_target_hashes = {}
    for row in files:
        target = safe_join_under_root(self.cfg.out_dir, row["path"])
        self._delivery_expected_target_hashes[row["path"]] = (
            sha256_file(target) if os.path.isfile(target) else None
        )

    total_files = len(files)
    base_a3_prev_id = ""
    out_files: list[dict[str, Any]] = []
    for idx, f in enumerate(files, start=1):
        self._check_stop()
        path = f.get("path")
        if not isinstance(path, str) or not path:
            continue
        self._progress_stage = "A3"
        self.progress_event.emit(ProgressEvent("A3", completed=idx - 1, total=total_files, unit="souborů", detail=str(path)))
        self._set(30 + int(45 * (idx - 1) / max(1, len(files))), 0, f"A3: generuji soubor {path} ({idx}/{total_files})")
        content, _last_resp_id = self._gen_file_chunks(
            client,
            prev_id=base_a3_prev_id,
            contract="A3_FILE",
            path=path,
            action=None,
            diag_file_ids=diag_file_ids,
            tools=self._fs_tools,
            model_override=a3_model,
        )
        out_files.append({"path": path, "content": content, "purpose": f.get("purpose", "")})
        self.subprogress.emit(int(idx * 100 / max(1, total_files)))
        self.progress_event.emit(ProgressEvent("A3", completed=idx, total=total_files, unit="souborů", detail=str(path)))

    self._verify_completed_files()
    saved_map = self._save_out_files(out_files)
    missing_report = self._write_missing_files_report(skipped_a3_deliverables)
    missing_deliverables = [item.get("path") for item in skipped_a3_deliverables if item.get("path")]
    no_changes = not out_files and not missing_deliverables
    if missing_deliverables:
        self.log.update_state({"missing_deliverables": missing_deliverables})
    elif no_changes:
        self.log.update_state({"no_changes": True, "written_files": []})
    return {
        "mode": "GENERATE",
        "plan": plan,
        "structure": struct,
        "saved": saved_map,
        "status": "partial" if missing_deliverables else "files_complete_unverified",
        "no_changes": no_changes,
        "response_id": resp2_id,
        "last_response_id": self._final_response_id or resp2_id,
        "missing_files_report": missing_report,
        "missing_deliverables": missing_deliverables,
    }

# Odeslání souborových úloh po živé přípravě.


class GenerateExecutor:
    """Samostatné workflow GENERATE nad společnými službami běhu."""

    def execute(self, context: RunContext) -> dict[str, Any]:
        return _run_a_generate(context, context.client, context.diag_file_ids, context.base_prev_id)
