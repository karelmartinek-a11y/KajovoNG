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

def _run_a_generate(self: RunContext, client: OpenAIClient, diag_file_ids: list[str], base_prev_id: str | None) -> dict[str, Any]:
    # ReRun se známou strukturou přeskočí A1 a A2.
    plan: dict[str, Any] = {}
    struct: dict[str, Any]
    a3_model = self._generate_model("A3")
    files: list[dict[str, Any]] = []
    skipped_a3_deliverables: list[dict[str, Any]] = []
    auto_skip_image_exts = {".png", ".jpg", ".jpeg"}
    if self.cfg.resume_files and not self.cfg.preparation_snapshot:
        if self.cfg.send_as_c:
            raise ContractError("Starý ReRun neobsahuje společnou specifikaci. Opakujte dávku v panelu BATCH nebo spusťte nový A1/A2.")
        self._set(10, 0, "ReRun: používám uloženou strukturu A2; A1/A2 se neopakují.", stage="ReRun")
        struct = {"contract": "A2_STRUCTURE", "files": self.cfg.resume_files}
        resp2_id = self.cfg.resume_prev_id or self.cfg.response_id or None
        try:
            self.log.save_json(
                "manifests",
                f"resume_structure_{ts_code()}",
                {"resume_files": self.cfg.resume_files, "resume_prev_id": resp2_id},
            )
        except Exception as evidence_error:
            logging.getLogger(__name__).warning(
                "Zápis pomocné evidence selhal: %s", evidence_error
            )

        if self.cfg.stop_after_plan:
            return {
                "mode": "GENERATE", "plan": plan, "structure": struct,
                "status": "plan_ready", "checkpoint": "plan_ready",
                "response_id": resp2_id, "last_response_id": self._final_response_id or resp2_id,
            }

        files_raw = struct.get("files", []) or []
        if not files_raw:
            raise ContractError("GENERATE ReRun: uložená struktura neobsahuje žádný výstupní soubor.")
        validate_paths(files_raw)
        for f in files_raw:
            self._check_stop()
            path = f.get("path")
            if not isinstance(path, str) or not path:
                continue
            if path in (self.cfg.skip_paths or []):
                continue
            ext = os.path.splitext(path)[1].lower()
            if f.get("kind") != "text" or ext in auto_skip_image_exts:
                skipped_a3_deliverables.append(
                    {
                        "path": path,
                        "purpose": f.get("purpose", ""),
                        "language": f.get("language", ""),
                        "reason": (
                            "binární prostředek vyžaduje deklarovaný resource producer"
                            if f.get("kind") == "binary_required"
                            else "lokálně odvozený nebo automaticky negenerovaný typ výstupu"
                        ),
                    }
                )
                self._log_debug(f"A3: skipping generated image extension {ext} ({path})")
                continue
            if ext in (self.cfg.skip_exts or []):
                skipped_a3_deliverables.append({
                    "path": path, "purpose": f.get("purpose", ""),
                    "language": f.get("language", ""),
                    "reason": f"typ {ext or 'bez přípony'} je vyloučen z automatického generování",
                })
                self._log_debug(f"A3: skipping due to extension {ext} ({path})")
                continue
            if path in (self.cfg.skip_paths or []):
                self._log_debug(f"A3: skipping already completed {path}")
                continue
            files.append(f)
    else:
        a1_text = self.cfg.prompt or ""
        if self.cfg.recovery_instruction:
            a1_text += self._recovery_suffix()
        a1_text = self._with_diag_text(self._append_io_reference(
            a1_text, self._files_with_in_dir(self.cfg.attached_file_ids + diag_file_ids)))
        note = self._in_dir_fallback_note()
        if note:
            a1_text += "\n\n" + note
        input_files, input_images = self._build_input_attachments(client, self._input_file_ids())
        plan, struct, resp2_id = prepare_delivery(
            self, client, "GENERATE", base_prev_id, a1_text, input_files, input_images, self._fs_tools)
        if self.cfg.stop_after_plan:
            self.progress_event.emit(
                ProgressEvent("A2Q" if self.cfg.maximum_quality else "A2", detail="Ověřená příprava je hotová; A3 nebylo spuštěno.")
            )
            return {
                "mode": "GENERATE", "plan": plan, "structure": struct,
                "status": "plan_ready", "checkpoint": "plan_ready",
                "response_id": resp2_id, "last_response_id": self._final_response_id or resp2_id,
            }
        if self.cfg.send_as_c:
            selected = [
                f["path"]
                for f in struct["spine"]["files"]
                if f["kind"] == "text"
                and f["action"] == "generate"
                and os.path.splitext(f["path"])[1].lower() not in (self.cfg.skip_exts or [])
                and f["path"] not in (self.cfg.skip_paths or [])
            ]
            if selected:
                expected_target_hashes = {}
                for path in selected:
                    target = safe_join_under_root(self.cfg.out_dir, path)
                    expected_target_hashes[path] = (
                        sha256_file(target) if os.path.isfile(target) else None
                    )
                manifest = build_batch_manifest(
                    self.log.run_id,
                    self.cfg.prompt,
                    plan,
                    struct,
                    a3_model,
                    (
                        self.cfg.temperature
                        if self._model_caps(a3_model).get("supports_temperature", False)
                        else None
                    ),
                    selected,
                    requirements=self._delivery_snapshot["requirements"],
                    maximum_quality=self.cfg.maximum_quality,
                    recovery_instruction=self.cfg.recovery_instruction,
                    run_config=self.cfg,
                    expected_target_hashes=expected_target_hashes,
                )
                return self._submit_generate_batch(client, manifest)

        try:
            self.log.save_json(
                "manifests",
                f"resume_structure_{ts_code()}",
                {
                    "resume_files": list((struct.get("spine") or {}).get("files") or []),
                    "resume_prev_id": resp2_id,
                },
            )
        except Exception as evidence_error:
            logging.getLogger(__name__).warning(
                "Zápis pomocné evidence selhal: %s", evidence_error
            )

        files_raw = list((struct.get("spine") or {}).get("files") or [])
        if not files_raw:
            raise ContractError("GENERATE: IMPLEMENTATION_GRAPH_V3 neobsahuje žádný výstupní soubor.")
        for f in files_raw:
            self._check_stop()
            path = f.get("path")
            if not isinstance(path, str) or not path:
                continue
            if path in (self.cfg.skip_paths or []):
                continue
            ext = os.path.splitext(path)[1].lower()
            if f.get("kind") != "text" or ext in auto_skip_image_exts:
                skipped_a3_deliverables.append(
                    {
                        "path": path,
                        "purpose": f.get("purpose", ""),
                        "language": f.get("language", ""),
                        "reason": "binární nebo automaticky negenerovaný typ výstupu",
                    }
                )
                self._log_debug(f"A3: skipping generated image extension {ext} ({path})")
                continue
            if ext in (self.cfg.skip_exts or []):
                skipped_a3_deliverables.append({
                    "path": path, "purpose": f.get("purpose", ""),
                    "language": f.get("language", ""),
                    "reason": f"typ {ext or 'bez přípony'} je vyloučen z automatického generování",
                })
                self._log_debug(f"A3: skipping due to extension {ext} ({path})")
                continue
            if path in (self.cfg.skip_paths or []):
                self._log_debug(f"A3: skipping already completed {path}")
                continue
            files.append(f)

    from ..context_compiler import ContextCompiler
    compiler = ContextCompiler(self._delivery_snapshot)
    wave_rank = {
        path: wave_index
        for wave_index, wave in enumerate(compiler.graph.get("waves") or [])
        for path in wave
    }
    files.sort(key=lambda row: (wave_rank.get(row["path"], 10**9), row["path"]))
    self._delivery_verified_artifacts = {}

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
