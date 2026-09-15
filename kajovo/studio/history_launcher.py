"""Jediná služba pro přímé pokračování, rerun a repair z Historie."""

from __future__ import annotations

import copy
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable

from kajovo.core.batch_completion import pending_batch_ids, read_state
from kajovo.core.cascade_pipeline import CascadeRunConfig, CascadeRunWorker
from kajovo.core.cascade_types import CascadeDefinition
from kajovo.core.delivery_preparation import validate_preparation_snapshot
from kajovo.core.model_capabilities import ModelCapabilitiesCache
from kajovo.core.pipeline import RunWorker, UiRunConfig
from kajovo.core.runlog import RunLogger, verified_output_evidence
from kajovo.core.utils import new_run_id


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
        return selected_stage or str(state.get("next_step_id") or "První krok kaskády")
    return mode or "Není evidováno"


class HistoryBranchLauncher:
    def __init__(self, context, refreshed: Callable[[], None] | None = None):
        self.context = context
        self.refreshed = refreshed or (lambda: None)
        self._launched: set[tuple[str, str, str]] = set()

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
        if pending_batch_ids(read_state(adapter.root)):
            raise ValueError("Zdrojový běh již odeslal BATCH; dokončete jej v původním běhu.")
        ui = state.get("ui_state") if isinstance(state.get("ui_state"), dict) else {}
        mode = str(ui.get("mode") or state.get("mode") or adapter.run_record().get("mode") or "")
        if mode not in SUPPORTED_DIRECT_MODES:
            raise ValueError("Tento typ běhu nemá bezpečný přímý launcher; použijte jeho doménovou obrazovku.")
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
        error = str(state.get("human_error") or state.get("error") or adapter.run_record().get("output_summary") or "Není evidováno")
        technical_error = str(state.get("technical_error") or state.get("error") or "Není evidováno")
        return BranchPreview(
            relation, adapter.run_id, checkpoint_id, str(checkpoint.get("checkpoint_type") or ""),
            selected_stage, tuple(inherited), skipped,
            first_paid_operation(mode, checkpoint, selected_stage), error, technical_error,
            "Zdrojový běh zůstane neměnný; vznikne nový Run ID a nová lineage větev.",
        )

    def launch(self, adapter, preview: BranchPreview, repair_instruction: str = ""):
        key = (preview.source_run_id, preview.checkpoint_id, preview.relation)
        if key in self._launched:
            raise ValueError("Tato potvrzovací akce již byla spuštěna; její druhý submit je zablokován.")
        if not self.context.api_key:
            raise ValueError("Přímé spuštění vyžaduje uložený přístupový klíč.")
        checkpoint = adapter.bundle.validate_checkpoint(preview.checkpoint_id)
        state = copy.deepcopy(checkpoint["state_snapshot"])
        ui = copy.deepcopy(state.get("ui_state") or {})
        mode = str(ui.get("mode") or state.get("mode") or adapter.run_record().get("mode") or "")
        if pending_batch_ids(read_state(adapter.root)):
            raise ValueError("BATCH již byl odeslán; nový submit je zablokován.")
        if mode == "KASKADA":
            reserved_output = Path(str(state.get("out_dir"))).resolve() if state.get("out_dir") else None
        else:
            reserved_output = (Path(str(ui.get("out_dir"))).resolve()
                               if ui.get("out_dir") and not ui.get("send_as_c") and mode != "QA" else None)
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
            record = self.context.operations.adopt(
                f"{preview.relation.upper()} · {project}", worker,
                receive=lambda value: self.refreshed(), identifier=run_id, output_dir=output_dir,
            )
            record.worker.finished.connect(self.refreshed)
            return record
        except Exception:
            self._launched.discard(key)
            raise

    def _standard_worker(self, run_id, adapter, ui, state, preview, repair_instruction):
        from .workbench import default_state

        merged = {**default_state(self.context.settings), **ui}
        merged["recovery_instruction"] = repair_instruction
        merged["source_checkpoint_id"] = preview.checkpoint_id
        snapshot = state.get("preparation_snapshot")
        if snapshot and merged.get("mode") in {"GENERATE", "MODIFY"}:
            merged["preparation_snapshot"] = validate_preparation_snapshot(
                snapshot, merged["mode"], bool(merged.get("maximum_quality"))
            )
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
        logger.record_lineage(
            preview.source_run_id, preview.relation,
            source_checkpoint_id=preview.checkpoint_id,
            inherited_configuration={"mode": config.mode, "maximum_quality": config.maximum_quality},
            notes=merged["recovery_instruction"],
        )
        output = Path(config.out_dir).resolve() if config.out_dir and not config.send_as_c and config.mode != "QA" else None
        return RunWorker(config, copy.deepcopy(self.context.settings), self.context.api_key, logger), output, config.project

    def _cascade_worker(self, run_id, adapter, state, preview, repair_instruction):
        definition_data = state.get("cascade_definition")
        if not isinstance(definition_data, dict):
            raise ValueError("Checkpoint kaskády nemá kanonickou definici kroků.")
        definition = CascadeDefinition.from_dict(definition_data)
        mappings = state.get("cascade_input_artifacts")
        if isinstance(mappings, list):
            by_step = {step.id: step for step in definition.steps}
            for mapping in mappings:
                if not isinstance(mapping, dict) or not mapping.get("path_in_bundle"):
                    continue
                step = by_step.get(str(mapping.get("step_id") or ""))
                if not step:
                    continue
                source = str((Path(adapter.root) / str(mapping["path_in_bundle"])).resolve())
                if mapping.get("field") == "input":
                    item = next((row for row in step.inputs if row.id == mapping.get("input_id")), None)
                    if item:
                        item.value = source
                elif mapping.get("field") == "files_local_paths":
                    try:
                        index = int(str(mapping.get("input_id") or ""))
                    except ValueError:
                        continue
                    if 0 <= index < len(step.files_local_paths):
                        step.files_local_paths[index] = source
        start_step = state.get("next_step_id") or state.get("failed_step_id") or preview.selected_stage
        if start_step:
            definition.run_from_step_id = str(start_step)
        config = CascadeRunConfig(
            str(state.get("project") or ""), definition, str(state.get("in_dir") or ""),
            str(state.get("out_dir") or ""), run_id,
            resume_snapshot=copy.deepcopy(state.get("cascade_runtime")),
            recovery_instruction=repair_instruction,
            lineage={"source_run_id": preview.source_run_id, "relation_type": preview.relation,
                     "source_checkpoint_id": preview.checkpoint_id, "notes": repair_instruction},
        )
        output = Path(config.out_dir).resolve() if config.out_dir else None
        return CascadeRunWorker(config, copy.deepcopy(self.context.settings), self.context.api_key), output, config.project
