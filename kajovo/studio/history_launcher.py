"""Jediná služba pro přímé pokračování, rerun a repair z Historie."""

from __future__ import annotations

import copy
import shutil
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from kajovo.core.batch_completion import pending_batch_ids, read_state
from kajovo.core.cascade_pipeline import CascadeRunConfig
from kajovo.core.cascade_types import CascadeDefinition
from kajovo.core.delivery_preparation import validate_preparation_snapshot
from kajovo.core.model_capabilities import ModelCapabilitiesCache
from kajovo.core.orchestration.run_config import require_resumable_run_config_v2
from kajovo.core.runlog import RunLogger, verified_output_evidence
from kajovo.core.runs.config import UiRunConfig
from kajovo.core.runs.live_continuation import inherit_pending, pending_live, read_evidence
from kajovo.core.utils import new_run_id
from kajovo.studio.workers.cascade_worker import CascadeRunWorker
from kajovo.studio.workers.run_worker import RunWorker

SUPPORTED_DIRECT_MODES = {"GENERATE", "MODIFY", "QA", "QFILE", "KASKADA"}


@dataclass(frozen=True)
class BranchPreview:
    relation: str
    source_run_id: str
    checkpoint_id: str
    checkpoint_type: str
    selected_stage: str
    inherited_stages: tuple[str, ...]
    skipped_paths: tuple[str, ...]
    first_paid_operation: str
    source_error: str
    technical_error: str
    impact: str
    output_dir: str = ""


def first_paid_operation(mode: str, checkpoint: dict[str, Any], selected_stage: str = "") -> str:
    state = checkpoint.get("state_snapshot") if isinstance(checkpoint.get("state_snapshot"), dict) else {}
    if mode in {"GENERATE", "MODIFY"}:
        snapshot = state.get("preparation_snapshot") if isinstance(state.get("preparation_snapshot"), dict) else {}
        prefix = "A" if mode == "GENERATE" else "B"
        stages = [prefix + "0R", prefix + "1", prefix + "2"]
        if snapshot.get("maximum_quality"):
            stages.append(prefix + "2Q")
        stage = str(snapshot.get("canonical_stage") or "")
        if stage in stages:
            index = stages.index(stage) + 1
            return stages[index] if index < len(stages) else prefix + "3"
        return prefix + "0R"
    if mode == "KASKADA":
        definition = state.get("cascade_definition") or {}
        steps = definition.get("steps") or []
        return str(state.get("next_step_id") or (steps[0].get("id") if steps else "První krok kaskády"))
    return mode or "Není evidováno"


class HistoryBranchLauncher:
    def __init__(self, context, refreshed: Callable[[], None] | None = None):
        self.context = context
        self.refreshed = refreshed or (lambda: None)
        self._launched: set[tuple[str, str, str]] = set()

    @staticmethod
    def _source_state(adapter):
        state = read_state(adapter.root)
        if pending_batch_ids(state):
            raise ValueError("Zdrojový běh již odeslal BATCH; dokončete jej v původním běhu.")
        pending = state.get("response_pending") or {}
        if state.get("status") == "submission_unknown" or (pending.get("status") == "submitting" and not pending.get("id")):
            raise ValueError("Výsledek původního odeslání není potvrzen; nový submit je zablokován.")
        return state

    def preview(self, adapter, checkpoint_id: str, relation: str, selected_stage: str = "") -> BranchPreview:
        if relation not in {"continue", "rerun", "repair"}:
            raise ValueError("Neplatný typ historické větve.")
        if not adapter.bundle or adapter.legacy:
            raise ValueError("Legacy běh nelze přímo spustit bez explicitního kanonického checkpointu.")
        integrity = adapter.bundle.verify_integrity()
        if integrity.get("status") == "changed":
            raise ValueError("Zdrojový Run Bundle neprošel kontrolou integrity.")
        checkpoint = adapter.bundle.validate_checkpoint(checkpoint_id)
        state = checkpoint.get("state_snapshot")
        if not isinstance(state, dict):
            raise ValueError("Checkpoint nemá platný stavový snímek.")
        current_state = self._source_state(adapter)
        preview_live = pending_live(current_state) and relation == "continue"
        if preview_live:
            read_evidence(adapter.root)
        if (current_state.get("response_pending") or {}).get("status") in {"queued", "in_progress", "submitting"} and relation == "repair":
            raise ValueError("Rozpracovanou LIVE odpověď nejprve převezměte přes Continue.")
        ui = state.get("ui_state") if isinstance(state.get("ui_state"), dict) else {}
        if ui.get("in_dir") and not (state.get("input_archive") or {}).get("complete"):
            raise ValueError("Bod obnovy nedokládá úplný archiv vstupního adresáře. Použijte klon a zkontrolujte jeho podklady.")
        mode = str(ui.get("mode") or state.get("mode") or adapter.run_record().get("mode") or "")
        if preview_live and mode not in {"GENERATE", "MODIFY"}:
            raise ValueError("Tento režim nemá doložený adaptér obnovy LIVE odpovědi.")
        if mode not in SUPPORTED_DIRECT_MODES:
            raise ValueError("Tento typ běhu nemá bezpečný přímý launcher; použijte jeho doménovou obrazovku.")
        require_resumable_run_config_v2(ui, mode)
        snapshot = state.get("preparation_snapshot") if isinstance(state.get("preparation_snapshot"), dict) else {}
        inherited = []
        if snapshot.get("canonical_stage"):
            prefix = "A" if mode == "GENERATE" else "B"
            ordered = [prefix + "0R", prefix + "1", prefix + "2", prefix + "2Q"]
            inherited = ordered[: ordered.index(snapshot["canonical_stage"]) + 1] if snapshot["canonical_stage"] in ordered else []
        elif mode == "KASKADA":
            runtime = state.get("cascade_runtime") if isinstance(state.get("cascade_runtime"), dict) else {}
            inherited = [str(value) for value in runtime.get("executed_step_ids") or [] if value]
        evidence = verified_output_evidence(adapter.root, ui.get("out_dir")) if mode in {"GENERATE", "MODIFY"} else []
        skipped = tuple(sorted(row["path"] for row in evidence if row.get("path") and row.get("sha256")))
        source_state = read_state(adapter.root)
        error = str(source_state.get("human_error") or source_state.get("error") or adapter.run_record().get("output_summary") or "Popis chyby nebyl uložen.")
        technical_error = str(source_state.get("technical_error") or source_state.get("error") or "Technický detail nebyl uložen.")
        checkpoint_type = str(checkpoint.get("checkpoint_type") or "")
        output_ui = self._branch_ui(ui, mode, relation, checkpoint_type)
        return BranchPreview(
            relation, adapter.run_id, checkpoint_id, checkpoint_type,
            selected_stage, tuple(inherited), skipped,
            ("GET existující odpovědi; bez nového POST" if preview_live
             else first_paid_operation(mode, checkpoint, selected_stage)), error, technical_error,
            "Zdrojový běh zůstane neměnný; vznikne nový Run ID a nová lineage větev.",
            str(self._output_dir(mode, state, output_ui) or ""),
        )

    @staticmethod
    def _branch_ui(ui, mode, relation, checkpoint_type):
        result = copy.deepcopy(ui)
        if mode != "MODIFY":
            result["dry_run"] = False
        if relation == "continue" and checkpoint_type == "plan_ready" and mode in {"GENERATE", "MODIFY"}:
            result["stop_after_plan"] = False
            result["execution_approval_id"] = ""
        return result

    @staticmethod
    def _output_dir(mode, state, ui):
        value = state.get("out_dir") if mode == "KASKADA" else (
            ui.get("out_dir")
            if mode != "QA" and not ui.get("send_as_c")
            and not ui.get("dry_run") and not ui.get("stop_after_plan")
            else None
        )
        return Path(str(value)).resolve() if value else None

    def launch_async(self, adapter, preview, repair_instruction="", receive=None):
        key = (preview.source_run_id, preview.checkpoint_id, preview.relation)
        if key in self._launched:
            raise ValueError("Tato potvrzovací akce již byla spuštěna.")
        if not self.context.api_key:
            raise ValueError("Přímé spuštění vyžaduje uložený přístupový klíč.")
        self._launched.add(key)
        operations = self.context.operations

        def prepare(task):
            try:
                result = self.launch(adapter, preview, repair_instruction, _prepare_only=True)
                result[1].moveToThread(operations.thread())
                return result
            except Exception:
                self._launched.discard(key)
                raise

        def prepared(result):
            run_id, worker, output_dir, project = result
            try:
                record = self._adopt(preview, run_id, worker, output_dir, project)
            except Exception:
                worker.deleteLater()
                self._launched.discard(key)
                raise
            preparation.result = {"run_id": run_id, "status": "started"}
            preparation.dialog.hide()
            if receive:
                receive(record)

        try:
            preparation = operations.start("Příprava nové větve", prepare, prepared,
                                           output_dir=preview.output_dir or None)
            return preparation
        except Exception:
            self._launched.discard(key)
            raise

    def _adopt(self, preview, run_id, worker, output_dir, project):
        record = self.context.operations.adopt(
            f"{preview.relation.upper()} · {project}", worker,
            receive=lambda value: self.refreshed(), identifier=run_id, output_dir=output_dir)
        record.worker.finished.connect(self.refreshed)
        return record

    def launch(self, adapter, preview: BranchPreview, repair_instruction: str = "", *, _prepare_only=False):
        key = (preview.source_run_id, preview.checkpoint_id, preview.relation)
        if key in self._launched and not _prepare_only:
            raise ValueError("Tato potvrzovací akce již byla spuštěna; její druhý submit je zablokován.")
        if not self.context.api_key:
            raise ValueError("Přímé spuštění vyžaduje uložený přístupový klíč.")
        if adapter.bundle.verify_integrity().get("status") == "changed":
            raise ValueError("Zdrojový běh se změnil; kontrola integrity spuštění zablokovala.")
        checkpoint = adapter.bundle.validate_checkpoint(preview.checkpoint_id)
        state = copy.deepcopy(checkpoint["state_snapshot"])
        ui = copy.deepcopy(state.get("ui_state") or {})
        mode = str(ui.get("mode") or state.get("mode") or adapter.run_record().get("mode") or "")
        ui = self._branch_ui(ui, mode, preview.relation, preview.checkpoint_type)
        current_state = self._source_state(adapter)
        if preview.relation == "continue" and pending_live(current_state) and mode not in {"GENERATE", "MODIFY"}:
            raise ValueError("Tento režim nemá doložený adaptér obnovy LIVE odpovědi.")
        if (current_state.get("response_pending") or {}).get("status") in {"queued", "in_progress", "submitting"} and preview.relation == "repair":
            raise ValueError("Rozpracovanou LIVE odpověď nejprve převezměte přes Continue.")
        reserved_output = self._output_dir(mode, state, ui)
        if _prepare_only:
            if str(reserved_output or "") != preview.output_dir:
                raise ValueError("Výstupní adresář se od potvrzeného náhledu změnil.")
        else:
            self.context.operations.assert_output_available(reserved_output)
        run_id = new_run_id()
        self._launched.add(key)
        try:
            if mode == "KASKADA":
                worker, output_dir, project = self._cascade_worker(
                    run_id, adapter, state, preview, repair_instruction
                )
            else:
                worker, output_dir, project = self._standard_worker(run_id, adapter, ui, state, preview, repair_instruction)
            if _prepare_only:
                return run_id, worker, output_dir, project
            return self._adopt(preview, run_id, worker, output_dir, project)
        except Exception:
            self._launched.discard(key)
            raise

    def _standard_worker(self, run_id, adapter, ui, state, preview, repair_instruction):
        from .workbench import default_state

        current = self._source_state(adapter)
        live = read_evidence(adapter.root) if preview.relation == "continue" and pending_live(current) else None
        if live:
            if repair_instruction:
                raise ValueError("Continue LIVE nesmí měnit zmrazené zadání.")
            ui = copy.deepcopy(live["source_state"].get("ui_state") or {})
            state = copy.deepcopy(live["source_state"])
        source_mode = str(ui.get("mode") or state.get("mode") or "")
        require_resumable_run_config_v2(ui, source_mode)
        merged = self._branch_ui(
            {**default_state(self.context.settings), **ui}, source_mode,
            preview.relation, preview.checkpoint_type,
        )
        if merged.get("in_dir"):
            from kajovo.core.utils import safe_join_under_root

            from .history_artifacts import ArtifactGuard
            archive = state.get("input_archive") or {}
            if not archive.get("complete"):
                raise ValueError("Chybí úplný archiv vstupů pro nový běh.")
            artifacts = {row.get("artifact_id"): row for row in adapter.artifacts()}
            staging = Path(self.context.settings.cache_dir).resolve() / "history_inputs" / run_id
            staging.mkdir(parents=True, exist_ok=False)
            guard = ArtifactGuard(adapter.root)
            for identifier in archive.get("artifact_ids") or []:
                record = artifacts.get(identifier)
                if not record:
                    raise ValueError("Chybí archivovaný vstup: " + identifier)
                source = guard.resolve(record)
                relative = (record.get("metadata") or {}).get("relative_path") or record.get("reconstruction_role")
                if not relative:
                    raise ValueError("Vstup nemá evidovanou relativní cestu.")
                destination = Path(safe_join_under_root(str(staging), relative))
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            merged["in_dir"] = str(staging)
        merged["recovery_instruction"] = repair_instruction
        merged["source_checkpoint_id"] = preview.checkpoint_id
        snapshot = state.get("preparation_snapshot")
        if snapshot and merged.get("mode") in {"GENERATE", "MODIFY"}:
            merged["preparation_snapshot"] = validate_preparation_snapshot(
                snapshot, merged["mode"], bool(merged.get("maximum_quality"))
            )
        if (
            preview.relation == "continue"
            and preview.checkpoint_type == "plan_ready"
            and merged.get("mode") in {"GENERATE", "MODIFY"}
        ):
            if not merged.get("preparation_snapshot"):
                raise ValueError(
                    "Checkpoint plan_ready nemá úplný ověřený preparation snapshot."
                )
            prefix = "A" if merged["mode"] == "GENERATE" else "B"
            expected_stage = (
                prefix + "2Q"
                if bool(merged.get("maximum_quality"))
                else prefix + "2"
            )
            if merged["preparation_snapshot"].get("canonical_stage") != expected_stage:
                raise ValueError(
                    "Checkpoint plan_ready nekončí na poslední požadované fázi přípravy."
                )
            merged["stop_after_plan"] = False
            # Nový běh dostane od executor-u novou autorizaci svázanou s novým
            # Run ID a znovu ověřeným SourcePackem. Starý approval se nedědí.
            merged["execution_approval_id"] = ""
        evidence = verified_output_evidence(adapter.root, merged.get("out_dir"))
        hashes = {row["path"]: row["sha256"] for row in evidence if row.get("path") and row.get("sha256")}
        if preview.relation in {"continue", "repair"}:
            merged["completed_hashes"] = hashes
            merged["skip_paths"] = sorted(hashes)
        else:
            merged["completed_hashes"] = {}
            merged["skip_paths"] = []
        cache = ModelCapabilitiesCache("")
        models = list(self.context.models) or [value for key, value in merged.items() if key.startswith("model") and isinstance(value, str) and value]
        merged["available_models"] = models
        merged["caps_by_model"] = {model: cache.get(model).to_dict() for model in models if cache.get(model)}
        capability = cache.get(str(merged.get("model") or ""))
        merged["model_caps"] = capability.to_dict() if capability else dict(merged.get("model_caps") or {})
        config = UiRunConfig(**{field.name: copy.deepcopy(merged.get(field.name)) for field in fields(UiRunConfig)})
        logger = RunLogger(self.context.settings.log_dir, run_id, project_name=config.project)
        if live:
            from kajovo.core.safe_config import safe_ui_state
            inherit_pending(adapter, logger, live, ui_state=safe_ui_state(config))
        if preview.relation in {"continue", "repair"} and config.mode in {"GENERATE", "MODIFY"}:
            current = read_state(adapter.root)
            if current.get("manual_resource_bindings") or current.get("staged_files"):
                from kajovo.core.orchestration.manual_resources import inherit_resources
                inherit_resources(adapter.root, logger)
        logger.record_lineage(
            preview.source_run_id, preview.relation,
            source_checkpoint_id=preview.checkpoint_id,
            inherited_configuration={
                "mode": config.mode,
                "maximum_quality": config.maximum_quality,
                "stop_after_plan": config.stop_after_plan,
                "dry_run": config.dry_run,
            },
            notes=merged["recovery_instruction"],
        )
        output = self._output_dir(config.mode, state, merged)
        return RunWorker(config, copy.deepcopy(self.context.settings), self.context.api_key, logger), output, config.project

    def _cascade_worker(self, run_id, adapter, state, preview, repair_instruction):
        definition_data = state.get("cascade_definition")
        if not isinstance(definition_data, dict):
            raise ValueError("Checkpoint kaskády nemá kanonickou definici kroků.")
        definition = CascadeDefinition.from_dict(definition_data)
        mappings = state.get("cascade_input_artifacts")
        start_step = state.get("next_step_id") or state.get("failed_step_id") or preview.selected_stage
        if start_step:
            definition.run_from_step_id = str(start_step)
        config = CascadeRunConfig(
            str(state.get("project") or ""), definition, str(state.get("in_dir") or ""),
            str(state.get("out_dir") or ""), run_id,
            resume_snapshot=copy.deepcopy(state.get("cascade_runtime")),
            resume_source_dir=str(adapter.root),
            input_bindings=copy.deepcopy(mappings) if isinstance(mappings, list) else None,
            recovery_instruction=repair_instruction,
            lineage={"source_run_id": preview.source_run_id, "relation_type": preview.relation,
                     "source_checkpoint_id": preview.checkpoint_id, "notes": repair_instruction},
        )
        output = Path(config.out_dir).resolve() if config.out_dir else None
        return CascadeRunWorker(config, copy.deepcopy(self.context.settings), self.context.api_key), output, config.project
