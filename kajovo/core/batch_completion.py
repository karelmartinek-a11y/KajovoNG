"""Dokončení uložených dávek a bezpečný import souborových výsledků."""

import hashlib
import json
import time
from pathlib import Path

from .utils import safe_join_under_root, atomic_write_text
from .contracts import (
    parse_json_strict, extract_text_from_response, validate_paths,
    validate_chunk_metadata, ContractError,
)
from .generate_batch import process_saved_batch
from .progress import ProgressEvent
from .batch_submit import exact_batch_matches

TERMINAL = {"completed", "failed", "expired", "cancelled"}
CANCELLABLE = {"validating", "in_progress", "finalizing"}


def read_state(run_dir):
    try:
        value = json.loads((Path(run_dir) / "run_state.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        for key in ("generate_batches", "batch_imports", "batch_records", "ui_state"):
            if key in value and not isinstance(value[key], dict):
                return {}
        for key in ("generate_batches", "batch_imports", "batch_records"):
            if not all(isinstance(item, dict) for item in value.get(key, {}).values()):
                return {}
        return value
    except (OSError, ValueError):
        return {}


def batch_ids(state):
    return list(dict.fromkeys(bid for bid in [
        *([state["batch_id"]] if state.get("batch_id") else []),
        *(state.get("generate_batches") or {}),
    ] if isinstance(bid, str) and bid))


def pending_batch_ids(state):
    if state.get("status") == "files_complete_unverified":
        return []
    imports = state.get("batch_imports") or {}
    return [bid for bid in batch_ids(state)
            if imports.get(bid, {}).get("import_status", imports.get(bid, {}).get("status"))
            != "files_complete_unverified"]


def preflight_ids(state):
    values = state.get("preflight_batches") or []
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(item["id"] for item in values
                             if isinstance(item, dict) and isinstance(item.get("id"), str)
                             and item["id"]))


def can_continue_preflight(state):
    return bool(preflight_ids(state)) and not batch_ids(state) and not state.get("submission_unknown")


def read_batch_statuses(log_dir):
    """Samostatný snímek serveru nesmí přepisovat stav souběžně běžícího pracovníka."""
    try:
        value = json.loads((Path(log_dir) / "batch_status.json").read_text(encoding="utf-8"))
        return {bid: record for bid, record in value.items() if isinstance(record, dict)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_batch_statuses(log_dir, records):
    saved = read_batch_statuses(log_dir)
    for record in records:
        if isinstance(record.get("id"), str) and record["id"]:
            bid = record["id"]
            saved[bid] = {**saved.get(bid, {}), **record, "checked_at": time.time()}
    atomic_write_text(str(Path(log_dir) / "batch_status.json"), json.dumps(saved, ensure_ascii=False))


def recover_unknown_submission(run_dir, records):
    """Dohledá neurčitý pracovní submit pouze přes přesný input_file_id + endpoint."""
    state = read_state(run_dir)
    if not state.get("submission_unknown"):
        return None
    input_file_id = state.get("submission_input_file_id") or state.get("batch_input_file_id")
    endpoint = state.get("submission_endpoint") or "/v1/responses"
    if not input_file_id:
        raise ContractError("Neurčitý submit nemá uložený input_file_id; automatický resubmit je zakázán.")
    matches = exact_batch_matches(records, input_file_id, endpoint)
    if not matches:
        return None
    if len(matches) != 1:
        raise ContractError("Neurčitý submit odpovídá více vzdáleným dávkám; je nutný ruční zásah.")
    batch = matches[0]
    bid = str(batch.get("id") or "")
    if not bid:
        raise ContractError("Nalezená dávka nemá ID.")
    state["batch_id"] = bid
    state["submission_unknown"] = False
    state["status"] = "batch_pending"
    state.setdefault("batch_records", {})[bid] = batch
    pending = state.pop("pending_batch_submission", None)
    if isinstance(pending, dict) and isinstance(pending.get("manifest"), dict):
        state.setdefault("generate_batches", {})[bid] = pending["manifest"]
    atomic_write_text(str(Path(run_dir) / "run_state.json"), json.dumps(state, ensure_ascii=False, indent=2))
    return batch


def local_batches(log_dir):
    records = {}
    for path in sorted(Path(log_dir).glob("RUN_*/run_state.json"), reverse=True):
        state = read_state(path.parent)
        work = batch_ids(state)
        for bid in dict.fromkeys([*work, *preflight_ids(state)]):
            info = dict(run_id=path.parent.name, run_dir=str(path.parent), state=state,
                        out_dir=state.get("out_dir", ""), kind="work" if bid in work else "preflight")
            if bid not in records:
                records[bid] = {**info, "runs": []}
            records[bid]["runs"].append(info)
    return records


def import_bundle(raw, target, previous_hashes=None, expected_ids=None, expected_contract=None, progress=None):
    """Neúplný nebo kolidující soubor se nezapisuje; raw je uložen samostatně."""
    target = str(Path(target).resolve())
    bundles, chunks, errors = [], {}, []
    seen = set()
    if progress:
        progress(ProgressEvent("Parsování odpovědí", detail="Načítám JSONL výsledky."))
    try:
        entries = [parse_json_strict(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not all(isinstance(entry, dict) for entry in entries):
            raise ContractError("Výsledek musí být objekt JSON.")
    except (ContractError, ValueError) as exc:
        return {"written": [], "hashes": dict(previous_hashes or {}), "errors": [str(exc)], "status": "partial"}
    if expected_ids is not None:
        ids = [entry.get("custom_id") for entry in entries]
        if not all(isinstance(cid, str) for cid in ids) or len(ids) != len(set(ids)) or set(ids) - set(expected_ids):
            raise ContractError("Duplicitní nebo neznámé ID výsledku.")
    if progress:
        progress(ProgressEvent("Validace kontraktů", completed=0, total=len(entries), unit="položek"))
    for entry_index, entry in enumerate(entries, start=1):
        try:
            if isinstance(entry.get("custom_id"), str):
                seen.add(entry["custom_id"])
            response = entry.get("response") or {}
            if entry.get("error") or response.get("status_code", 200) >= 400:
                raise ContractError(f"Položka {entry.get('custom_id', '')} selhala.")
            body = response.get("body") or entry.get("body")
            if not isinstance(body, dict):
                raise ContractError("Chybí objekt odpovědi.")
            if body.get("status") != "completed":
                raise ContractError("Odpověď nebyla dokončena.")
            payload = parse_json_strict(extract_text_from_response(body))
            contract = payload.get("contract")
            if expected_contract and contract != expected_contract:
                raise ContractError("Výstup neodpovídá kontraktu odeslaného požadavku.")
            if contract == "C_FILES_ALL":
                files = payload.get("files")
                validate_paths(files)
                root = payload.get("root", "")
                if not isinstance(root, str) or not isinstance(files, list):
                    raise ContractError("Kořen musí být text a files seznam.")
                base = safe_join_under_root(target, root) if root else target
                if any(not isinstance(record.get("content"), str) for record in files):
                    raise ContractError("Obsah souboru musí být text.")
                bundles.extend((safe_join_under_root(base, record["path"]), record["content"]) for record in files)
            elif contract == "A3_FILE":
                name = payload.get("path", "")
                safe_join_under_root(target, name)
                info = chunks.setdefault(name, {"parts": {}, "count": None, "end": None, "invalid": False})
                try:
                    metadata = payload.get("chunking") or {}
                    validate_chunk_metadata(metadata)
                    chunk_index = metadata["chunk_index"]
                    count = metadata.get("chunk_count") or None
                    if chunk_index in info["parts"] or count and info["count"] not in (None, count):
                        raise ContractError("Duplicitní část nebo rozdílný počet částí.")
                    if not isinstance(payload.get("content"), str):
                        raise ContractError("Část souboru není text.")
                    if metadata["has_more"] is False:
                        if info["end"] is not None:
                            raise ContractError("Více koncových částí.")
                        info["end"] = chunk_index
                    info["count"] = count or info["count"]
                    info["parts"][chunk_index] = payload["content"]
                except Exception:
                    info["invalid"] = True
                    raise
            else:
                raise ContractError(f"Nepodporovaný kontrakt {contract}.")
        except Exception as exc:
            errors.append(str(exc))
        if progress:
            progress(ProgressEvent("Validace kontraktů", completed=entry_index, total=len(entries), unit="položek"))
    try:
        validate_paths([{"path": name} for name in chunks])
    except ContractError as exc:
        return {"written": [], "errors": [*errors, str(exc)], "status": "partial"}
    for name, info in chunks.items():
        count = info["count"] or len(info["parts"])
        if info["invalid"] or sorted(info["parts"]) != list(range(count)) or info["end"] != count - 1:
            errors.append(f"{name}: neúplné nebo neplatné části.")
        else:
            bundles.append((safe_join_under_root(target, name), "".join(info["parts"][part] for part in range(count))))
    if expected_ids is not None and set(expected_ids) - seen:
        errors.append("Chybí výsledek požadavku.")
    if not bundles and not errors:
        errors.append("Dávka neobsahuje souborové výsledky.")
    counts = {}
    for destination, _content in bundles:
        counts[destination.casefold()] = counts.get(destination.casefold(), 0) + 1
    written, hashes = [], dict(previous_hashes or {})
    if progress:
        progress(ProgressEvent("Ukládání souborů", completed=0, total=len(bundles), unit="souborů"))
    for write_index, (destination, content) in enumerate(bundles, start=1):
        if counts[destination.casefold()] != 1:
            errors.append(f"Kolize cílové cesty: {destination}")
        else:
            try:
                relative = Path(destination).relative_to(Path(target)).as_posix()
                new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if Path(destination).exists():
                    old_hash = hashlib.sha256(Path(destination).read_bytes()).hexdigest()
                    if old_hash != new_hash and old_hash != hashes.get(relative):
                        raise ContractError("Existující soubor byl změněn nebo nepatří importu; zachován.")
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(destination, content)
                written.append(destination)
                hashes[relative] = new_hash
            except (OSError, ValueError, ContractError) as exc:
                errors.append(str(exc))
        if progress:
            progress(ProgressEvent("Ukládání souborů", completed=write_index, total=len(bundles), unit="souborů", detail=str(destination)))
    return {"written": written, "hashes": hashes, "errors": errors,
            "status": "partial" if errors else "files_complete_unverified"}


def complete_saved_batch(client, run_dir, batch_id, settings, progress=None):
    """Převezme existující dávku bez nových generujících požadavků."""
    state = read_state(run_dir)
    if batch_id not in batch_ids(state):
        raise ContractError("Dávka nepatří k tomuto běhu nebo chybí jeho podklady.")
    if progress:
        progress(ProgressEvent("Kontrola stavu dávky", detail=f"Ověřuji {batch_id}."))
    batch = client.retrieve_batch(batch_id)
    if batch.get("status") not in TERMINAL:
        return {"status": "batch_pending", "batch_id": batch_id, "written": [],
                "detail": "Dávka se ještě zpracovává. Dokončit ji můžete později."}
    if state.get("generate_batch"):
        return process_saved_batch(client, run_dir, batch_id, settings, batch=batch, progress=progress)
    target = state.get("out_dir")
    if not target:
        raise ContractError("Běh nemá cílový adresář OUT.")
    expected = [f"{Path(run_dir).name}_C1"]
    raw_files = []
    response_dir = Path(run_dir) / "responses"
    response_dir.mkdir(exist_ok=True)
    if progress:
        progress(ProgressEvent("Stahování výsledků", detail="Stahuji výstupní a chybové JSONL."))
    for key in ("output_file_id", "error_file_id"):
        if batch.get(key):
            raw = client.file_content(batch[key])
            Path(safe_join_under_root(str(response_dir), f"{batch_id}_{key}.jsonl")).write_bytes(raw)
            raw_files.append(raw)
    previous = (state.get("batch_imports") or {}).get(batch_id, {})
    result = import_bundle(b"\n".join(raw_files), target, previous.get("hashes"), expected, "C_FILES_ALL", progress=progress)
    result["import_status"] = result["status"]
    state.setdefault("batch_imports", {})[batch_id] = result
    state["status"] = result["status"]
    state.setdefault("batch_records", {})[batch_id] = batch
    if progress:
        progress(ProgressEvent("Aktualizace evidence", detail="Ukládám stav importu."))
    atomic_write_text(str(Path(run_dir) / "run_state.json"), json.dumps(state, ensure_ascii=False, indent=2))
    return result
