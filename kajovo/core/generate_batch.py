"""Společná specifikace a samostatné souborové úlohy GENERATE a MODIFY BATCH."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .contracts import ContractError, file_response_format, parse_json_strict, validate_paths
from .request_rules import uses_reasoning_defaults, validate_response_payload
from .structured_output import validate_output
from .utils import atomic_write_text, is_versing_snapshot_dir, safe_join_under_root
from .batch_submit import submit_verified_batch
from .progress import ProgressEvent
from .context_compiler import ContextCompiler, canonical
from .context_budget import configure_file_request, measure_request, enforce_budget


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


def build_manifest(run_id, prompt, plan, structure, model, temperature, paths=None, *,
                   requirements=None, maximum_quality=False, mode="GENERATE", originals=None,
                   recovery_instruction=""):
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
    rows, reports = [], []
    for index, file in enumerate(selected):
        action = file["action"] if modifying else None
        if modifying and action == "modify" and not isinstance(originals.get(file["path"]), str):
            raise ContractError(f"Chybí úplný původní obsah souboru {file['path']}.")
        compiled = compiler.compile(file["path"], originals=originals)
        context = {"file_context": compiled, "file": file}
        if recovery_instruction:
            context["recovery_instruction"] = str(recovery_instruction)
        fmt = file_response_format(stage, file["path"], 0, action=action)
        chunk = fmt["format"]["schema"]["properties"]["chunking"]["properties"]
        chunk["chunk_count"] = {"type": "integer", "enum": [1]}
        chunk["has_more"] = {"type": "boolean", "enum": [False]}
        chunk["next_chunk_index"] = {"type": "null"}
        body = {
            "model": model,
            "store": False,
            "instructions": stage_instructions(stage, batch=True),
            "input": canonical(context),
        }
        body["text"] = fmt
        if temperature is not None and not uses_reasoning_defaults(model):
            body["temperature"] = temperature
        routing = configure_file_request(body, compiled, maximum_quality=maximum_quality)
        apply_quality(body, maximum_quality)
        report = enforce_budget(measure_request(body, compiled=compiled, batch=True))
        legacy_context = {"specification": snapshot, "file": file}
        if modifying:
            legacy_context["original_content"] = originals.get(file["path"], "")
            legacy_context["originals"] = {p: originals[p] for p in [file["path"], *file["dependencies"]] if p in originals}
        legacy = measure_request({**body, "input": json.dumps(legacy_context, ensure_ascii=False)}, batch=True)
        report["legacy_estimated_input_tokens"] = legacy["input_tokens"]
        report["saved_estimated_input_tokens"] = legacy["input_tokens"] - report["input_tokens"]
        validate_response_payload(body)
        rows.append({"custom_id": f"{run_id}_{stage[:2]}_{index:05d}", "method": "POST", "url": "/v1/responses", "body": body})
        report.update(routing=routing, path=file["path"], custom_id=rows[-1]["custom_id"])
        reports.append(report)
    manifest = {"version": 3, "mode": mode, "snapshot": snapshot, "snapshot_hash": digest(snapshot), "requests": rows,
                "cost_context_reports": reports, "dependency_waves": compiler.graph,
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
    paths = {file["path"] for file in structure.get("touched_files" if manifest.get("mode") == "MODIFY" else "files", [])}
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
        validate_response_payload(row["body"])
        if row["method"] != "POST" or row["url"] != "/v1/responses" or row["body"].get("previous_response_id"):
            raise ContractError("Souborová úloha musí být samostatný požadavek Responses.")
        context, _ = json.JSONDecoder().raw_decode(row["body"]["input"])
        if manifest.get("version") == 3:
            compiled = context.get("file_context", {})
            original_sources = {s["path"]: s["content"] for s in
                                compiled.get("working_context", {}).get("relevant_source_excerpts", [])}
            if any(hashlib.sha256(value.encode("utf-8")).hexdigest() !=
                   manifest["snapshot"].get("original_hashes", {}).get(path)
                   for path, value in original_sources.items()):
                raise ContractError("Původní obsah neodpovídá auditnímu snapshotu.")
            expected_context = compiler.compile(
                context["file"]["path"], originals=original_sources)
            if compiled != expected_context or "specification" in context or row["body"].get("tools"):
                raise ContractError("FileContext neodpovídá kanonické přípravě.")
            enforce_budget(measure_request(row["body"], compiled=compiled, batch=True))
        elif digest(context["specification"]) != manifest["snapshot_hash"]:
            raise ContractError("Úloha neodpovídá společné specifikaci.")
        if context["file"]["path"] != manifest["expected"][row["custom_id"]]:
            raise ContractError("Úloha neodpovídá společné specifikaci nebo cílové cestě.")
        if manifest.get("version") in {2, 3}:
            modifying = manifest["mode"] == "MODIFY"
            files = manifest["snapshot"]["structure"]["touched_files" if modifying else "files"]
            if context["file"] not in files:
                raise ContractError("Souborová úloha mění kanonickou specifikaci souboru.")
            properties = row["body"]["text"]["format"]["schema"]["properties"]
            stage = "B3_FILE" if modifying else "A3_FILE"
            if properties["contract"].get("enum") != [stage] or properties["path"].get("enum") != [context["file"]["path"]]:
                raise ContractError("Schéma úlohy neodpovídá režimu nebo cestě manifestu.")
            if modifying and ((manifest.get("version") == 2 and not isinstance(context.get("original_content"), str))
                              or properties.get("action", {}).get("enum") != [context["file"]["action"]]):
                raise ContractError("Úloha MODIFY nemá původní obsah nebo správnou akci.")
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
                errors[cid] = "Duplicitní výsledek."
            entries[cid] = item
    contents = {}
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
                raise ContractError("Požadavek selhal nebo nebyl dokončen.")
            request_body = request_bodies[cid]
            stage = "B3_FILE" if manifest.get("mode") == "MODIFY" else "A3_FILE"
            payload = validate_output(body, {"text": request_body.get("text") or file_response_format(stage, path, 0)})
            if payload.get("contract") != stage or payload.get("path") != path or not isinstance(payload.get("content"), str):
                raise ContractError("Nesouhlasí souborový kontrakt nebo cesta.")
            chunk = payload.get("chunking")
            if not isinstance(chunk, dict) or type(chunk.get("chunk_index")) is not int or chunk["chunk_index"] != 0 or type(chunk.get("chunk_count")) is not int or chunk["chunk_count"] != 1 or chunk.get("has_more") is not False or chunk.get("next_chunk_index") is not None:
                raise ContractError("Dávkový soubor musí být úplný v jediné části.")
            contents[cid] = payload["content"]
            if manifest.get("version") == 3:
                context, _ = json.JSONDecoder().raw_decode(request_body["input"])
                allow_empty = context["file_context"]["working_context"]["implementation_contract"]["allow_empty"]
                if not payload["content"].strip() and not allow_empty:
                    contents.pop(cid)
                    raise ContractError("Prázdný soubor odporuje implementačnímu kontraktu.")
        except (ContractError, AttributeError) as exc:
            errors[cid] = str(exc)
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
                hashes[path] = new_hash
                written.append(path)
        except (OSError, ValueError, ContractError) as exc:
            errors[cid] = str(exc)
        if progress:
            progress(ProgressEvent("Ukládání souborů", completed=write_index, total=len(items), unit="souborů", detail=path))
    return {"written": written, "errors": errors, "hashes": hashes, "responses": responses,
            "completed_errors": completed_errors,
            "dry_run": dry_run, "planned_files": planned, "snapshot_dir": snapshot_dir,
            "file_errors": {**completed_errors, **{expected[cid]: message for cid, message in errors.items()}},
            "omitted": manifest.get("omitted", []),
            "status": "partial" if errors or completed_errors or manifest.get("omitted") else
                      "dry_run" if dry_run else "files_complete_unverified"}


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
    from .cost_context_report import CostContextReport
    reporter = CostContextReport(run_dir)
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
                reporter.record(requests_by_id[cid], custom_id=cid, response=body,
                                path=manifest["expected"][cid])
                usage = body.get("usage") or {}
                batch_usage["input_tokens"] += usage.get("input_tokens", 0)
                batch_usage["output_tokens"] += usage.get("output_tokens", 0)
                batch_usage["reasoning_tokens"] += (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
    if progress:
        progress(ProgressEvent("Spotřeba BATCH", detail=f"Vstup {batch_usage['input_tokens']:,} · "
            f"výstup {batch_usage['output_tokens']:,} · reasoning {batch_usage['reasoning_tokens']:,} tokenů"))
    target = state.get("out_dir")
    if not target:
        raise ContractError("Běh nemá cílový adresář OUT.")
    previous_import = state.get("batch_imports", {}).get(batch_id, {})
    allowed = {**manifest.get("overwrite_hashes", {}), **manifest.get("base_hashes", {}),
               **previous_import.get("hashes", {})}
    if progress:
        progress(ProgressEvent("Validace kontraktů", detail="Ověřuji výsledky proti uloženému manifestu."))
    result = import_results(manifest, raw_files, target, state.get("generated_hashes"), allowed, progress=progress)
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


def repeat_saved_batch(client, run_dir, source_batch_id, paths, feedback=""):
    from .requirements import stage_instructions

    state_path = Path(run_dir) / "run_state.json"
    from .recoverable_artifacts import load_run_state, save_artifact
    state = load_run_state(run_dir)
    if source_batch_id != state.get("batch_id") and source_batch_id not in state.get("generate_batches", {}):
        raise ContractError("Dávka nepatří k tomuto běhu.")
    source = (state.get("generate_batches") or {}).get(source_batch_id, state["generate_batch"])
    encode_requests(source)
    if source.get("version") != 3:
        raise ContractError("Legacy dávku lze importovat; nové odeslání vyžaduje implementační přípravu a manifest v3.")
    if state.get("submission_unknown") or state.get("pending_batch_submission") or state.get("status") == "submission_unknown":
        raise ContractError("Neznámý submit musí být dohledán před novým odesláním.")
    selected = set(paths)
    if not selected or not selected <= set(source["expected"].values()):
        raise ContractError("Vyberte pouze soubory z manifestu dávky.")
    manifest = copy.deepcopy(source)
    manifest["base_hashes"] = {p: h for p, h in {
        **source.get("overwrite_hashes", {}), **source.get("base_hashes", {}),
        **state.get("generated_hashes", {}),
    }.items() if p in selected}
    rows, expected = [], {}
    prefix = uuid.uuid4().hex
    stage = "B3_FILE" if manifest.get("mode") == "MODIFY" else "A3_FILE"
    for source_row in source["requests"]:
        row = copy.deepcopy(source_row)
        path = source["expected"][row["custom_id"]]
        if path not in selected:
            continue
        row["custom_id"] = f"{prefix}_{stage[:2]}_{len(rows):05d}"
        row["body"]["instructions"] = stage_instructions(stage, batch=True)
        if feedback:
            dest = safe_join_under_root(state["out_dir"], path)
            content = Path(dest).read_text(encoding="utf-8") if os.path.isfile(dest) else ""
            row["body"]["input"] += "\n" + json.dumps({"repair": feedback, "current_content": content,
                "instruction": "Oprav celý soubor, zachovej společná rozhraní."}, ensure_ascii=False)
            # Výslovně vybraný obsah je základ opravy; další změny import opět ochrání.
            if os.path.isfile(dest):
                manifest["base_hashes"][path] = hashlib.sha256(Path(dest).read_bytes()).hexdigest()
        rows.append(row)
        expected[row["custom_id"]] = path
    manifest.update(requests=rows, expected=expected, omitted=[])
    for row in rows:
        context, _ = json.JSONDecoder().raw_decode(row["body"]["input"])
        action = context["file"]["action"] if stage == "B3_FILE" else None
        row["body"]["text"] = file_response_format(stage, expected[row["custom_id"]], 0, action=action)
        chunk = row["body"]["text"]["format"]["schema"]["properties"]["chunking"]["properties"]
        chunk.update(chunk_count={"type": "integer", "enum": [1]}, has_more={"type": "boolean", "enum": [False]}, next_chunk_index={"type": "null"})
        client.validate_access(row["body"], batch=True)
    data = encode_requests(manifest)
    path = Path(run_dir) / "requests" / f"repeat_{prefix}.jsonl"
    path.write_bytes(data)
    from .cost_context_report import CostContextReport
    reporter = CostContextReport(run_dir)
    for row in rows:
        context, _ = json.JSONDecoder().raw_decode(row["body"]["input"])
        measurement = measure_request(row["body"], compiled=context["file_context"], batch=True)
        reporter.record(row["body"], custom_id=row["custom_id"], path=expected[row["custom_id"]],
                        measurement=measurement, status="submitting")
    uploaded = client.upload_file(str(path), purpose="batch")
    state["pending_batch_submission"] = {"input_file_id": uploaded["id"], "manifest": manifest}
    save_artifact(run_dir, "state/pending_batch_submission", state["pending_batch_submission"])
    state["submission_input_file_id"] = uploaded["id"]
    state["submission_endpoint"] = "/v1/responses"
    state["submission_jsonl_sha256"] = hashlib.sha256(data).hexdigest()
    state["submission_unknown"] = True
    atomic_write_text(str(state_path), json.dumps(state, ensure_ascii=False, indent=2))
    batch = submit_verified_batch(client, uploaded["id"], manifest["requests"])
    state.setdefault("batch_records", {})[batch["id"]] = batch
    state.setdefault("generate_batches", {})[batch["id"]] = manifest
    save_artifact(run_dir, "state/generate_batches", state["generate_batches"])
    state.pop("pending_batch_submission", None)
    state["submission_unknown"] = False
    state["status"] = "batch_pending"
    atomic_write_text(str(state_path), json.dumps(state, ensure_ascii=False, indent=2))
    return {"batch_id": batch["id"], "files": len(rows)}
