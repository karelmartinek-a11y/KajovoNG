from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..contracts import (
    ContractError,
)
from ..delivery_preparation import validate_preparation_snapshot
from ..openai_client import OpenAIClient
from ..progress import ProgressEvent
from ..utils import safe_join_under_root, sha256_file

if TYPE_CHECKING:
    from .context import RunContext

def prepare_runtime(self: RunContext, client: OpenAIClient) -> tuple[list[str], str | None]:
    runtime_path = self.log.find_json("manifests", "response_runtime") if self._response_journal else None
    if runtime_path:
        runtime = json.loads(Path(runtime_path).read_text(encoding="utf-8"))
        for name, value in runtime["attributes"].items():
            setattr(self, name, value)
        diag_file_ids = runtime["diag_file_ids"]
        self.cfg.preparation_snapshot = runtime["preparation_snapshot"]
        self.cfg.response_id = runtime["response_id"]
        self.cfg.resume_files = runtime["resume_files"]
        self.cfg.resume_prev_id = runtime["resume_prev_id"]
    else:
        self._prepare_response_runtime(client)
        diag_file_ids = self._runtime_diag_file_ids
        if self._response_journal:
            self.log.save_json("manifests", "response_runtime", {
                "attributes": {name: getattr(self, name) for name in (
                    "_diag_text", "_in_dir_info", "_vector_store_ids", "_diag_vector_store_ids",
                    "_fs_tools", "_diag_zip_path", "_input_kind_cache", "_file_name_cache")},
                "diag_file_ids": diag_file_ids, "preparation_snapshot": self.cfg.preparation_snapshot,
                "response_id": self.cfg.response_id, "resume_files": self.cfg.resume_files,
                "resume_prev_id": self.cfg.resume_prev_id,
            })

    # Zpracování dlouhého zadání.
    # GENERATE/MODIFY zavádí zadání přes A0 s previous_response_id.
    # QA odesílá zadání v textových částech zprávy.
    if self.cfg.preparation_snapshot and self.cfg.mode in ("GENERATE", "MODIFY"):
        checkpoint = validate_preparation_snapshot(self.cfg.preparation_snapshot, self.cfg.mode, self.cfg.maximum_quality)
        base_prev_id = checkpoint["response_id"]
    elif self.cfg.mode in ("GENERATE", "MODIFY"):
        base_prev_id = self._ingest_prompt_if_needed(client, prev_id=self.cfg.response_id or None)
    elif self.cfg.mode == "QA" and bool(getattr(self.cfg, "qa_continue_conversation", False)):
        base_prev_id = self.cfg.response_id or None
    else:
        base_prev_id = None

    return diag_file_ids, base_prev_id

def _recovery_suffix(self: RunContext) -> str:
    instruction = str(getattr(self.cfg, "recovery_instruction", "") or "").strip()
    if not instruction:
        return ""
    return (
        "\n\n[NOVÁ VĚTEV - explicitní pokyn platí pouze pro nově prováděnou část]\n"
        + instruction
    )


def _verify_completed_files(self: RunContext):
    """ReRun přeskočí pouze doložený soubor, který uživatel mezitím nezměnil."""
    hashes = getattr(self.cfg, "completed_hashes", None) or {}
    entries = []
    for path in self.cfg.skip_paths or []:
        target = safe_join_under_root(self.cfg.out_dir, path)
        if not hashes.get(path) or not os.path.isfile(target) or sha256_file(target) != hashes[path]:
            raise ContractError(f"ReRun: dokončený soubor nemá platný důkaz zápisu: {path}")
        entries.append({"path": path, "sha256": hashes[path], "dst": target})
    if entries:
        self.log.save_json("manifests", "out_completed_evidence", {
            "out_dir": self.cfg.out_dir, "saved": entries,
        })

# Sestavení požadavků.


def _ingest_prompt_if_needed(self: RunContext, client: OpenAIClient, prev_id: str | None) -> str | None:
    """Zachová přesný dlouhý vstup lokálně; příjem proběhne v pracovní A0R/B0R."""
    from ..recoverable_artifacts import save_artifact
    prompt = self.cfg.prompt or ""
    if len(prompt) > 150_000:
        save_artifact(self.log.paths.run_dir, "source_prompt", {"text": prompt})
        self.progress_event.emit(ProgressEvent("A0", completed=1, total=1,
            unit="zadání", detail=f"Uloženo přesné zadání: {len(prompt):,} znaků; bez placených potvrzení částí."))
    return prev_id
