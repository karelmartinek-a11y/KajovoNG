"""Společná specifikace a samostatné souborové úlohy GENERATE a MODIFY BATCH."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .contracts import (
    ContractError,
    RemoteResponseError,
    parse_json_strict,
    validate_paths,
)
from .request_rules import uses_reasoning_defaults, validate_response_payload
from .structured_output import file_content_format, validate_output
from .orchestration.contracts import canonical_sha256
from .orchestration.work_order import freeze_order, validate_work_order_v2
from .utils import atomic_write_text, is_versing_snapshot_dir, safe_join_under_root
from .batch_submit import response_batch_submit_payload, submit_verified_batch
from .progress import ProgressEvent
from .run_bundle import RunBundle
from .context_compiler import ContextCompiler, canonical
from .context_limits import configure_file_request, measure_request, ensure_technical_limits


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def structure_format():
    text = {"type": "string"}
    strings = {"type": "array", "items": text}
    schema = object_schema({
        "contract": {"type": "string", "enum": ["A2_STRUCTURE"]},
        "version": {"type": "integer", "enum": [2]},
        "rules": strings,
        "packages": {"type": "array", "items": object_schema({"name": text, "version": text})},
        "interfaces": {"type": "array", "items": object_schema({"id": text, "definition": text})},
        "files": {"type": "array", "items": object_schema({
            "path": text, "purpose": text, "language": text,
            "kind": {"type": "string", "enum": ["text", "binary"]},
            "dependencies": {**strings, "description": "Pouze přesné cesty jiných souborů tohoto projektu z files[].path. Pro každé requires musí obsahovat cestu alespoň jednoho souboru, který dané id uvádí v provides, pokud rozhraní neposkytuje sám tento soubor. Žádné externí ani standardní knihovny."},
            "provides": {**strings, "description": "Pouze identifikátory z interfaces[].id poskytované tímto souborem."},
            "requires": {**strings, "description": "Pouze identifikátory z interfaces[].id využívané tímto souborem. Poskytovatel musí být uveden v dependencies, pokud nejde o vlastní provides. Žádné importy standardní knihovny."},
            "behavior": text,
        })},
    })
    return {"format": {"type": "json_schema", "name": "A2_STRUCTURE_V2", "strict": True, "schema": schema}}


def plan_format():
    text = {"type": "string"}
    strings = {"type": "array", "items": text}
    schema = object_schema({
        "contract": {"type": "string", "enum": ["A1_PLAN"]},
        "project": object_schema({k: text for k in ("name", "one_liner", "target_os", "language", "runtime")}),
        "assumptions": strings,
        "requirements": object_schema({k: strings for k in ("functional", "non_functional", "constraints")}),
        "architecture": object_schema({
            "modules": {"type": "array", "items": object_schema({"name": text, "responsibility": text})},
            "data_flow": strings, "error_handling": strings, "security_notes": strings}),
        "build_run": object_schema({k: strings for k in ("prerequisites", "commands", "verification")}),
        "deliverable_policy": object_schema({"max_lines_per_chunk": {"type": "integer"}}),
    })
    return {"format": {"type": "json_schema", "name": "A1_PLAN", "strict": True, "schema": schema}}


def _validate_structure_base(struct):
    """Ověří tvar a jednoznačnost identifikátorů před zpracováním vazeb."""
    import jsonschema
    try:
        jsonschema.validate({k: v for k, v in struct.items() if k != "implementation"},
                            structure_format()["format"]["schema"])
    except jsonschema.ValidationError as exc:
        raise ContractError(f"Neúplná specifikace A2: {exc.message}") from exc
    validate_paths(struct["files"])
    ids = [i["id"] for i in struct["interfaces"]]
    if len(set(ids)) != len(ids) or any(not i.strip() for i in ids):
        raise ContractError("Rozhraní A2 musí mít jedinečné neprázdné identifikátory.")
    if any(not i["definition"].strip() for i in struct["interfaces"]):
        raise ContractError("Rozhraní A2 vyžaduje přesnou definici.")
    if any(not p["name"].strip() or not p["version"].strip() for p in struct["packages"]):
        raise ContractError("Závislosti vyžadují jméno a verzi.")


def _interface_providers(struct):
    providers = {interface["id"]: set() for interface in struct["interfaces"]}
    for file in struct["files"]:
        for interface in file["provides"]:
            if interface in providers:
                providers[interface].add(file["path"])
    return providers


def prepare_structure(struct):
    """Vrátí kopii A2 a evidenci pouze jednoznačně odvoditelných závislostí.

    Neopravitelné vztahy ponechá přísnému validátoru. Uložené dávkové manifesty
    se touto přípravou nemění; patří pouze novým odpovědím A2.
    """
    _validate_structure_base(struct)
    prepared = copy.deepcopy(struct)
    providers = _interface_providers(prepared)
    additions = []
    for file in prepared["files"]:
        available_paths = {file["path"], *file["dependencies"]}
        for interface in file["requires"]:
            candidates = providers.get(interface, set())
            if candidates & available_paths or len(candidates) != 1:
                continue
            dependency = next(iter(candidates))
            file["dependencies"].append(dependency)
            available_paths.add(dependency)
            additions.append({"path": file["path"], "interface": interface, "dependency": dependency})
    return prepared, additions


def validate_structure(struct):
    """Odmítne neplatné A2 a oznámí všechny zjistitelné vztahové chyby najednou."""
    _validate_structure_base(struct)
    paths = {f["path"] for f in struct["files"]}
    providers = _interface_providers(struct)
    ids = set(providers)
    errors = []
    for file in struct["files"]:
        if not file["purpose"].strip() or not file["behavior"].strip():
            errors.append(f"Soubor {file['path']} vyžaduje účel a požadované chování.")
        if not set(file["dependencies"]) <= paths:
            errors.append(f"Soubor {file['path']}: neznámé závislosti {sorted(set(file['dependencies']) - paths)}; povolené cesty {sorted(paths)}. Standardní a externí knihovny sem nepatří.")
        unknown_ids = set(file["provides"] + file["requires"]) - ids
        if unknown_ids:
            errors.append(f"Soubor {file['path']}: neznámá rozhraní v provides/requires {sorted(unknown_ids)}; povolené identifikátory jsou {sorted(ids)}.")
        available_paths = {file["path"], *file["dependencies"]}
        for interface in sorted(set(file["requires"]) & ids):
            candidates = providers[interface]
            if not candidates:
                errors.append(f"Soubor {file['path']}: chybí poskytovatel vyžadovaného rozhraní {interface}; žádný soubor je neuvádí v provides.")
            elif not candidates & available_paths:
                errors.append(f"Soubor {file['path']}: rozhraní {interface} vyžaduje v dependencies alespoň jednoho z poskytovatelů {sorted(candidates)}.")
        if file["kind"] == "text" and Path(file["path"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".exe", ".dll", ".woff", ".woff2", ".ttf"}:
            errors.append(f"Binární prostředek musí mít kind=binary: {file['path']}")
    if errors:
        raise ContractError("Neplatná specifikace A2:\n" + "\n".join(errors))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_batch_model(model):
    validate_response_payload({"model": model}, batch=True)



def _build_manifest_v3(
    run_id,
    prompt,
    plan,
    structure,
    model,
    temperature,
    paths=None,
    *,
    requirements,
    maximum_quality=False,
    mode="GENERATE",
    originals=None,
    recovery_instruction="",
    run_config=None,
    expected_target_hashes=None,
    verified_artifacts=None,
    approved_paths=None,
    completed_targets=None,
):
    """Build exactly one ready production wave from IMPLEMENTATION_GRAPH_V3."""
    import jsonschema

    from .orchestration.preparation import GRAPH_SCHEMA
    from .orchestration.waves import build_execution_dag
    from .requirements import apply_quality, stage_instructions

    if mode not in {"GENERATE", "MODIFY"} or structure.get("mode") != mode:
        raise ContractError("IMPLEMENTATION_GRAPH_V3 neodpovídá workflow.")
    try:
        jsonschema.Draft202012Validator(GRAPH_SCHEMA).validate(structure)
    except jsonschema.ValidationError as exc:
        raise ContractError(f"Neplatný IMPLEMENTATION_GRAPH_V3: {exc.message}") from exc
    if not isinstance(requirements, dict) or not isinstance(plan, dict):
        raise ContractError("V3 BATCH vyžaduje kanonické requirements a plan.")

    validate_batch_model(model)
    originals = dict(originals or {})
    if any(not isinstance(value, str) for value in originals.values()):
        raise ContractError("Původní obsah musí být text.")
    verified_artifacts = copy.deepcopy(verified_artifacts or {})
    expected_target_hashes = dict(expected_target_hashes or {})

    snapshot = copy.deepcopy({
        "prompt": prompt,
        "plan": plan,
        "structure": structure,
        "requirements": requirements,
        "maximum_quality": maximum_quality,
        "original_hashes": {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in originals.items()
        },
    })
    dag = build_execution_dag(structure)
    verified_targets = {
        path
        for path, artifact in verified_artifacts.items()
        if isinstance(artifact, dict)
        and artifact.get("validation_status") == "verified"
    }
    completed_targets = set(completed_targets or ()) | verified_targets
    production_actions = (
        {"generate"} if mode == "GENERATE" else {"add", "modify"}
    )
    production = {
        row["path"]
        for row in structure["spine"]["files"]
        if row["kind"] == "text" and row["action"] in production_actions
    }
    requested = set(paths) if paths is not None else set(production)
    approved_scope = (
        set(approved_paths) if approved_paths is not None else set(requested)
    )
    if not requested <= production or not approved_scope <= production:
        raise ContractError(
            "BATCH výběr obsahuje neprodukční nebo neznámé cesty: "
            + str(sorted((requested | approved_scope) - production))
        )
    if not requested <= approved_scope:
        raise ContractError(
            "BATCH wave se pokouší rozšířit zmrazený schválený scope."
        )
    missing_expectations = approved_scope - expected_target_hashes.keys()
    if missing_expectations:
        raise ContractError(
            "Schválený BATCH scope nemá původní stav cíle: "
            + str(sorted(missing_expectations))
        )
    snapshot["expected_target_hashes"] = {
        path: expected_target_hashes[path] for path in sorted(approved_scope)
    }
    compiler = ContextCompiler(snapshot)

    wave_index = {
        path: index
        for index, wave in enumerate(dag.waves)
        for path in wave
    }
    # Normal continuation excludes already verified targets. An explicit repair
    # selects only already verified targets and is therefore allowed to rebuild
    # exactly those targets without reopening providers or siblings.
    pending_requested = requested - verified_targets
    candidates = pending_requested if pending_requested else requested
    eligible = {
        path
        for path in candidates
        if set(dag.content_dependencies.get(path, ())) <= completed_targets
    }
    if not eligible:
        remaining_content = {
            path: list(dag.content_dependencies.get(path, ()))
            for path in sorted(candidates)
        }
        raise ContractError(
            "BATCH nemá připravenou dependency-wave; chybí verified content evidence: "
            + json.dumps(remaining_content, ensure_ascii=False, sort_keys=True)
        )
    first_wave = min(wave_index[path] for path in eligible)
    selected_paths = sorted(
        path for path in eligible if wave_index[path] == first_wave
    )
    blocked_requested = sorted(
        path
        for path in requested
        if path not in selected_paths and path not in verified_targets
    )

    files_by_path = {row["path"]: row for row in structure["spine"]["files"]}
    selected = [files_by_path[path] for path in selected_paths]
    if len(selected) > 50_000:
        raise ContractError("Dávka překračuje 50 000 souborů.")

    stage = "B3_FILE" if mode == "MODIFY" else "A3_FILE"
    rows, reports, work_orders = [], [], []
    task_indices = {path: index for index, path in enumerate(sorted(production))}
    for file in selected:
        index = task_indices[file["path"]]
        if mode == "MODIFY" and file["action"] == "modify":
            if not isinstance(originals.get(file["path"]), str):
                raise ContractError(
                    f"Chybí úplný původní obsah souboru {file['path']}."
                )
        compiled = compiler.compile(
            file["path"],
            originals=originals,
            verified_artifacts=verified_artifacts,
        )
        context = {"file_context": compiled, "file": file}
        if recovery_instruction:
            context["recovery_instruction"] = str(recovery_instruction)
            current = verified_artifacts.get(file["path"])
            if isinstance(current, dict) and isinstance(current.get("content"), str):
                context["repair_current_artifact"] = {
                    "content": current["content"],
                    "sha256": current.get("output_hash"),
                    "validation_status": current.get("validation_status"),
                }
        fmt = file_content_format()
        body = {
            "model": model,
            "store": False,
            "instructions": (
                stage_instructions(stage, batch=True)
                + "\nWire kontrakt FILE_CONTENT_V1: vrať pouze objekt s polem content. "
                "Cesta a akce jsou důvěryhodná metadata WorkOrderu."
            ),
            "input": canonical(context),
            "text": fmt,
        }
        if temperature is not None and not uses_reasoning_defaults(model):
            body["temperature"] = temperature
        routing = configure_file_request(
            body, compiled, maximum_quality=maximum_quality
        )
        apply_quality(body, maximum_quality)
        report = ensure_technical_limits(
            measure_request(body, compiled=compiled, batch=True)
        )
        validate_response_payload(body, batch=True)
        custom_id = f"{run_id}_{stage[:2]}_{index:05d}"
        rows.append({
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": body,
        })
        report.update(
            routing=routing, path=file["path"], custom_id=custom_id
        )
        reports.append(report)

        cfg_for_order = run_config
        if cfg_for_order is None:
            from types import SimpleNamespace
            cfg_for_order = SimpleNamespace(
                mode=mode,
                model=model,
                model_a1="",
                model_a2="",
                model_a3="",
                send_as_c=True,
                maximum_quality=maximum_quality,
                auto_repair="off",
                verification_profile_ids=[],
                stop_after_plan=False,
                dry_run=False,
                execution_approval_id=f"user-start:{run_id}",
            )
        order = freeze_order(
            cfg_for_order,
            {
                "run_id": run_id,
                "step_id": "batch:" + custom_id,
                "task_id": custom_id,
                "stage": stage.split("_", 1)[0],
                "route": "responses_batch",
                "provider_endpoint": "/v1/batches",
                "target_id": file["path"],
                "target_path": file["path"],
                "expected_target_hash": expected_target_hashes.get(file["path"]),
                "contract_name": "FILE_CONTENT_V1",
                "schema": fmt["format"]["schema"],
                "prompt": body["instructions"] + "\n" + body["input"],
                "model": model,
                "model_capability": {},
                "source_snapshot": {
                    "graph_hash": digest(structure),
                    "requirements_hash": digest(requirements),
                    "plan_hash": digest(plan),
                },
                "attempt_no": 1,
                "approval_id": (
                    getattr(cfg_for_order, "execution_approval_id", "")
                    or f"user-start:{run_id}"
                ),
            },
            compiled,
        )
        work_orders.append({**order.to_dict(), "order_hash": order.order_hash})

    selected_set = set(selected_paths)
    deferred = sorted(approved_scope - selected_set - verified_targets)
    manifest = {
        "version": 3,
        "graph_version": 3,
        "wave_no": first_wave,
        "mode": mode,
        "snapshot": snapshot,
        "snapshot_hash": digest(snapshot),
        "requests": rows,
        "work_orders": {row["task_id"]: row for row in work_orders},
        "context_reports": reports,
        "dependency_waves": {
            "waves": [list(wave) for wave in dag.waves],
            "content_dependencies": {
                key: list(value)
                for key, value in dag.content_dependencies.items()
            },
        },
        "wave_paths": selected_paths,
        "approved_paths": sorted(approved_scope),
        "excluded_paths": sorted(production - approved_scope),
        "deferred_paths": deferred,
        "blocked_requested_paths": blocked_requested,
        "completed_dependency_targets": sorted(completed_targets),
        "verified_dependency_artifacts": {
            path: verified_artifacts[path]
            for path in sorted(verified_targets)
            if path in verified_artifacts
        },
        "expected": {
            row["custom_id"]: file["path"]
            for row, file in zip(rows, selected, strict=True)
        },
        "omitted": [],
        "resource_targets": sorted(
            row["path"]
            for row in structure["spine"]["files"]
            if row["action"] in production_actions and row["kind"] != "text"
        ),
    }
    if recovery_instruction:
        manifest["recovery_instruction"] = str(recovery_instruction)
    encode_requests(manifest)
    return manifest


def build_manifest(run_id, prompt, plan, structure, model, temperature, paths=None, *,
                   requirements=None, maximum_quality=False, mode="GENERATE", originals=None,
                   recovery_instruction="", run_config=None, expected_target_hashes=None,
                   verified_artifacts=None, approved_paths=None,
                   completed_targets=None):
    if structure.get("contract") == "IMPLEMENTATION_GRAPH_V3":
        return _build_manifest_v3(
            run_id,
            prompt,
            plan,
            structure,
            model,
            temperature,
            paths,
            requirements=requirements,
            maximum_quality=maximum_quality,
            mode=mode,
            originals=originals,
            recovery_instruction=recovery_instruction,
            run_config=run_config,
            expected_target_hashes=expected_target_hashes,
            verified_artifacts=verified_artifacts,
            approved_paths=approved_paths,
            completed_targets=completed_targets,
        )

    from .requirements import apply_quality, stage_instructions, validate_traceability

    if mode not in {"GENERATE", "MODIFY"}:
        raise ContractError("Neznámý režim souborové dávky.")
    modifying = mode == "MODIFY"
    stage = "B3_FILE" if modifying else "A3_FILE"
    files = structure.get("touched_files" if modifying else "files")
    if modifying:
        if structure.get("contract") != "B2_STRUCTURE" or not isinstance(files, list):
            raise ContractError("MODIFY vyžaduje specifikaci B2_STRUCTURE.")
        # Stejné kontroly rozhraní a cest jako A2, navíc přesná akce změny.
        properties = structure_format()["format"]["schema"]["properties"]["files"]["items"]["properties"]
        normalized = {
            "contract": "A2_STRUCTURE", "version": 2,
            "rules": structure.get("rules", []), "packages": structure.get("packages", []),
            "interfaces": structure.get("interfaces", []),
            "files": [{k: f[k] for k in properties if k in f} for f in files],
        }
        for preserved in structure.get("preserved_files", []):
            normalized["files"].append({
                "path": preserved["path"], "provides": preserved["provides"],
                "purpose": "Zachované rozhraní projektu", "behavior": "Beze změny",
                "language": "", "kind": "binary", "requires": [], "dependencies": [],
            })
        validate_structure(normalized)
        if any(f.get("action") not in {"add", "modify"} for f in files):
            raise ContractError("MODIFY podporuje pouze akce add a modify.")
    else:
        normalized = copy.deepcopy(structure)
        normalized.pop("implementation", None)
        for file in normalized.get("files", []):
            file.pop("requirement_ids", None)
            file.pop("architecture_item_ids", None)
        validate_structure(normalized)
    if requirements is not None:
        validate_traceability(requirements, plan, structure)
    validate_batch_model(model)
    snapshot = copy.deepcopy({"prompt": prompt, "plan": plan, "structure": structure,
                              "requirements": requirements, "maximum_quality": maximum_quality})
    originals = originals or {}
    if any(not isinstance(value, str) for value in originals.values()):
        raise ContractError("Původní obsah musí být text.")
    snapshot["original_hashes"] = {path: hashlib.sha256(content.encode("utf-8")).hexdigest()
                                   for path, content in originals.items()}
    compiler = ContextCompiler(snapshot)
    selected = [f for f in files if f["kind"] == "text" and (paths is None or f["path"] in paths)]
    if not selected:
        raise ContractError("Manifest neobsahuje žádné vybrané textové soubory.")
    if len(selected) > 50_000:
        raise ContractError("Dávka překračuje 50 000 souborů.")
    rows, reports, work_orders = [], [], []
    expected_target_hashes = dict(expected_target_hashes or {})
    for index, file in enumerate(selected):
        action = file["action"] if modifying else None
        if modifying and action == "modify" and not isinstance(originals.get(file["path"]), str):
            raise ContractError(f"Chybí úplný původní obsah souboru {file['path']}.")
        compiled = compiler.compile(file["path"], originals=originals)
        context = {"file_context": compiled, "file": file}
        if recovery_instruction:
            context["recovery_instruction"] = str(recovery_instruction)
        fmt = file_content_format()
        body = {
            "model": model,
            "store": False,
            "instructions": (
                stage_instructions(stage, batch=True)
                + "\nWire kontrakt FILE_CONTENT_V1: vrať pouze objekt s polem content. "
                  "Cesta a akce jsou důvěryhodná metadata WorkOrderu, nikoli součást odpovědi."
            ),
            "input": canonical(context),
        }
        body["text"] = fmt
        if temperature is not None and not uses_reasoning_defaults(model):
            body["temperature"] = temperature
        routing = configure_file_request(body, compiled, maximum_quality=maximum_quality)
        apply_quality(body, maximum_quality)
        report = ensure_technical_limits(measure_request(body, compiled=compiled, batch=True))
        legacy_context = {"specification": snapshot, "file": file}
        if modifying:
            legacy_context["original_content"] = originals.get(file["path"], "")
            legacy_context["originals"] = {p: originals[p] for p in [file["path"], *file["dependencies"]] if p in originals}
        legacy = measure_request({**body, "input": json.dumps(legacy_context, ensure_ascii=False)}, batch=True)
        report["legacy_estimated_input_tokens"] = legacy["input_tokens"]
        report["saved_estimated_input_tokens"] = legacy["input_tokens"] - report["input_tokens"]
        validate_response_payload(body)
        custom_id = f"{run_id}_{stage[:2]}_{index:05d}"
        rows.append({"custom_id": custom_id, "method": "POST", "url": "/v1/responses", "body": body})
        report.update(routing=routing, path=file["path"], custom_id=custom_id)
        reports.append(report)
        cfg_for_order = run_config
        if cfg_for_order is None:
            from types import SimpleNamespace
            cfg_for_order = SimpleNamespace(
                mode=mode, model=model, model_a1="", model_a2="", model_a3="",
                send_as_c=True, maximum_quality=maximum_quality, auto_repair="off",
                verification_profile_ids=[], stop_after_plan=False, dry_run=False,
                execution_approval_id=f"user-start:{run_id}",
            )
        order = freeze_order(
            cfg_for_order,
            {
                "run_id": run_id,
                "step_id": "batch:" + custom_id,
                "task_id": custom_id,
                "stage": stage.split("_", 1)[0],
                "route": "responses_batch",
                "provider_endpoint": "/v1/batches",
                "target_id": file["path"],
                "target_path": file["path"],
                "expected_target_hash": expected_target_hashes.get(file["path"]),
                "contract_name": "FILE_CONTENT_V1",
                "schema": fmt["format"]["schema"],
                "prompt": body["instructions"] + "\n" + body["input"],
                "model": model,
                "model_capability": {},
                "source_snapshot": snapshot,
                "attempt_no": 1,
                "approval_id": getattr(cfg_for_order, "execution_approval_id", "") or f"user-start:{run_id}",
            },
            compiled,
        )
        work_orders.append({**order.to_dict(), "order_hash": order.order_hash})
    manifest = {"version": 3, "mode": mode, "snapshot": snapshot, "snapshot_hash": digest(snapshot), "requests": rows,
                "work_orders": {row["task_id"]: row for row in work_orders},
                "context_reports": reports, "dependency_waves": compiler.graph,
                "expected": {row["custom_id"]: file["path"] for row, file in zip(rows, selected, strict=True)},
                "omitted": [f["path"] for f in files if f not in selected]}
    if recovery_instruction:
        manifest["recovery_instruction"] = str(recovery_instruction)
    encode_requests(manifest)
    return manifest


def encode_requests(manifest):
    if manifest.get("version", 1) not in {1, 2, 3} or manifest.get("mode", "GENERATE") not in {"GENERATE", "MODIFY"}:
        raise ContractError("Nepodporovaná verze nebo režim manifestu.")
    if digest(manifest["snapshot"]) != manifest["snapshot_hash"]:
        raise ContractError("Specifikace dávky byla změněna.")
    completed = manifest.get("completed_hashes", {})
    if not isinstance(completed, dict):
        raise ContractError("Důkazy dokončených souborů musí být objekt.")
    validate_paths([{"path": path} for path in completed])
    structure = manifest["snapshot"]["structure"]
    v3_graph = structure.get("contract") == "IMPLEMENTATION_GRAPH_V3"
    source_files = (
        list((structure.get("spine") or {}).get("files") or [])
        if v3_graph
        else list(
            structure.get(
                "touched_files" if manifest.get("mode") == "MODIFY" else "files",
                [],
            )
        )
    )
    paths = {file["path"] for file in source_files}
    if (not set(completed) <= paths or set(completed) & set(manifest["expected"].values())
            or set(completed) & set(manifest.get("omitted", []))):
        raise ContractError("Dokončené soubory neodpovídají specifikaci a výběru dávky.")
    if any(not isinstance(value, str) or len(value) != 64 or set(value) - set("0123456789abcdef")
           for value in completed.values()):
        raise ContractError("Dokončený soubor vyžaduje SHA-256 důkaz zápisu.")
    rows = manifest["requests"]
    if not 0 < len(rows) <= 50_000:
        raise ContractError("Dávka vyžaduje 1 až 50 000 položek.")
    if len({r["custom_id"] for r in rows}) != len(rows):
        raise ContractError("Duplicitní ID úlohy.")
    if len({r["body"]["model"] for r in rows}) != 1:
        raise ContractError("Souborová dávka vyžaduje jediný model.")
    if set(manifest["expected"]) != {r["custom_id"] for r in rows}:
        raise ContractError("Mapování úloh neodpovídá požadavkům.")
    validate_paths([{"path": p} for p in manifest["expected"].values()])
    compiler = ContextCompiler(manifest["snapshot"]) if manifest.get("version") == 3 else None
    for row in rows:
        validate_response_payload(row["body"], batch=True if v3_graph else False)
        if row["method"] != "POST" or row["url"] != "/v1/responses" or row["body"].get("previous_response_id"):
            raise ContractError("Souborová úloha musí být samostatný požadavek Responses.")
        context = parse_json_strict(row["body"]["input"])
        if manifest.get("version") == 3:
            compiled = context.get("file_context", {})
            original_sources = {s["path"]: s["content"] for s in
                                compiled.get("working_context", {}).get("relevant_source_excerpts", [])}
            if any(hashlib.sha256(value.encode("utf-8")).hexdigest() !=
                   manifest["snapshot"].get("original_hashes", {}).get(path)
                   for path, value in original_sources.items()):
                raise ContractError("Původní obsah neodpovídá auditnímu snapshotu.")
            expected_context = compiler.compile(
                context["file"]["path"],
                originals=original_sources,
                verified_artifacts=manifest.get("verified_dependency_artifacts") or {},
            )
            if compiled != expected_context or "specification" in context or row["body"].get("tools"):
                raise ContractError("FileContext neodpovídá kanonické přípravě.")
            ensure_technical_limits(measure_request(row["body"], compiled=compiled, batch=True))
        elif digest(context["specification"]) != manifest["snapshot_hash"]:
            raise ContractError("Úloha neodpovídá společné specifikaci.")
        if context["file"]["path"] != manifest["expected"][row["custom_id"]]:
            raise ContractError("Úloha neodpovídá společné specifikaci nebo cílové cestě.")
        if manifest.get("version") in {2, 3}:
            modifying = manifest["mode"] == "MODIFY"
            files = (
                list(
                    (
                        manifest["snapshot"]["structure"].get("spine") or {}
                    ).get("files") or []
                )
                if v3_graph
                else manifest["snapshot"]["structure"][
                    "touched_files" if modifying else "files"
                ]
            )
            if context["file"] not in files:
                raise ContractError("Souborová úloha mění kanonickou specifikaci souboru.")
            if manifest.get("version") == 3:
                properties = row["body"]["text"]["format"]["schema"]["properties"]
                if set(properties) != {"content"} or row["body"]["text"]["format"].get("name") != "FILE_CONTENT_V1":
                    raise ContractError("V3 souborová úloha musí používat FILE_CONTENT_V1.")
                order = (manifest.get("work_orders") or {}).get(row["custom_id"])
                if not isinstance(order, dict):
                    raise ContractError("V3 souborová úloha nemá WORK_ORDER_V2.")
                validate_work_order_v2({k: v for k, v in order.items() if k != "order_hash"})
                if order.get("target_path") != context["file"]["path"] or order.get("contract_name") != "FILE_CONTENT_V1":
                    raise ContractError("WORK_ORDER_V2 neodpovídá cíli dávkové úlohy.")
            else:
                properties = row["body"]["text"]["format"]["schema"]["properties"]
                stage = "B3_FILE" if modifying else "A3_FILE"
                if properties["contract"].get("enum") != [stage] or properties["path"].get("enum") != [context["file"]["path"]]:
                    raise ContractError("Historické schéma úlohy neodpovídá režimu nebo cestě manifestu.")
                if modifying and (
                    not isinstance(context.get("original_content"), str)
                    or properties.get("action", {}).get("enum") != [context["file"]["action"]]
                ):
                    raise ContractError("Historická MODIFY úloha nemá původní obsah nebo správnou akci.")
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    if len(data) > 200_000_000:
        raise ContractError("JSONL překračuje 200 MB.")
    return data


def _snapshot_before_import(target):
    """Zachová obsah OUT před prvním zápisem, bez vnořených snapshotů."""
    root = Path(target).resolve()
    name = root.name + time.strftime("%d%m%Y%H%M%S")
    destination = root / name

    def ignore(_directory, names):
        return [entry for entry in names if entry in {"venv", ".venv", "LOG", name}
                or is_versing_snapshot_dir(entry, root.name)]

    shutil.copytree(root, destination, ignore=ignore, symlinks=True)
    return str(destination)


def import_results(manifest, raw_files, target, previous_hashes=None, overwrite_hashes=None, progress=None):
    """Nejdříve ověří celou dávku, potom bezpečně zapíše samostatné výsledky."""
    encode_requests(manifest)
    expected = manifest["expected"]
    completed_hashes = manifest.get("completed_hashes", {})
    validate_paths([{"path": path} for path in completed_hashes])
    completed_errors = {}
    for path, expected_hash in completed_hashes.items():
        try:
            dest = safe_join_under_root(target, path)
            if not expected_hash or hashlib.sha256(Path(dest).read_bytes()).hexdigest() != expected_hash:
                raise ContractError("Dokončený soubor se od přípravy změnil; zachován.")
        except (OSError, ValueError, ContractError) as exc:
            completed_errors[path] = str(exc)
    request_bodies = {row["custom_id"]: row["body"] for row in manifest["requests"]}
    validate_paths([{"path": p} for p in expected.values()])
    entries, errors, responses = {}, {}, []
    for raw in raw_files:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            item = parse_json_strict(line)
            cid = item.get("custom_id")
            if cid not in expected:
                raise ContractError(f"Neznámé ID výsledku: {cid}")
            if cid in entries:
                raise ContractError(f"Duplicitní ID výsledku: {cid}")
            entries[cid] = item
    contents, error_details = {}, {}
    for cid, path in expected.items():
        if cid in errors:
            continue
        item = entries.get(cid)
        if item is None:
            errors[cid] = "Chybí výsledek."
            continue
        response = item.get("response")
        response = response if isinstance(response, dict) else {}
        body = response.get("body")
        body = body if isinstance(body, dict) else {}
        if isinstance(body, dict) and body.get("id"):
            responses.append(body)
        try:
            if item.get("error") or response.get("status_code") != 200 or body.get("status") != "completed":
                raise RemoteResponseError(
                    body if body else {"error": item.get("error") or {}},
                    request_id=response.get("request_id") or "", status_code=response.get("status_code"),
                    custom_id=cid, path=path,
                )
            request_body = request_bodies[cid]
            if manifest.get("version") == 3:
                payload = validate_output(body, {"text": request_body.get("text") or file_content_format()})
                if not isinstance(payload.get("content"), str):
                    raise ContractError("FILE_CONTENT_V1 vyžaduje content:string.")
            else:
                from .contracts import file_response_format
                stage = "B3_FILE" if manifest.get("mode") == "MODIFY" else "A3_FILE"
                payload = validate_output(body, {"text": request_body.get("text") or file_response_format(stage, path, 0)})
                if payload.get("contract") != stage or payload.get("path") != path or not isinstance(payload.get("content"), str):
                    raise ContractError("Nesouhlasí historický souborový kontrakt nebo cesta.")
            contents[cid] = payload["content"]
            if manifest.get("version") != 3 and not payload["content"].strip():
                contents.pop(cid)
                raise ContractError("Prázdný historický výsledek nemá výslovné oprávnění; vyžaduje posouzení.")
            if manifest.get("version") == 3:
                context = parse_json_strict(request_body["input"])
                allow_empty = context["file_context"]["working_context"]["implementation_contract"]["allow_empty"]
                if not payload["content"].strip() and not allow_empty:
                    contents.pop(cid)
                    raise ContractError("Prázdný soubor odporuje implementačnímu kontraktu.")
        except (ContractError, RemoteResponseError, AttributeError) as exc:
            from dataclasses import asdict
            from .user_errors import describe_error
            errors[cid] = str(exc)
            error_details[cid] = {**asdict(describe_error(exc)), "custom_id": cid, "path": path,
                                  "evidence": exc.evidence() if hasattr(exc, "evidence") else {}}
    hashes, written = dict(previous_hashes or {}), []
    allowed = ({**manifest.get("overwrite_hashes", {}), **manifest.get("base_hashes", {}), **hashes}
               if overwrite_hashes is None else overwrite_hashes)
    items = list(contents.items())
    dry_run = bool(manifest.get("dry_run"))
    planned, snapshot_dir = [], None
    if progress:
        progress(ProgressEvent("Ukládání souborů", completed=0, total=len(items), unit="souborů"))
    for write_index, (cid, content) in enumerate(items, start=1):
        path = expected[cid]
        try:
            dest = safe_join_under_root(target, path)
            data = content.encode("utf-8")
            new_hash = hashlib.sha256(data).hexdigest()
            old_hash = None
            if os.path.exists(dest):
                old_hash = hashlib.sha256(Path(dest).read_bytes()).hexdigest()
                if old_hash != new_hash and old_hash != allowed.get(path):
                    raise ContractError("Existující soubor byl změněn nebo nepatří tomuto importu; zachován.")
            if dry_run:
                planned.append({"path": path, "content": content})
            else:
                if old_hash != new_hash:
                    if manifest.get("versing") and snapshot_dir is None and os.path.isdir(target):
                        snapshot_dir = _snapshot_before_import(target)
                        current_hash = hashlib.sha256(Path(dest).read_bytes()).hexdigest() if os.path.isfile(dest) else None
                        if current_hash != old_hash:
                            raise ContractError("Soubor se změnil během vytváření snapshotu; zachován.")
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    atomic_write_text(dest, content)
                if hashlib.sha256(Path(dest).read_bytes()).hexdigest() != new_hash:
                    raise ContractError("Zápis neodpovídá ověřenému obsahu souboru.")
                hashes[path] = new_hash
                written.append(path)
        except (OSError, ValueError, ContractError) as exc:
            errors[cid] = str(exc)
        if progress:
            progress(ProgressEvent("Ukládání souborů", completed=write_index, total=len(items), unit="souborů", detail=path))
    from dataclasses import asdict
    from .user_errors import describe_error
    for cid, message in errors.items():
        error_details.setdefault(cid, {**asdict(describe_error(ContractError(message))),
                                      "custom_id": cid, "path": expected[cid]})
    return {"written": written, "errors": errors, "hashes": hashes, "responses": responses,
            "error_details": error_details,
            "completed_errors": completed_errors,
            "dry_run": dry_run, "planned_files": planned, "snapshot_dir": snapshot_dir,
            "file_errors": {**completed_errors, **{expected[cid]: message for cid, message in errors.items()}},
            "omitted": manifest.get("omitted", []),
            "status": "partial" if errors or completed_errors or manifest.get("omitted") else
                      "dry_run" if dry_run else "files_complete_unverified"}



def _v3_verified_artifacts(run_dir, manifest, staged_files):
    """Reconstruct dependency artifacts only from immutable staged bytes."""
    compiler = ContextCompiler(manifest["snapshot"])
    root = Path(run_dir).resolve()
    artifacts = {}
    for row in staged_files:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        staged_path = str(row.get("staged_path") or "")
        if path not in compiler.files or not staged_path:
            continue
        source = (root / staged_path).resolve()
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ContractError(f"Staged dependency escapes Run Bundle: {path}") from exc
        if not source.is_file():
            continue
        data = source.read_bytes()
        digest_value = hashlib.sha256(data).hexdigest()
        if digest_value != row.get("sha256"):
            raise ContractError(f"Staged dependency hash mismatch: {path}")
        try:
            content = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        artifacts[path] = {
            "validation_status": "verified",
            "verification_level": "wire_and_artifact",
            "contract_hash": compiler.provider_hash(path),
            "output_hash": digest_value,
            "content": content,
        }
    return artifacts


def _v3_cfg_namespace(state):
    from types import SimpleNamespace

    cfg = dict(state.get("run_config_v2") or {})
    ui = dict(state.get("ui_state") or {})
    workflow = str(cfg.get("workflow") or ui.get("mode") or "GENERATE")
    return SimpleNamespace(
        mode=workflow,
        model=str(ui.get("model") or ""),
        model_a1=str(ui.get("model_a1") or ""),
        model_a2=str(ui.get("model_a2") or ""),
        model_a3=str(ui.get("model_a3") or ""),
        send_as_c=True,
        maximum_quality=bool(
            cfg.get("quality") == "maximum" or ui.get("maximum_quality")
        ),
        auto_repair=str(
            cfg.get("auto_repair") or ui.get("auto_repair") or "off"
        ),
        verification_profile_ids=list(
            cfg.get("verification_profile_ids")
            or ui.get("verification_profile_ids")
            or []
        ),
        stop_after_plan=bool(
            cfg.get("stop_after_plan", ui.get("stop_after_plan", False))
        ),
        dry_run=bool(cfg.get("dry_run", ui.get("dry_run", False))),
        execution_approval_id=str(
            (state.get("execution_authorization") or {}).get("approval_id")
            or ui.get("execution_approval_id")
            or ""
        ),
    )


def _prepare_v3_followup(run_dir, state, manifest, *, retry_source=None):
    """Zmrazí attempt/effect identity bez finančního rozhodování."""
    from dataclasses import replace

    from .context_limits import ensure_technical_limits
    from .orchestration.repository import OrchestrationRepository
    from .orchestration.work_order import (
        attempt_identity,
        work_order_from_mapping,
    )

    encode_requests(manifest)
    for candidate in (manifest, retry_source):
        if candidate is None:
            continue
        for custom_id, raw in candidate["work_orders"].items():
            order = work_order_from_mapping(raw)
            if "attempt_id" in raw and raw.get("order_hash") != order.order_hash:
                raise ContractError(
                    f"{custom_id}: nesouhlasí hash WORK_ORDER_V2."
                )

    repo = OrchestrationRepository(
        Path(run_dir).resolve().parent / "orchestration.sqlite3"
    )

    if retry_source is not None:
        source_orders = {
            value["target_path"]: value
            for value in retry_source["work_orders"].values()
        }
        renamed = {}
        orders_by_id = {}
        for request in manifest["requests"]:
            old_id = request["custom_id"]
            path = manifest["expected"][old_id]
            previous = source_orders[path]
            with repo.connect() as db:
                attempts = db.execute(
                    "SELECT w.attempt_no,p.state FROM work_orders w "
                    "LEFT JOIN provider_operations p "
                    "ON p.work_order_hash=w.work_order_hash "
                    "WHERE w.run_id=? AND w.task_id=?",
                    (previous["run_id"], previous["task_id"]),
                ).fetchall()
            if any(
                status in {"prepared", "submitted", "submission_unknown"}
                for _, status in attempts
            ):
                raise ContractError(
                    "Před opravou dokončete nebo dohledejte předchozí pokus úlohy."
                )
            attempt = max(
                [int(previous["attempt_no"]), *(int(number) for number, _ in attempts)]
            ) + 1
            if attempt > 3:
                raise ContractError(
                    "Úloha vyčerpala limit tří pokusů; automatické opravy jsou vyčerpány. "
                    "Další postup vyžaduje explicitní rozhodnutí uživatele."
                )
            custom_id = f"{previous['task_id']}_retry_{attempt}"
            raw_order = manifest["work_orders"][old_id]
            order = work_order_from_mapping(raw_order)
            order = replace(
                order,
                task_id=previous["task_id"],
                step_id="batch:" + custom_id,
                attempt_no=attempt,
                attempt_id=attempt_identity(
                    str(previous["run_id"]),
                    str(previous["task_id"]),
                    attempt,
                ),
            )
            request["custom_id"] = custom_id
            renamed[old_id] = custom_id
            orders_by_id[custom_id] = {
                **order.to_dict(),
                "order_hash": order.order_hash,
            }
        manifest["expected"] = {
            renamed[key]: path for key, path in manifest["expected"].items()
        }
        manifest["work_orders"] = orders_by_id
        for report in manifest["context_reports"]:
            report["custom_id"] = renamed[report["custom_id"]]
        encode_requests(manifest)

    reports = {
        str(row.get("custom_id")): row
        for row in manifest.get("context_reports", [])
    }
    orders = {}
    for request in manifest["requests"]:
        custom_id = str(request["custom_id"])
        raw_order = (manifest.get("work_orders") or {}).get(custom_id)
        if not isinstance(raw_order, dict):
            raise ContractError(f"{custom_id}: chybí WORK_ORDER_V2.")
        order = work_order_from_mapping(raw_order)
        if "attempt_id" in raw_order and raw_order.get("order_hash") != order.order_hash:
            raise ContractError(
                f"{custom_id}: nesouhlasí hash WORK_ORDER_V2."
            )
        report = reports.get(custom_id)
        if not isinstance(report, dict):
            raise ContractError(f"{custom_id}: chybí context report.")
        ensure_technical_limits(copy.deepcopy(report))
        body_hash = hashlib.sha256(
            json.dumps(
                request["body"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        import sqlite3

        try:
            persisted_hash = repo.register_work_order(
                order,
                body_ref=body_hash,
                input_hash=order.input_projection_hash,
            )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                "Pracovní pokus nelze zaregistrovat; ověřte stav "
                "souběžné operace před dalším odesláním."
            ) from exc
        repo.prepare_provider_operation(
            attempt_id=order.attempt_id,
            work_order_hash=persisted_hash,
            endpoint=order.provider_endpoint,
            request_hash=body_hash,
        )
        orders[custom_id] = order
    return repo, orders


def _submit_v3_followup_wave(
    client,
    run_dir,
    state,
    source_manifest,
    verified_artifacts,
    progress=None,
):
    """Build and submit exactly one next dependency wave under existing approval."""
    deferred = list(source_manifest.get("deferred_paths") or [])
    if not deferred:
        return None
    if state.get("submission_unknown") or state.get("pending_batch_submission"):
        raise ContractError("Neznámý submit musí být dohledán před novým odesláním.")

    ui = dict(state.get("ui_state") or {})
    cfg = _v3_cfg_namespace(state)
    originals = {}
    bundle = RunBundle(Path(run_dir))
    root = Path(run_dir).resolve()
    for artifact in bundle.artifacts():
        if artifact.get("role") != "in_project_file":
            continue
        metadata = artifact.get("metadata") or {}
        rel = str(metadata.get("relative_path") or artifact.get("reconstruction_role") or "")
        path_in_bundle = artifact.get("path_in_bundle")
        if not rel or not isinstance(path_in_bundle, str):
            continue
        source = (root / path_in_bundle).resolve()
        try:
            source.relative_to(root)
        except ValueError:
            continue
        if not source.is_file():
            continue
        try:
            originals[rel] = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

    model = str(source_manifest["requests"][0]["body"]["model"])
    temperature = source_manifest["requests"][0]["body"].get("temperature")
    expected_target_hashes = dict(source_manifest["snapshot"].get("expected_target_hashes") or {})
    if not set(deferred) <= expected_target_hashes.keys():
        raise ContractError("Další wave nemá zmrazené původní hashe cílových souborů.")

    next_manifest = build_manifest(
        Path(run_dir).name,
        str(ui.get("prompt") or source_manifest["snapshot"].get("prompt") or ""),
        source_manifest["snapshot"]["plan"],
        source_manifest["snapshot"]["structure"],
        model,
        temperature,
        deferred,
        requirements=source_manifest["snapshot"]["requirements"],
        maximum_quality=bool(source_manifest["snapshot"].get("maximum_quality")),
        mode=source_manifest["mode"],
        originals=originals,
        recovery_instruction=str(source_manifest.get("recovery_instruction") or ""),
        run_config=cfg,
        expected_target_hashes=expected_target_hashes,
        verified_artifacts=verified_artifacts,
        approved_paths=source_manifest.get("approved_paths") or deferred,
        completed_targets=(
            set(source_manifest.get("completed_dependency_targets") or [])
            | set(verified_artifacts)
            | set(state.get("resource_completed_paths") or [])
        ),
    )
    next_manifest["dry_run"] = bool(source_manifest.get("dry_run"))
    next_manifest["resource_completed_paths"] = list(
        state.get("resource_completed_paths")
        or source_manifest.get("resource_completed_paths")
        or []
    )
    next_manifest["resource_pending_paths"] = list(
        state.get("resource_pending_paths")
        or source_manifest.get("resource_pending_paths")
        or []
    )
    next_manifest["resource_expected_target_hashes"] = dict(
        source_manifest.get("resource_expected_target_hashes") or {}
    )
    next_manifest["excluded_scope"] = list(
        source_manifest.get("excluded_scope") or []
    )
    next_manifest["source_manifest_hash"] = digest(source_manifest)
    repo, orders = _prepare_v3_followup(run_dir, state, next_manifest)
    data = encode_requests(next_manifest)
    requests_dir = Path(run_dir) / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    request_path = requests_dir / (
        f"wave_{len(state.get('generate_batches') or {}) + 1}_{uuid.uuid4().hex[:8]}.jsonl"
    )
    request_path.write_bytes(data)
    for row in next_manifest["requests"]:
        client.validate_access(row["body"], batch=True)
    if progress:
        progress(
            ProgressEvent(
                "Příprava další dependency-wave",
                detail=f"Připravuji {len(next_manifest['requests'])} souborových úloh.",
            )
        )
    uploaded = client.upload_file(str(request_path), purpose="batch")
    input_file_id = str(uploaded.get("id") or "")
    if not input_file_id:
        raise ContractError("Upload další BATCH wave nemá Files ID.")

    from .orchestration.batch_manifest import (
        from_file_manifest,
        transition as transition_batch_manifest,
    )
    v4 = from_file_manifest(
        Path(run_dir).name,
        next_manifest,
        wave_no=int(source_manifest.get("wave_no") or 0) + 1,
    )
    v4 = transition_batch_manifest(
        v4, "input_uploaded", input_file_id=input_file_id
    )
    v4 = transition_batch_manifest(v4, "submitting")
    state.setdefault("batch_manifests_v4", {})[v4["manifest_id"]] = v4
    state["pending_batch_submission"] = {
        "input_file_id": input_file_id,
        "manifest": next_manifest,
        "manifest_v4_id": v4["manifest_id"],
    }
    state["submission_input_file_id"] = input_file_id
    state["submission_endpoint"] = "/v1/responses"
    state["submission_jsonl_sha256"] = hashlib.sha256(data).hexdigest()
    state["submission_unknown"] = True
    atomic_write_text(
        str(Path(run_dir) / "run_state.json"),
        json.dumps(state, ensure_ascii=False, indent=2),
    )

    physical_submit = response_batch_submit_payload(input_file_id)
    for order in orders.values():
        repo.bind_physical_request(
            order.attempt_id,
            physical_request_hash=canonical_sha256(physical_submit),
            remote_input_file_id=input_file_id,
        )
        repo.mark_submission_started(order.attempt_id)
    try:
        batch = submit_verified_batch(
            client, input_file_id, next_manifest["requests"]
        )
    except Exception as exc:
        definite_reject = (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None)
            in {400, 401, 403, 404, 422, 429}
        )
        if definite_reject:
            for order in orders.values():
                repo.mark_not_submitted(order.attempt_id)
            v4 = transition_batch_manifest(v4, "failed")
            state["submission_unknown"] = False
        else:
            for order in orders.values():
                repo.mark_submitted(
                    order.attempt_id, None, unknown=True
                )
            v4 = transition_batch_manifest(v4, "submission_unknown")
            state["status"] = "submission_unknown"
        state["batch_manifests_v4"][v4["manifest_id"]] = v4
        atomic_write_text(
            str(Path(run_dir) / "run_state.json"),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        raise

    batch_id = str(batch.get("id") or "")
    if not batch_id:
        for order in orders.values():
            repo.mark_submitted(
                order.attempt_id, None, unknown=True
            )
        v4 = transition_batch_manifest(v4, "submission_unknown")
        state["batch_manifests_v4"][v4["manifest_id"]] = v4
        state["status"] = "submission_unknown"
        atomic_write_text(
            str(Path(run_dir) / "run_state.json"),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        raise ContractError(
            "Další BATCH wave nemá potvrzené provider ID; resubmit je zablokován."
        )

    for order in orders.values():
        repo.mark_submitted(
            order.attempt_id, batch_id, unknown=False
        )
    v4 = transition_batch_manifest(
        v4, "submitted", provider_batch_id=batch_id
    )
    state["batch_manifests_v4"][v4["manifest_id"]] = v4
    state.setdefault("generate_batches", {})[batch_id] = next_manifest
    state.setdefault("batch_records", {})[batch_id] = batch
    state.pop("pending_batch_submission", None)
    state["submission_unknown"] = False
    state["status"] = "batch_pending"
    atomic_write_text(
        str(Path(run_dir) / "run_state.json"),
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    if progress:
        progress(
            ProgressEvent(
                "Další dependency-wave odeslána",
                detail=f"Batch {batch_id} · {len(next_manifest['requests'])} úloh.",
            )
        )
    return {"batch_id": batch_id, "manifest": next_manifest}



def _process_saved_batch_v3(
    client,
    run_dir,
    batch_id,
    state,
    manifest,
    batch,
    raw_files,
    batch_usage,
    settings,
    progress=None,
):
    """Import one V3 wave into immutable staging and advance the DAG."""
    from .orchestration.batch_manifest import transition as transition_batch_manifest
    from .orchestration.repository import OrchestrationRepository
    from .orchestration.verification import (
        candidate_verification_report,
        technical_staging_report,
    )
    from .orchestration.work_order import work_order_from_mapping

    run_root = Path(run_dir).resolve()
    repo = OrchestrationRepository(run_root.parent / "orchestration.sqlite3")

    # Provider usage se archivuje beze změny; opakovaný import je idempotentní.
    orders = {}
    for custom_id, raw_order in (manifest.get("work_orders") or {}).items():
        if isinstance(raw_order, dict):
            orders[str(custom_id)] = work_order_from_mapping(raw_order)
    for raw in raw_files:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            row = parse_json_strict(line)
            custom_id = str(row.get("custom_id") or "")
            order = orders.get(custom_id)
            response = row.get("response")
            response = response if isinstance(response, dict) else {}
            body = response.get("body")
            body = body if isinstance(body, dict) else {}
            provider_id = str(body.get("id") or "")
            usage = body.get("usage")
            if order is None or not provider_id or not isinstance(usage, dict):
                continue
            repo.record_usage(
                order.attempt_id,
                provider="openai",
                provider_item_id=provider_id,
                usage=usage,
                raw_response_ref="batch-item:" + provider_id,
            )

    staging_root = run_root / "staging" / "batch" / str(batch_id)
    generated_root = staging_root / "generated"
    generated_root.mkdir(parents=True, exist_ok=True)

    previous_import = (state.get("batch_imports") or {}).get(batch_id, {})
    import_manifest = copy.deepcopy(manifest)
    # Even MODIFY dry-run must materialize immutable staged bytes; only publish
    # is forbidden.
    import_manifest["dry_run"] = False
    result = import_results(
        import_manifest,
        raw_files,
        str(generated_root),
        previous_import.get("hashes"),
        previous_import.get("hashes"),
        progress=progress,
    )

    files_by_path = {
        row["path"]: row
        for row in manifest["snapshot"]["structure"]["spine"]["files"]
    }
    order_by_path = {
        order.target_path: order
        for order in orders.values()
        if order.target_path
    }
    staged = []
    for path in result.get("written") or []:
        source = Path(safe_join_under_root(str(generated_root), path))
        if not source.is_file():
            continue
        digest_value = hashlib.sha256(source.read_bytes()).hexdigest()
        staged.append({
            "path": path,
            "staged_path": source.relative_to(run_root).as_posix(),
            "bytes": source.stat().st_size,
            "sha256": digest_value,
            "expected_target_hash": (
                order_by_path[path].expected_target_hash
                if path in order_by_path
                else None
            ),
            "action": files_by_path.get(path, {}).get("action"),
            "purpose": files_by_path.get(path, {}).get("purpose", ""),
            "batch_id": batch_id,
        })

    verification = technical_staging_report(
        generated_root,
        target_id=f"{Path(run_dir).name}:batch:{batch_id}",
    )
    manifest_record = {
        "version": 2,
        "run_id": Path(run_dir).name,
        "mode": manifest["mode"],
        "batch_id": batch_id,
        "publication": (
            "blocked_dry_run"
            if manifest.get("dry_run")
            else "awaiting_verification_or_explicit_take"
        ),
        "staging_root": staging_root.relative_to(run_root).as_posix(),
        "files": staged,
    }
    atomic_write_text(
        str(staging_root / "manifest.json"),
        json.dumps(manifest_record, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        str(staging_root / "verification.json"),
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
    )

    merged = {
        str(row["path"]): row
        for row in state.get("staged_files", [])
        if isinstance(row, dict) and row.get("path")
    }
    merged.update({str(row["path"]): row for row in staged})
    state["staged_files"] = [merged[path] for path in sorted(merged)]
    state["generated_hashes"] = {
        path: row["sha256"] for path, row in merged.items()
    }
    state.setdefault("verification_reports", {})[batch_id] = verification
    state["verification_evidence"] = verification
    state["written_files"] = []
    if manifest.get("dry_run"):
        state["dry_run"] = True

    # Persist imported text bytes before any newly-ready image/resource submit.
    atomic_write_text(
        str(run_root / "run_state.json"),
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    from .orchestration.resource_delivery import advance_batch_resources
    state = advance_batch_resources(
        client, run_dir, state, manifest, settings
    )
    merged = {
        str(row["path"]): row
        for row in state.get("staged_files", [])
        if isinstance(row, dict) and row.get("path")
    }
    state["generated_hashes"] = {
        path: row["sha256"] for path, row in merged.items()
    }
    aggregate_verification = candidate_verification_report(
        run_root,
        state.get("staged_files", []),
        mode=str(manifest["mode"]),
        target_id=f"{Path(run_dir).name}:batch-candidate",
        profile_ids=list(
            (state.get("run_config_v2") or {}).get(
                "verification_profile_ids"
            )
            or []
        ),
    )
    state.setdefault("verification_reports", {})[
        f"{batch_id}:aggregate"
    ] = aggregate_verification
    state["verification_evidence"] = aggregate_verification
    aggregate_path = (
        run_root / "staging" / "verification_candidate"
        / "verification.json"
    )
    atomic_write_text(
        str(aggregate_path),
        json.dumps(
            aggregate_verification,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    result["published"] = False
    result["written"] = []
    result["staged_files"] = state.get("staged_files", [])
    result["verification"] = aggregate_verification
    result["dry_run"] = bool(manifest.get("dry_run"))
    result["usage"] = batch_usage

    import_errors = bool(
        result.get("errors")
        or result.get("completed_errors")
        or result.get("omitted")
    )
    result["import_status"] = (
        "partial" if import_errors else "files_complete_unverified"
    )
    state.setdefault("batch_records", {})[batch_id] = batch
    state.setdefault("batch_imports", {})[batch_id] = copy.deepcopy(result)

    # Advance canonical BATCH_MANIFEST_V4 evidence for this provider batch.
    candidates = []
    if isinstance(state.get("batch_manifest_v4"), dict):
        candidates.append(state["batch_manifest_v4"])
    candidates.extend(
        row
        for row in (state.get("batch_manifests_v4") or {}).values()
        if isinstance(row, dict)
    )
    current_v4 = next(
        (
            row
            for row in candidates
            if row.get("provider_batch_id") == batch_id
        ),
        None,
    )
    if current_v4 is not None:
        if current_v4.get("state") in {"submitted", "submission_unknown"}:
            current_v4 = transition_batch_manifest(
                current_v4, "remote_terminal"
            )
        final_v4_state = "partial" if import_errors else "imported"
        current_v4 = transition_batch_manifest(
            current_v4, final_v4_state
        )
        state.setdefault("batch_manifests_v4", {})[
            current_v4["manifest_id"]
        ] = current_v4
        if (
            isinstance(state.get("batch_manifest_v4"), dict)
            and state["batch_manifest_v4"].get("manifest_id")
            == current_v4["manifest_id"]
        ):
            state["batch_manifest_v4"] = current_v4

    if import_errors:
        state["status"] = "partial"
        result["status"] = "partial"
        atomic_write_text(
            str(run_root / "run_state.json"),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        return result

    verified_artifacts = _v3_verified_artifacts(
        run_dir, manifest, state["staged_files"]
    )
    followup = None
    submitted_followup = any(
        row.get("source_manifest_hash") == digest(manifest)
        for row in (state.get("generate_batches") or {}).values()
        if isinstance(row, dict)
    )
    graph_for_ready = manifest["snapshot"]["structure"]
    from .orchestration.waves import build_execution_dag
    dag_for_ready = build_execution_dag(graph_for_ready)
    completed_for_ready = (
        set(manifest.get("completed_dependency_targets") or [])
        | set(verified_artifacts)
        | set(state.get("resource_completed_paths") or [])
    )
    ready_deferred = {
        path
        for path in (manifest.get("deferred_paths") or [])
        if set(dag_for_ready.content_dependencies.get(path, ())) <= completed_for_ready
    }
    if ready_deferred and not submitted_followup:
        atomic_write_text(
            str(run_root / "run_state.json"),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        followup = _submit_v3_followup_wave(
            client,
            run_dir,
            state,
            manifest,
            verified_artifacts,
            progress=progress,
        )
    if followup is not None:
        result["next_batch_id"] = followup["batch_id"]
        result["status"] = "batch_pending"
        result["import_status"] = "files_complete_unverified"
        return result

    root_manifest = state.get("generate_batch") or manifest
    graph = root_manifest["snapshot"]["structure"]
    production_actions = (
        {"generate"} if graph["mode"] == "GENERATE" else {"add", "modify"}
    )
    expected_paths = set(
        root_manifest.get("approved_paths")
        or {
            row["path"]
            for row in graph["spine"]["files"]
            if row["kind"] == "text" and row["action"] in production_actions
        }
    )
    staged_paths = set(merged)
    missing = sorted(expected_paths - staged_paths)
    result["missing"] = missing

    pending = {
        identifier
        for identifier in {
            state.get("batch_id"),
            *(state.get("generate_batches") or {}).keys(),
        }
        if identifier
        and (state.get("batch_imports") or {}).get(identifier, {}).get(
            "import_status"
        )
        not in {"files_complete_unverified", "partial", "dry_run"}
    }
    resource_pending = list(state.get("resource_pending_paths") or [])
    if pending:
        state["status"] = "batch_pending"
    elif resource_pending:
        manual = any(
            (state.get("resource_states") or {}).get(path, {}).get("status")
            == "waiting_manual"
            for path in resource_pending
        )
        state["status"] = (
            "waiting_manual_resource" if manual else "partial"
        )
    elif missing:
        state["status"] = "partial"
    elif manifest.get("dry_run"):
        state["status"] = "dry_run"
    else:
        state["status"] = "files_complete_unverified"
    result["status"] = state["status"]
    # import_status describes this provider batch, not the aggregate run.
    # Another still-pending batch must not make an already imported batch look
    # pending again.
    result["import_status"] = (
        "dry_run" if manifest.get("dry_run") else "files_complete_unverified"
    )
    state["batch_imports"][batch_id] = copy.deepcopy(result)
    atomic_write_text(
        str(run_root / "run_state.json"),
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    return result


def process_saved_batch(client, run_dir, batch_id, settings, *, batch=None, progress=None):
    """Stáhne a vyhodnotí vlastní dávku; neprovádí žádný vygenerovaný kód."""
    state_path = Path(run_dir) / "run_state.json"
    from .recoverable_artifacts import load_run_state
    state = load_run_state(run_dir)
    if batch_id != state.get("batch_id") and batch_id not in state.get("generate_batches", {}):
        raise ContractError("Dávka nepatří k tomuto běhu.")
    manifest = (state.get("generate_batches") or {}).get(batch_id, state["generate_batch"])
    batch = batch if batch is not None else client.retrieve_batch(batch_id)
    if batch.get("status") not in ("completed", "failed", "expired", "cancelled"):
        raise ContractError("Dávka ještě není v konečném stavu.")
    raw_files = []
    if progress:
        progress(ProgressEvent("Stahování výsledků", detail="Stahuji výstupní a chybové JSONL."))
    response_dir = Path(run_dir) / "responses"
    response_dir.mkdir(exist_ok=True)
    for key in ("output_file_id", "error_file_id"):
        if batch.get(key):
            raw = client.file_content(batch[key])
            raw_files.append(raw)
            raw_path = safe_join_under_root(str(response_dir), f"{batch_id}_{key}.jsonl")
            Path(raw_path).write_bytes(raw)
    batch_usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    requests_by_id = {r["custom_id"]: r["body"] for r in manifest["requests"]}
    for raw in raw_files:
        for line in raw.decode("utf-8").splitlines():
            if not line.strip():
                continue
            result_row = parse_json_strict(line)
            cid = result_row.get("custom_id")
            if cid in requests_by_id:
                body = (result_row.get("response") or {}).get("body") or {"status": "failed"}
                if result_row.get("error"):
                    body = {**body, "error": result_row["error"]}
                usage = body.get("usage") or {}
                batch_usage["input_tokens"] += usage.get("input_tokens", 0)
                batch_usage["output_tokens"] += usage.get("output_tokens", 0)
                batch_usage["reasoning_tokens"] += (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
    if progress:
        progress(ProgressEvent("Spotřeba BATCH", detail=f"Vstup {batch_usage['input_tokens']:,} · "
            f"výstup {batch_usage['output_tokens']:,} · reasoning {batch_usage['reasoning_tokens']:,} tokenů"))
    if (
        (manifest.get("snapshot") or {}).get("structure", {}).get("contract")
        == "IMPLEMENTATION_GRAPH_V3"
    ):
        return _process_saved_batch_v3(
            client,
            run_dir,
            batch_id,
            state,
            manifest,
            batch,
            raw_files,
            batch_usage,
            settings,
            progress=progress,
        )
    target = state.get("out_dir")
    if not target:
        raise ContractError("Běh nemá cílový adresář OUT.")
    previous_import = state.get("batch_imports", {}).get(batch_id, {})
    allowed = {**manifest.get("overwrite_hashes", {}), **manifest.get("base_hashes", {}),
               **previous_import.get("hashes", {})}
    if progress:
        progress(ProgressEvent("Validace kontraktů", detail="Ověřuji výsledky proti uloženému manifestu."))
    result = import_results(manifest, raw_files, target, state.get("generated_hashes"), allowed, progress=progress)
    if result["dry_run"]:
        staging_root = Path(run_dir) / "staging" / "dry_run" / str(batch_id)
        generated_root = staging_root / "generated"
        generated_root.mkdir(parents=True, exist_ok=True)
        source_root = str((state.get("ui_state") or {}).get("in_dir") or "")
        staged, diff_parts = [], []
        for planned in result.get("planned_files", []):
            rel, content = planned["path"], planned["content"]
            destination = safe_join_under_root(str(generated_root), rel)
            Path(destination).parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(destination, content)
            digest_value = hashlib.sha256(Path(destination).read_bytes()).hexdigest()
            before = ""
            source = safe_join_under_root(source_root, rel) if source_root else ""
            if source and os.path.isfile(source):
                before = Path(source).read_text(encoding="utf-8")
            diff_parts.extend(difflib.unified_diff(
                before.splitlines(keepends=True), content.splitlines(keepends=True),
                fromfile=f"a/{rel}", tofile=f"b/{rel}",
            ))
            staged.append({
                "path": rel,
                "staged_path": str(Path(destination).relative_to(Path(run_dir))),
                "bytes": os.path.getsize(destination),
                "sha256": digest_value,
                "action": "modify" if before else "add",
            })
        diff_text = "".join(diff_parts)
        dry_manifest = {
            "version": 1, "mode": manifest.get("mode"), "dry_run": True,
            "publication": "blocked", "batch_id": batch_id,
            "staging_root": str(staging_root.relative_to(Path(run_dir))),
            "files": staged,
        }
        verification = {
            "version": 1, "status": "passed", "technical_validation": "passed",
            "content_acceptance": "not_claimed", "published": False,
            "checks": ["batch_contracts", "relative_paths", "staged_sha256", "diff_generated"],
            "files": [{"path": row["path"], "sha256": row["sha256"]} for row in staged],
        }
        atomic_write_text(str(staging_root / "changes.diff"), diff_text)
        atomic_write_text(str(staging_root / "manifest.json"), json.dumps(dry_manifest, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(str(staging_root / "verification.json"), json.dumps(verification, ensure_ascii=False, indent=2) + "\n")
        result.update(staged_files=staged, dry_run_staging=dry_manifest["staging_root"],
                      diff=diff_text, verification=verification)
    result["usage"] = batch_usage
    result["import_status"] = result["status"]
    for body in result.pop("responses"):
        stage = "B3" if manifest.get("mode") == "MODIFY" else "A3"
        response_path = safe_join_under_root(str(response_dir), f"{stage}_batch_{body['id']}.json")
        atomic_write_text(response_path, json.dumps({**body, "batch_id": batch_id}, ensure_ascii=False))
    state["generated_hashes"] = result["hashes"]
    state.setdefault("batch_records", {})[batch_id] = batch
    state.setdefault("batch_imports", {})[batch_id] = result
    expected_paths = set(state["generate_batch"]["expected"].values())
    primary_completed = state["generate_batch"].get("completed_hashes", {})
    expected_paths.update(primary_completed)
    hashes_to_check = {**primary_completed, **result["hashes"]}
    missing = []
    for path in sorted(expected_paths):
        if result["dry_run"]:
            continue
        dest = safe_join_under_root(target, path)
        if not os.path.isfile(dest) or hashlib.sha256(Path(dest).read_bytes()).hexdigest() != hashes_to_check.get(path):
            missing.append(path)
    state["status"] = "partial" if missing or result["errors"] or result["completed_errors"] or state["generate_batch"].get("omitted") else "files_complete_unverified"
    if result["dry_run"]:
        state["dry_run"] = True
        state["written_files"] = []
        state["staged_files"] = result.get("staged_files", [])
        state["dry_run_staging"] = result.get("dry_run_staging")
        state["verification_evidence"] = result.get("verification")
        if state["status"] == "files_complete_unverified":
            state["status"] = "dry_run"
    known_batches = {state.get("batch_id"), *state.get("generate_batches", {})} - {None}
    if known_batches - state["batch_imports"].keys():
        state["status"] = "batch_pending"
    result["omitted"] = state["generate_batch"].get("omitted", [])
    result["status"] = state["status"]
    result["missing"] = missing
    if progress:
        progress(ProgressEvent("Aktualizace evidence", detail="Ukládám stav importu."))
    atomic_write_text(str(state_path), json.dumps(state, ensure_ascii=False, indent=2))
    return result



def _repeat_v3_batch(
    client,
    run_dir,
    state,
    source,
    selected,
    feedback,
):
    if state.get("submission_unknown") or state.get("pending_batch_submission"):
        raise ContractError(
            "Neznámý submit musí být dohledán před novým odesláním."
        )
    from .orchestration.authorization import (
        create_targeted_retry_authorization,
        validate_execution_authorization,
    )

    try:
        root_authorization = validate_execution_authorization(
            state.get("execution_authorization") or {},
            run_id=Path(run_dir).name,
            run_config=state.get("run_config_v2") or {},
            scope_hash=str(state.get("run_scope_hash") or ""),
            require_repair=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            "Ruční retry vyžaduje platnou explicitní autorizaci opravy."
        ) from exc

    approved = set(
        source.get("approved_paths") or source.get("expected", {}).values()
    )
    if not selected <= approved:
        raise ContractError(
            "Ruční retry se pokouší rozšířit původní schválený scope."
        )
    manual_authorization = create_targeted_retry_authorization(
        root_authorization.run_id,
        root_authorization.scope_hash,
        selected,
        digest(source),
    )
    state.setdefault("manual_retry_authorizations", {})[
        manual_authorization.approval_id
    ] = manual_authorization.to_dict()
    staged_files = list(state.get("staged_files") or [])
    verified_artifacts = _v3_verified_artifacts(
        run_dir, source, staged_files
    )

    originals = {}
    bundle = RunBundle(Path(run_dir))
    root = Path(run_dir).resolve()
    for artifact in bundle.artifacts():
        if artifact.get("role") != "in_project_file":
            continue
        metadata = artifact.get("metadata") or {}
        rel = str(
            metadata.get("relative_path")
            or artifact.get("reconstruction_role")
            or ""
        )
        path_in_bundle = artifact.get("path_in_bundle")
        if not rel or not isinstance(path_in_bundle, str):
            continue
        source_path = (root / path_in_bundle).resolve()
        try:
            source_path.relative_to(root)
        except ValueError:
            continue
        if not source_path.is_file():
            continue
        try:
            originals[rel] = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

    frozen_original_hashes = dict(
        source["snapshot"].get("original_hashes") or {}
    )
    originals = {
        path: content
        for path, content in originals.items()
        if path in frozen_original_hashes
    }
    if set(originals) != set(frozen_original_hashes):
        missing = sorted(set(frozen_original_hashes) - set(originals))
        raise ContractError(
            "Oprava nemá úplný původní SourcePack pro: " + ", ".join(missing)
        )
    if any(
        hashlib.sha256(content.encode("utf-8")).hexdigest()
        != frozen_original_hashes[path]
        for path, content in originals.items()
    ):
        raise ContractError(
            "Oprava nemá shodný původní obsah se zmrazeným snapshotem."
        )

    expected_target_hashes = dict(source["snapshot"].get("expected_target_hashes") or {})
    if not selected <= expected_target_hashes.keys():
        raise ContractError("Oprava nemá zmrazené původní hashe cílových souborů.")
    cfg = _v3_cfg_namespace(state)
    cfg.execution_approval_id = manual_authorization.approval_id
    model = str(source["requests"][0]["body"]["model"])
    temperature = source["requests"][0]["body"].get("temperature")
    repair_instruction = str(feedback or "").strip() or (
        "Znovu vyrob pouze vybraný cílový soubor podle jeho zmrazeného kontraktu."
    )
    manifest = build_manifest(
        Path(run_dir).name,
        str(source["snapshot"].get("prompt") or ""),
        source["snapshot"]["plan"],
        source["snapshot"]["structure"],
        model,
        temperature,
        sorted(selected),
        requirements=source["snapshot"]["requirements"],
        maximum_quality=bool(source["snapshot"].get("maximum_quality")),
        mode=source["mode"],
        originals=originals,
        recovery_instruction=repair_instruction,
        run_config=cfg,
        expected_target_hashes=expected_target_hashes,
        verified_artifacts=verified_artifacts,
        approved_paths=approved,
        completed_targets=(
            set(source.get("completed_dependency_targets") or [])
            | set(verified_artifacts)
            | set(state.get("resource_completed_paths") or [])
        ),
    )
    # Targeted retry narrows execution, never the canonical preparation snapshot.
    if manifest["snapshot_hash"] != source["snapshot_hash"]:
        raise ContractError(
            "Targeted retry změnil zmrazený snapshot; odeslání je zablokováno."
        )

    # A repair is terminal for exactly the explicitly selected targets; it must
    # not accidentally continue unrelated deferred tasks from the source batch.
    manifest["deferred_paths"] = []
    manifest["blocked_requested_paths"] = []

    repo, orders = _prepare_v3_followup(run_dir, state, manifest, retry_source=source)
    data = encode_requests(manifest)
    request_path = (
        Path(run_dir)
        / "requests"
        / f"repair_{uuid.uuid4().hex[:12]}.jsonl"
    )
    request_path.write_bytes(data)
    for row in manifest["requests"]:
        client.validate_access(row["body"], batch=True)
    uploaded = client.upload_file(str(request_path), purpose="batch")
    input_file_id = str(uploaded.get("id") or "")
    if not input_file_id:
        raise ContractError("Targeted repair upload nemá Files ID.")

    from .orchestration.batch_manifest import (
        from_file_manifest,
        transition as transition_batch_manifest,
    )
    v4 = from_file_manifest(
        Path(run_dir).name,
        manifest,
        wave_no=int(source.get("wave_no") or 0),
    )
    v4 = transition_batch_manifest(
        v4, "input_uploaded", input_file_id=input_file_id
    )
    v4 = transition_batch_manifest(v4, "submitting")
    state.setdefault("batch_manifests_v4", {})[v4["manifest_id"]] = v4
    state["pending_batch_submission"] = {
        "input_file_id": input_file_id,
        "manifest": manifest,
        "manifest_v4_id": v4["manifest_id"],
    }
    state["submission_input_file_id"] = input_file_id
    state["submission_endpoint"] = "/v1/responses"
    state["submission_jsonl_sha256"] = hashlib.sha256(data).hexdigest()
    state["submission_unknown"] = True
    atomic_write_text(
        str(Path(run_dir) / "run_state.json"),
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    physical_submit = response_batch_submit_payload(input_file_id)
    for order in orders.values():
        repo.bind_physical_request(
            order.attempt_id,
            physical_request_hash=canonical_sha256(physical_submit),
            remote_input_file_id=input_file_id,
        )
        repo.mark_submission_started(order.attempt_id)
    try:
        batch = submit_verified_batch(
            client, input_file_id, manifest["requests"]
        )
    except Exception as exc:
        definite_reject = (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None)
            in {400, 401, 403, 404, 422, 429}
        )
        if definite_reject:
            for order in orders.values():
                repo.mark_not_submitted(order.attempt_id)
            v4 = transition_batch_manifest(v4, "failed")
            state["submission_unknown"] = False
        else:
            for order in orders.values():
                repo.mark_submitted(
                    order.attempt_id, None, unknown=True
                )
            v4 = transition_batch_manifest(v4, "submission_unknown")
            state["status"] = "submission_unknown"
        state["batch_manifests_v4"][v4["manifest_id"]] = v4
        atomic_write_text(
            str(Path(run_dir) / "run_state.json"),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        raise

    batch_id = str(batch.get("id") or "")
    if not batch_id:
        for order in orders.values():
            repo.mark_submitted(
                order.attempt_id, None, unknown=True
            )
        state["status"] = "submission_unknown"
        state["submission_unknown"] = True
        v4 = transition_batch_manifest(v4, "submission_unknown")
        state["batch_manifests_v4"][v4["manifest_id"]] = v4
        atomic_write_text(
            str(Path(run_dir) / "run_state.json"),
            json.dumps(state, ensure_ascii=False, indent=2),
        )
        raise ContractError(
            "Targeted repair nemá potvrzené provider ID; nový submit je zablokován."
        )

    for order in orders.values():
        repo.mark_submitted(
            order.attempt_id, batch_id, unknown=False
        )
    v4 = transition_batch_manifest(
        v4, "submitted", provider_batch_id=batch_id
    )
    state["batch_manifests_v4"][v4["manifest_id"]] = v4
    state.setdefault("generate_batches", {})[batch_id] = manifest
    state.setdefault("batch_records", {})[batch_id] = batch
    state.pop("pending_batch_submission", None)
    state["submission_unknown"] = False
    state["status"] = "batch_pending"
    atomic_write_text(
        str(Path(run_dir) / "run_state.json"),
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    return {"batch_id": batch_id, "files": len(manifest["requests"])}


def repeat_saved_batch(client, run_dir, source_batch_id, paths, feedback=""):
    from .recoverable_artifacts import load_run_state
    state = load_run_state(run_dir)
    if source_batch_id != state.get("batch_id") and source_batch_id not in state.get("generate_batches", {}):
        raise ContractError("Dávka nepatří k tomuto běhu.")
    source = (state.get("generate_batches") or {}).get(source_batch_id, state["generate_batch"])
    selected = set(paths)
    if (
        source.get("version") == 3
        and (source.get("snapshot") or {}).get("structure", {}).get("contract")
        == "IMPLEMENTATION_GRAPH_V3"
    ):
        encode_requests(source)
        if not selected or not selected <= set(source["expected"].values()):
            raise ContractError("Vyberte pouze soubory z manifestu dávky.")
        return _repeat_v3_batch(
            client, run_dir, state, source, selected, feedback
        )
    raise ContractError(
        "Legacy dávku lze importovat; nové odeslání vyžaduje nový WorkOrder "
        "s kompatibilními schválenými podklady. Chybí implementační detail "
        "IMPLEMENTATION_GRAPH_V3; spusťte novou přípravu."
    )
