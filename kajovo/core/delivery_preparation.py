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
)
from .utils import safe_join_under_root, sha256_file


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
    if index >= 2:
        prepared, _ = validate_delivery_structure(snapshot["requirements"], snapshot["plan"], snapshot["structure"], mode)
        if prepared != snapshot["structure"]:
            raise ContractError("Uložená kanonická struktura nemá úplné závislosti.")
    return copy.deepcopy(snapshot)


def validate_delivery_structure(requirements, plan, structure, mode):
    """Připraví jednoznačné vazby bez změny uložené odpovědi modelu."""
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


def prepare_delivery(worker, client, mode, previous_id, input_text, input_files, input_images, tools):
    """Provede pouze chybějící analytické fáze a vrátí kanonickou specifikaci."""
    prefix = "A" if mode == "GENERATE" else "B"
    quality = bool(worker.cfg.maximum_quality)
    stages = [prefix + "0R", prefix + "1", prefix + "2"]
    if quality:
        stages.append(prefix + "2Q")
    formats = [requirements_format(mode), enriched_plan_format(mode), enriched_structure_format(mode, implementation=True)]
    labels = (["Profesionální requirements", "Architektonický plán", "Implementační struktura"]
              if mode == "GENERATE" else ["Change requirements", "Plán změny", "Implementační struktura změny"])
    if quality:
        formats.append(enriched_structure_format(mode, implementation=True))
        labels.append("Quality gate")
    checkpoint = worker.cfg.preparation_snapshot
    if checkpoint:
        snapshot = validate_preparation_snapshot(checkpoint, mode, quality)
        if snapshot.get("prompt_hash") != digest(worker.cfg.prompt):
            raise ContractError("Zadání se liší od uložené přípravy; spusťte nový běh.")
        start = stages.index(snapshot["canonical_stage"]) + 1
        previous_id = snapshot["response_id"]
    else:
        start = 0
        snapshot = {"version": 1, "mode": mode, "maximum_quality": quality,
                    "prompt_hash": digest(worker.cfg.prompt), "requirements": None,
                    "plan": None, "structure": None}
    for index in range(start, len(stages)):
        stage, fmt = stages[index], formats[index]
        worker._check_stop()
        worker._set(10 + index * 8, 0, labels[index] + "…", stage=stage)
        model = worker._generate_model("A1" if index < 2 else "A2") if mode == "GENERATE" else worker.cfg.model
        context = {"source": input_text, "requirements": snapshot["requirements"],
                   "plan": snapshot["plan"], "structure": snapshot["structure"]}
        if worker.cfg.send_as_c:
            context["delivery"] = "Samostatné souborové úlohy bez historie; specifikace obsahuje všechny závěry příloh a diagnostiky."
        payload = worker._payload_base(
            model=model, instructions=stage_instructions(stage),
            input_parts=worker._input_parts(json.dumps(context, ensure_ascii=False),
                                            input_files if index == 0 or mode == "MODIFY" else [],
                                            input_images if index == 0 or mode == "MODIFY" else []),
            prev_id=previous_id,
            supports_temperature=worker._model_caps(model).get("supports_temperature", False),
        )
        payload["text"] = fmt
        if index >= 2:
            payload["instructions"] += (
                "\nImplementační kontrakt v1 je povinný. implementation.scopes určuje pro každou "
                "atomickou globální povinnost přesné cesty a důvod působnosti. Zdroj je JSON pointer "
                "/requirements/<pole>/<index>, /plan/<pole>/<index> nebo /structure/<pole>/<index>; "
                "u neprázdného objektu či skaláru bez indexu. Vynech contract, version, files, "
                "touched_files, preserved_files, interfaces, implementation, architecture_items "
                "a seznamy requirement objektů s id, které se vážou pomocí requirement_ids. "
                "Objekt plan.requirements je samostatná globální povinnost. "
                "Žádná povinnost nesmí zůstat bez vlastníka. U každého rozhraní definuj přesnou "
                "signaturu včetně typů/nullability, verzi, chybovou sémantiku a lifecycle. "
                "Consumer/provider používají stejný verzovaný kontrakt. U každého souboru "
                "vyjmenuj required_facets podle skutečných rizik a vyplň příslušné facets včetně "
                "zdrojových odkazů. Zachyť persistence, transakce, konkurenci, security a globální "
                "invarianty tam, kde platí. Akceptace a testovací scénáře musí být konkrétní. "
                "Neznámé kritické detaily označ jako critical unresolved_questions; nevymýšlej je. "
                "expected_output_tokens je odhad viditelného úplného souboru bez reasoning. "
                "Cykly implementuj proti přesným společným rozhraním, nikoli domnělému kódu."
            )
        apply_quality(payload, quality)
        if tools:
            payload["tools"] = tools
        for attempt in range(3 if index >= 2 else 1):
            worker._check_stop()
            worker.log.save_json("requests", f"{stage}_request_{attempt}", {"payload": payload, "ui_state": worker.cfg.__dict__})
            worker._log_api_action(stage, "send", {"contract": fmt["format"]["name"], "model": model})
            response = worker._create_response(client, payload)
            worker.log.save_json("responses", f"{stage}_response_{response.get('id', 'NOID')}", response)
            previous_id = str(response.get("id") or "")
            if not previous_id:
                raise ContractError(f"{stage}: odpověď nemá ID.")
            value = parse_json_strict(extract_text_from_response(response))
            worker._log_api_action(stage, "receive", {"response_id": previous_id, "contract": value.get("contract")})
            try:
                if index >= 2:
                    value, additions = validate_delivery_structure(snapshot["requirements"], snapshot["plan"], value, mode)
                    from .context_compiler import validate_implementation
                    validate_implementation({"requirements": snapshot["requirements"],
                                             "plan": snapshot["plan"], "structure": value})
                    worker.log.save_json("manifests", f"{stage}_prepared_candidate_{attempt}", {
                        "response_id": previous_id, "structure": value, "added_dependencies": additions})
                break
            except ContractError as exc:
                if attempt == 2:
                    raise
                payload["previous_response_id"] = previous_id
                repair_context = {**context, "structure": value, "validation_errors": str(exc)}
                payload["input"] = worker._input_parts(json.dumps(repair_context, ensure_ascii=False), [], [])
        snapshot[("requirements", "plan", "structure", "structure")[index]] = value
        snapshot.update(canonical_stage=stage, response_id=previous_id)
        snapshot.pop("snapshot_hash", None)
        snapshot["snapshot_hash"] = digest(snapshot)
        worker.cfg.preparation_snapshot = copy.deepcopy(snapshot)
        worker.log.update_state({"preparation_snapshot": snapshot, "maximum_quality": quality})
        worker.log.save_json("manifests", "preparation_snapshot", snapshot)
    worker._delivery_snapshot = copy.deepcopy(snapshot)
    return snapshot["plan"], snapshot["structure"], previous_id
