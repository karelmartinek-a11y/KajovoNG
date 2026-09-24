"""Živá příprava a obnovitelné kanonické podklady GENERATE a MODIFY."""

from __future__ import annotations

import copy
import os

import jsonschema

from .contracts import ContractError
from .generate_batch import digest, prepare_structure, validate_structure
from .requirements import (
    enriched_plan_format,
    enriched_structure_format,
    requirements_format,
    validate_plan,
    validate_requirements,
    validate_stage_schema,
    validate_traceability,
)
from .utils import safe_join_under_root, sha256_file


def validate_modify_sources(structure, root, items, completed_paths=()):
    """Ověří skutečné podklady B2/V3 proti zmrazenému IN."""
    sources = {item.rel_path: item for item in items}
    if structure.get("contract") == "IMPLEMENTATION_GRAPH_V3":
        files = list((structure.get("spine") or {}).get("files") or [])
        touched = [row for row in files if row.get("action") in {"add", "modify"}]
        preserved = [row for row in files if row.get("action") == "preserve"]
    else:
        touched = list(structure.get("touched_files", []) or [])
        preserved = list(structure.get("preserved_files", []) or [])

    for file in touched:
        path = file["path"]
        if path in completed_paths:
            continue
        target = safe_join_under_root(root, path)
        if file["action"] == "add" and os.path.lexists(target):
            raise ContractError(f"B3: přidávaný soubor již existuje v IN: {path}")
        if file["action"] == "modify":
            item = sources.get(path)
            if (
                item is None
                or not os.path.isfile(target)
                or not (item.uploadable or (
                    getattr(item, "inventory_only", False) is True and item.size == 0
                ))
                or sha256_file(target) != item.sha256
            ):
                raise ContractError(
                    f"B3: měněný soubor není dostupný v přesně zmrazeném IN: {path}"
                )

    for file in preserved:
        path = file["path"]
        item = sources.get(path)
        if item is None:
            raise ContractError(f"Zachovaný soubor není ve zmrazeném IN: {path}")
        target = safe_join_under_root(root, path)
        if not os.path.isfile(target) or sha256_file(target) != item.sha256:
            raise ContractError(f"IN se od skenu změnil: {path}")


def validate_preparation_snapshot(snapshot, mode, maximum_quality):
    """Ověří integritu a režim checkpointu před použitím jeho podkladů."""
    if not isinstance(snapshot, dict):
        raise ContractError("Chybí úplný snímek přípravy.")
    data = {key: value for key, value in snapshot.items() if key != "snapshot_hash"}
    if snapshot.get("snapshot_hash") != digest(data):
        raise ContractError("Snímek přípravy byl změněn nebo poškozen.")
    version = snapshot.get("version")
    if version not in {1, 2} or snapshot.get("mode") != mode:
        raise ContractError("Nepodporovaná verze nebo režim snímku přípravy.")
    if snapshot.get("maximum_quality") is not maximum_quality:
        raise ContractError("ReRun musí zachovat původní Maximum Quality.")

    if version == 2:
        stage = str(snapshot.get("canonical_stage") or "")
        prefix = "A" if mode == "GENERATE" else "B"
        known = (
            stage in {
                prefix + "0R",
                prefix + "1",
                prefix + "2_SPINE",
                prefix + "2",
                prefix + "2Q",
            }
            or stage.startswith(prefix + "2_DETAIL_")
        )
        if not known or not isinstance(snapshot.get("source_snapshot_hash"), str):
            raise ContractError(
                "Snímek V2 neobsahuje známou dokončenou fázi nebo SourcePack hash."
            )
        graph = snapshot.get("graph")
        if stage in {prefix + "2", prefix + "2Q"}:
            if not isinstance(graph, dict):
                raise ContractError("Snímek V2 nemá kanonický IMPLEMENTATION_GRAPH_V3.")
            from .orchestration.preparation import GRAPH_SCHEMA
            try:
                jsonschema.Draft202012Validator(GRAPH_SCHEMA).validate(graph)
            except jsonschema.ValidationError as exc:
                raise ContractError(
                    f"Neplatný IMPLEMENTATION_GRAPH_V3 v checkpointu: {exc.message}"
                ) from exc
            if (
                graph.get("mode") != mode
                or graph.get("source_snapshot_hash")
                != snapshot.get("source_snapshot_hash")
            ):
                raise ContractError(
                    "IMPLEMENTATION_GRAPH_V3 neodpovídá režimu nebo SourcePacku checkpointu."
                )
        if stage == prefix + "2Q" and not maximum_quality:
            raise ContractError("Standard nesmí obnovit Maximum Quality checkpoint.")
        return copy.deepcopy(snapshot)

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
    del previous_id
    worker._preparation_runtime_inputs = {
        "text": input_text if isinstance(input_text, dict) else {"supplied_context": str(input_text or "")},
        "file_ids": list(input_files or []),
        "image_ids": list(input_images or []),
    }
    from .orchestration.preparation import prepare_delivery_v2

    return prepare_delivery_v2(worker, client, mode, tools=tools)
