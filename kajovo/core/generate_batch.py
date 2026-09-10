"""Společná specifikace a samostatné souborové úlohy GENERATE BATCH."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from .contracts import ContractError, file_response_format, parse_json_strict, validate_paths
from .request_rules import uses_reasoning_defaults, validate_response_payload
from .structured_output import validate_output
from .utils import atomic_write_text, safe_join_under_root


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
            "dependencies": {**strings, "description": "Pouze přesné cesty jiných souborů tohoto projektu z files[].path. Žádné externí ani standardní knihovny."},
            "provides": {**strings, "description": "Pouze identifikátory z interfaces[].id poskytované tímto souborem."},
            "requires": {**strings, "description": "Pouze identifikátory z interfaces[].id využívané tímto souborem. Žádné importy standardní knihovny."},
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


def validate_structure(struct):
    import jsonschema
    try:
        jsonschema.validate(struct, structure_format()["format"]["schema"])
    except jsonschema.ValidationError as exc:
        raise ContractError(f"Neúplná specifikace A2: {exc.message}") from exc
    validate_paths(struct["files"])
    paths = {f["path"] for f in struct["files"]}
    ids = [i["id"] for i in struct["interfaces"]]
    if len(set(ids)) != len(ids) or any(not i.strip() for i in ids):
        raise ContractError("Rozhraní A2 musí mít jedinečné neprázdné identifikátory.")
    if any(not i["definition"].strip() for i in struct["interfaces"]):
        raise ContractError("Rozhraní A2 vyžaduje přesnou definici.")
    if any(not p["name"].strip() or not p["version"].strip() for p in struct["packages"]):
        raise ContractError("Závislosti vyžadují jméno a verzi.")
    provided = {i for f in struct["files"] for i in f["provides"]}
    for file in struct["files"]:
        if not file["purpose"].strip() or not file["behavior"].strip():
            raise ContractError("Soubor A2 vyžaduje účel a požadované chování.")
        if not set(file["dependencies"]) <= paths:
            raise ContractError(f"Soubor {file['path']}: neznámé závislosti {sorted(set(file['dependencies']) - paths)}; povolené cesty {sorted(paths)}. Standardní a externí knihovny sem nepatří.")
        if not set(file["provides"] + file["requires"]) <= set(ids):
            raise ContractError(f"Soubor {file['path']}: povolené identifikátory rozhraní jsou {ids}, provides/requires musí používat právě tato id.")
        if not set(file["requires"]) <= provided:
            raise ContractError(f"Chybí poskytovatel rozhraní: {file['path']}")
        available = {i for f in struct["files"] if f["path"] in file["dependencies"] or f["path"] == file["path"] for i in f["provides"]}
        if not set(file["requires"]) <= available:
            raise ContractError(f"Soubor {file['path']} musí uvést poskytovatele vyžadovaného rozhraní mezi dependencies.")
        if file["kind"] == "text" and Path(file["path"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".exe", ".dll", ".woff", ".woff2", ".ttf"}:
            raise ContractError(f"Binární prostředek musí mít kind=binary: {file['path']}")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_batch_model(model):
    # Lokální seznam textových rodin; skutečný přístup ověřuje služba při přijetí dávky.
    if not re.match(r"^(?:gpt-(?:4o|4\.1|5|6)(?:[.-]|$)|o[134](?:-|$))", model):
        raise ValueError("Pro tento model není v aplikaci ověřena rodina podporující textový Batch.")
    validate_response_payload({"model": model})


def build_manifest(run_id, prompt, plan, structure, model, temperature, paths=None):
    validate_structure(structure)
    validate_batch_model(model)
    snapshot = copy.deepcopy({"prompt": prompt, "plan": plan, "structure": structure})
    selected = [f for f in structure["files"] if f["kind"] == "text" and (paths is None or f["path"] in paths)]
    if not selected:
        raise ContractError("Manifest neobsahuje žádné vybrané textové soubory.")
    if len(selected) > 50_000:
        raise ContractError("Dávka překračuje 50 000 souborů.")
    rows = []
    for index, file in enumerate(selected):
        fmt = file_response_format("A3_FILE", file["path"], 0)
        chunk = fmt["format"]["schema"]["properties"]["chunking"]["properties"]
        chunk["chunk_count"] = {"type": "integer", "enum": [1]}
        chunk["has_more"] = {"type": "boolean", "enum": [False]}
        chunk["next_chunk_index"] = {"type": "null"}
        body = {
            "model": model,
            "store": False,
            "instructions": "Vytvoř právě jeden kompletní soubor. Dodrž závazná společná rozhraní, importy a verze. "
                            "Vrať pouze JSON podle schématu; žádný markdown ani pokračování. "
                            + json.dumps(fmt["format"]["schema"], ensure_ascii=False),
            "input": json.dumps({"specification": snapshot, "file": file}, ensure_ascii=False),
        }
        body["text"] = fmt
        if temperature is not None and not uses_reasoning_defaults(model):
            body["temperature"] = temperature
        validate_response_payload(body)
        rows.append({"custom_id": f"{run_id}_A3_{index:05d}", "method": "POST", "url": "/v1/responses", "body": body})
    manifest = {"version": 1, "snapshot": snapshot, "snapshot_hash": digest(snapshot), "requests": rows,
                "expected": {row["custom_id"]: file["path"] for row, file in zip(rows, selected, strict=True)},
                "omitted": [f["path"] for f in structure["files"] if f not in selected]}
    encode_requests(manifest)
    return manifest


def encode_requests(manifest):
    if digest(manifest["snapshot"]) != manifest["snapshot_hash"]:
        raise ContractError("Specifikace dávky byla změněna.")
    rows = manifest["requests"]
    if not 0 < len(rows) <= 50_000:
        raise ContractError("Dávka vyžaduje 1 až 50 000 položek.")
    if len({r["custom_id"] for r in rows}) != len(rows):
        raise ContractError("Duplicitní ID úlohy.")
    if len({r["body"]["model"] for r in rows}) != 1:
        raise ContractError("Dávka vyžaduje jediný model A3.")
    if set(manifest["expected"]) != {r["custom_id"] for r in rows}:
        raise ContractError("Mapování úloh neodpovídá požadavkům.")
    validate_paths([{"path": p} for p in manifest["expected"].values()])
    for row in rows:
        validate_response_payload(row["body"])
        if row["method"] != "POST" or row["url"] != "/v1/responses" or row["body"].get("previous_response_id"):
            raise ContractError("Souborová úloha musí být samostatný požadavek Responses.")
        context, _ = json.JSONDecoder().raw_decode(row["body"]["input"])
        if digest(context["specification"]) != manifest["snapshot_hash"] or context["file"]["path"] != manifest["expected"][row["custom_id"]]:
            raise ContractError("Úloha neodpovídá společné specifikaci nebo cílové cestě.")
    data = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
    if len(data) > 200_000_000:
        raise ContractError("JSONL překračuje 200 MB.")
    return data


def import_results(manifest, raw_files, target, previous_hashes=None, overwrite_hashes=None):
    """Nejdříve ověří celou dávku, potom bezpečně zapíše samostatné výsledky."""
    encode_requests(manifest)
    expected = manifest["expected"]
    request_bodies = {row["custom_id"]: row["body"] for row in manifest["requests"]}
    validate_paths([{"path": p} for p in expected.values()])
    entries, errors, receipts = {}, {}, []
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
            receipts.append(body)
        try:
            if item.get("error") or response.get("status_code") != 200 or body.get("status") != "completed":
                raise ContractError("Požadavek selhal nebo nebyl dokončen.")
            request_body = request_bodies[cid]
            payload = validate_output(body, {"text": request_body.get("text") or file_response_format("A3_FILE", path, 0)})
            if payload.get("contract") != "A3_FILE" or payload.get("path") != path or not isinstance(payload.get("content"), str):
                raise ContractError("Nesouhlasí souborový kontrakt nebo cesta.")
            chunk = payload.get("chunking")
            if not isinstance(chunk, dict) or type(chunk.get("chunk_index")) is not int or chunk["chunk_index"] != 0 or type(chunk.get("chunk_count")) is not int or chunk["chunk_count"] != 1 or chunk.get("has_more") is not False or chunk.get("next_chunk_index") is not None:
                raise ContractError("Dávkový soubor musí být úplný v jediné části.")
            contents[cid] = payload["content"]
        except (ContractError, AttributeError) as exc:
            errors[cid] = str(exc)
    hashes, written = dict(previous_hashes or {}), []
    allowed = hashes if overwrite_hashes is None else overwrite_hashes
    for cid, content in contents.items():
        path = expected[cid]
        try:
            dest = safe_join_under_root(target, path)
            data = content.encode("utf-8")
            new_hash = hashlib.sha256(data).hexdigest()
            if os.path.exists(dest):
                old_hash = hashlib.sha256(Path(dest).read_bytes()).hexdigest()
                if old_hash != new_hash and old_hash != allowed.get(path):
                    raise ContractError("Existující soubor byl změněn nebo nepatří tomuto importu; zachován.")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            atomic_write_text(dest, content)
            hashes[path] = new_hash
            written.append(path)
        except (OSError, ValueError, ContractError) as exc:
            errors[cid] = str(exc)
    return {"written": written, "errors": errors, "hashes": hashes, "receipts": receipts,
            "file_errors": {expected[cid]: message for cid, message in errors.items()},
            "omitted": manifest.get("omitted", []),
            "status": "partial" if errors or manifest.get("omitted") else "files_complete_unverified"}


def process_saved_batch(client, run_dir, batch_id, settings):
    """Stáhne a vyhodnotí vlastní dávku; neprovádí žádný vygenerovaný kód."""
    from .pricing import PriceTable, compute_cost
    from .receipt import Receipt, ReceiptDB
    state_path = Path(run_dir) / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if batch_id != state.get("batch_id") and batch_id not in state.get("generate_batches", {}):
        raise ContractError("Dávka nepatří k tomuto běhu.")
    manifest = (state.get("generate_batches") or {}).get(batch_id, state["generate_batch"])
    batch = client.retrieve_batch(batch_id)
    if batch.get("status") not in ("completed", "failed", "expired", "cancelled"):
        raise ContractError("Dávka ještě není v konečném stavu.")
    raw_files = []
    response_dir = Path(run_dir) / "responses"
    response_dir.mkdir(exist_ok=True)
    for key in ("output_file_id", "error_file_id"):
        if batch.get(key):
            raw = client.file_content(batch[key])
            raw_files.append(raw)
            raw_path = safe_join_under_root(str(response_dir), f"{batch_id}_{key}.jsonl")
            Path(raw_path).write_bytes(raw)
    target = state.get("out_dir")
    if not target:
        raise ContractError("Běh nemá cílový adresář OUT.")
    previous_import = state.get("batch_imports", {}).get(batch_id, {})
    allowed = {**manifest.get("base_hashes", {}), **previous_import.get("hashes", {})}
    result = import_results(manifest, raw_files, target, state.get("generated_hashes"), allowed)
    prices = PriceTable(os.path.join(settings.cache_dir, "price_table.json"))
    prices.load_cache()
    db = ReceiptDB(settings.db_path)
    for body in result.pop("receipts"):
        receipt_path = safe_join_under_root(str(response_dir), f"A3_batch_{body['id']}.json")
        atomic_write_text(receipt_path, json.dumps({**body, "batch_id": batch_id}, ensure_ascii=False))
        usage = body.get("usage") or {}
        inp, out = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        model = body.get("model") or manifest["requests"][0]["body"]["model"]
        row = prices.get(model) or PriceTable.builtin_fallback().get(model)
        total, tool, storage = compute_cost(row, inp, out, is_batch=True, usage=usage)
        db.insert(Receipt(run_id=state["run_id"], created_at=time.time(), project=state.get("project", ""),
                          model=model, mode="GENERATE", flow_type="A3_FILE", response_id=body["id"], batch_id=batch_id,
                          input_tokens=inp, output_tokens=out, tool_cost=tool, storage_cost=storage, total_cost=total,
                          pricing_verified=prices.is_verified(model), notes="GENERATE A3 Batch",
                          log_paths={"run_dir": str(run_dir)}, usage=usage))
    state["generated_hashes"] = result["hashes"]
    state.setdefault("batch_imports", {})[batch_id] = result
    expected_paths = set(state["generate_batch"]["expected"].values())
    missing = []
    for path in sorted(expected_paths):
        dest = safe_join_under_root(target, path)
        if not os.path.isfile(dest) or hashlib.sha256(Path(dest).read_bytes()).hexdigest() != result["hashes"].get(path):
            missing.append(path)
    state["status"] = "partial" if missing or result["errors"] or state["generate_batch"].get("omitted") else "files_complete_unverified"
    result["omitted"] = state["generate_batch"].get("omitted", [])
    result["status"] = state["status"]
    result["missing"] = missing
    atomic_write_text(str(state_path), json.dumps(state, ensure_ascii=False, indent=2))
    return result


def repeat_saved_batch(client, run_dir, source_batch_id, paths, feedback="", cost_control=None):
    state_path = Path(run_dir) / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if source_batch_id != state.get("batch_id") and source_batch_id not in state.get("generate_batches", {}):
        raise ContractError("Dávka nepatří k tomuto běhu.")
    source = (state.get("generate_batches") or {}).get(source_batch_id, state["generate_batch"])
    encode_requests(source)
    selected = set(paths)
    if not selected or not selected <= set(source["expected"].values()):
        raise ContractError("Vyberte pouze soubory z manifestu dávky.")
    manifest = copy.deepcopy(source)
    manifest["base_hashes"] = {p: h for p, h in state.get("generated_hashes", {}).items() if p in selected}
    rows, expected = [], {}
    prefix = uuid.uuid4().hex
    for row in source["requests"]:
        path = source["expected"][row["custom_id"]]
        if path not in selected:
            continue
        row = copy.deepcopy(row)
        row["custom_id"] = f"{prefix}_A3_{len(rows):05d}"
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
        row["body"]["text"] = file_response_format("A3_FILE", expected[row["custom_id"]], 0)
        chunk = row["body"]["text"]["format"]["schema"]["properties"]["chunking"]["properties"]
        chunk.update(chunk_count={"type": "integer", "enum": [1]}, has_more={"type": "boolean", "enum": [False]}, next_chunk_index={"type": "null"})
        client.validate_access(row["body"], batch=True)
    data = encode_requests(manifest)
    path = Path(run_dir) / "requests" / f"repeat_{prefix}.jsonl"
    path.write_bytes(data)
    operation = None
    if cost_control:
        operation, _ = cost_control.prepare(client, [r["body"] for r in rows], batch=True, stage="A3_FILE",
                                            custom_ids=[r["custom_id"] for r in rows])
        path.write_bytes(encode_requests(manifest))
    uploaded = client.upload_file(str(path), purpose="batch")
    state["pending_batch_submission"] = {"input_file_id": uploaded["id"], "manifest": manifest}
    atomic_write_text(str(state_path), json.dumps(state, ensure_ascii=False, indent=2))
    try:
        batch = client.create_batch(input_file_id=uploaded["id"], endpoint="/v1/responses")
    except Exception:
        if cost_control:
            cost_control.ledger.mark(operation, "unknown")
        raise
    if cost_control:
        cost_control.ledger.mark(operation, "pending", batch["id"])
    state.setdefault("generate_batches", {})[batch["id"]] = manifest
    state.pop("pending_batch_submission", None)
    state["status"] = "batch_pending"
    atomic_write_text(str(state_path), json.dumps(state, ensure_ascii=False, indent=2))
    return {"batch_id": batch["id"], "files": len(rows)}
