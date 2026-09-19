"""Živá příprava a obnovitelné kanonické podklady GENERATE a MODIFY."""

from __future__ import annotations

import copy
import json
import os

import jsonschema

from .contracts import ContractError, extract_text_from_response, parse_json_strict
from .generate_batch import digest, prepare_structure, validate_structure
from .requirements import (
    apply_quality, enriched_plan_format, enriched_structure_format,
    requirements_format, stage_instructions, validate_traceability,
    validate_requirements, validate_plan, validate_stage_schema, bound_reference_format,
)
from .utils import safe_join_under_root, sha256_file
from .context_budget import preparation_measurement
from .structured_output import prepare_payload


def validate_modify_sources(structure, root, items, completed_paths=()):
    """Ověří skutečné podklady B2 i pro výsledek bez změn a zachované vazby."""
    sources = {item.rel_path: item for item in items}
    for file in structure.get("touched_files", []):
        path = file["path"]
        if path in completed_paths:
            continue
        target = safe_join_under_root(root, path)
        if file["action"] == "add" and os.path.lexists(target):
            raise ContractError(f"B3: přidávaný soubor již existuje v IN: {path}")
        if file["action"] == "modify":
            item = sources.get(path)
            if item is None or not os.path.isfile(target) or (
                file.get("kind") != "binary" and not item.uploadable
            ):
                raise ContractError(f"B3: měněný soubor není dostupný ve schváleném IN: {path}")
    for file in structure.get("preserved_files", []):
        path = file["path"]
        item = sources.get(path)
        if item is None or not item.uploadable:
            raise ContractError(f"Zachovaný soubor není dostupný ve schváleném IN: {path}")
        if sha256_file(safe_join_under_root(root, path)) != item.sha256:
            raise ContractError(f"IN se od skenu změnil: {path}")


def validate_preparation_snapshot(snapshot, mode, maximum_quality):
    """Ověří integritu a režim checkpointu před použitím jeho podkladů."""
    if not isinstance(snapshot, dict):
        raise ContractError("Chybí úplný snímek přípravy.")
    data = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
    if snapshot.get("snapshot_hash") != digest(data):
        raise ContractError("Snímek přípravy byl změněn nebo poškozen.")
    if snapshot.get("version") != 1 or snapshot.get("mode") != mode:
        raise ContractError("Nepodporovaná verze nebo režim snímku přípravy.")
    if snapshot.get("maximum_quality") is not maximum_quality:
        raise ContractError("ReRun musí zachovat původní Maximum Quality.")
    prefix = "A" if mode == "GENERATE" else "B"
    stages = [prefix + suffix for suffix in ("0R", "1", "2", "2Q")]
    if snapshot.get("canonical_stage") not in stages or not snapshot.get("response_id"):
        raise ContractError("Snímek neobsahuje známou dokončenou fázi a její odpověď.")
    index = stages.index(snapshot["canonical_stage"])
    if index == 3 and not maximum_quality:
        raise ContractError("Standard nesmí obnovit quality gate.")
    for key, fmt in (("requirements", requirements_format(mode)),
                     ("plan", enriched_plan_format(mode)),
                     ("structure", enriched_structure_format(mode, implementation="implementation" in (snapshot.get("structure") or {})))):
        if (key == "plan" and index < 1) or (key == "structure" and index < 2):
            continue
        try:
            jsonschema.validate(snapshot.get(key), fmt["format"]["schema"])
        except jsonschema.ValidationError as exc:
            raise ContractError(f"Neplatný snímek {key}: {exc.message}") from exc
    validate_requirements(snapshot["requirements"], mode)
    if index >= 1:
        validate_plan(snapshot["requirements"], snapshot["plan"], mode)
    if index >= 2:
        prepared, _ = validate_delivery_structure(snapshot["requirements"], snapshot["plan"], snapshot["structure"], mode)
        if prepared != snapshot["structure"]:
            raise ContractError("Uložená kanonická struktura nemá úplné závislosti.")
    return copy.deepcopy(snapshot)


def validate_delivery_structure(requirements, plan, structure, mode):
    """Připraví jednoznačné vazby bez změny uložené odpovědi modelu."""
    validate_stage_schema(structure, enriched_structure_format(mode, implementation="implementation" in structure),
                          ("A" if mode == "GENERATE" else "B") + "2")
    prepared = copy.deepcopy(structure)
    additions = []
    if mode == "GENERATE":
        base = copy.deepcopy(structure)
        base.pop("implementation", None)
        for file in base["files"]:
            file.pop("requirement_ids", None)
            file.pop("architecture_item_ids", None)
        base, additions = prepare_structure(base)
        validate_structure(base)
        for target, source in zip(prepared["files"], base["files"], strict=True):
            target["dependencies"] = source["dependencies"]
    validate_traceability(requirements, plan, prepared)
    return prepared, additions


def prepare_delivery(
    worker,
    client,
    mode,
    previous_id,
    input_text,
    input_files,
    input_images,
    tools,
):
    """New runs use CHANGE V2 preparation; old readers stay for legacy evidence."""
    del previous_id, input_text, input_files, input_images
    from .orchestration.preparation import prepare_delivery_v2

    return prepare_delivery_v2(worker, client, mode, tools=tools)
