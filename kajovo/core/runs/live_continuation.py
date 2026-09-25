"""Převzetí zmrazené LIVE operace do potomka bez nového odeslání."""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

from ..contracts import ContractError, parse_json_strict
from ..delivery_preparation import digest
from ..recoverable_artifacts import artifact_path, load_run_state
from ..utils import atomic_write_text
from .locking import ExecutionLock


def pending_live(state):
    pending = state.get("response_pending")
    return bool(state.get("live_replay_pending")) or (
        bool(state.get("live_continuation")) and not state.get("live_replay_complete")
    ) or state.get("status") == "response_pending" or (
        isinstance(pending, dict) and pending.get("status") in {"submitting", "queued", "in_progress"}
    )


def _artifact(root, name):
    path = artifact_path(root, name)
    if not path:
        raise ContractError(f"Continue LIVE: chybí zmrazená evidence {name}; nové generování je zakázáno.")
    value = parse_json_strict(Path(path).read_text(encoding="utf-8"))
    return validate_live_continuation(value, target_run_id=Path(root).name) if name == "manifests/live_continuation" else value


def validate_live_continuation(value, *, target_run_id=None):
    """Uzavřená lokální obálka V1; vnořené pracovní kontrakty mají vlastní validátory."""
    from ..openai_client import OpenAIClient
    from ..orchestration.request_binding import validate_response_work_order
    from ..orchestration.work_order import work_order_from_mapping
    from .recovery import _validate_runtime_manifest

    fields = {"version", "source_run_id", "target_run_id", "pending_hash", "response_id",
              "journal", "work_orders", "provider_operations", "runtime", "source_pack",
              "source_state", "source_upload_ids"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ContractError("Continue LIVE: manifest má neplatnou množinu polí.")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ContractError("Continue LIVE: nepodporovaná verze manifestu.")
    for field in ("source_run_id", "target_run_id", "response_id"):
        try:
            OpenAIClient._validate_resource_id(value[field])
        except ValueError as exc:
            raise ContractError(f"Continue LIVE: neplatné {field}.") from exc
    if (value["source_run_id"] == value["target_run_id"]
            or (target_run_id is not None and value["target_run_id"] != target_run_id)):
        raise ContractError("Continue LIVE: manifest neodpovídá lineage potomka.")
    for field in fields - {"version", "source_run_id", "target_run_id", "pending_hash", "response_id"}:
        if not isinstance(value[field], dict):
            raise ContractError(f"Continue LIVE: {field} musí být objekt.")
    journal = value["journal"]
    if (set(journal) != {"version", "entries"} or type(journal["version"]) is not int
            or journal["version"] != 1 or not isinstance(journal["entries"], dict)):
        raise ContractError("Continue LIVE: neplatná obálka journalu.")
    entries = journal["entries"]
    key = value["pending_hash"]
    if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None or key not in entries:
        raise ContractError("Continue LIVE: neplatný pending hash.")
    if set(entries) != set(value["work_orders"]) or set(entries) != set(value["provider_operations"]):
        raise ContractError("Continue LIVE: journal a provider operace nemají shodné identity.")
    for request_hash, entry in entries.items():
        if (not isinstance(request_hash, str) or re.fullmatch(r"[0-9a-f]{64}", request_hash) is None
                or not isinstance(entry, dict) or not isinstance(entry.get("payload"), dict)
                or digest(entry["payload"]) != request_hash
                or not isinstance(entry.get("status"), str)
                or type(entry.get("created_at")) not in {int, float}):
            raise ContractError("Continue LIVE: neplatný typ nebo hash položky journalu.")
        try:
            OpenAIClient._validate_resource_id(entry.get("id"))
            raw_order = value["work_orders"][request_hash]
            if not isinstance(raw_order, dict) or not isinstance(raw_order.get("order_hash"), str):
                raise ValueError("Chybí hash WorkOrderu.")
            order = work_order_from_mapping(raw_order)
            validate_response_work_order(order, entry["payload"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ContractError("Continue LIVE: neplatná identita odpovědi nebo WorkOrder.") from exc
        operation = value["provider_operations"][request_hash]
        if (not isinstance(operation, dict)
                or set(operation) != {"attempt_id", "endpoint", "request_hash", "state", "provider_id"}
                or any(not isinstance(item, str) for item in operation.values())
                or operation["request_hash"] != request_hash or operation["provider_id"] != entry["id"]
                or operation["attempt_id"] != order.attempt_id or operation["endpoint"] != "/v1/responses"
                or order.provider_endpoint != operation["endpoint"] or order.route != "responses_live"
                or operation["state"] not in {"submitted", "completed"}):
            raise ContractError("Continue LIVE: neplatná provider operace.")
    pending = entries[key]
    state_pending = value["source_state"].get("response_pending") or value["source_state"].get("live_replay_pending")
    local_pending = value["source_state"].get("live_replay_pending") or {}
    completed_replay = (pending["status"] == "completed" and isinstance(local_pending, dict)
                        and local_pending.get("hash") == key and local_pending.get("id") == pending["id"])
    if (pending["id"] != value["response_id"] or not (pending["status"] in {"queued", "in_progress"} or completed_replay)
            or not isinstance(state_pending, dict) or state_pending.get("hash") != key
            or state_pending.get("id") != pending["id"]):
        raise ContractError("Continue LIVE: manifest nedokládá původní pending odpověď.")
    _validate_runtime_manifest(copy.deepcopy(value["runtime"]))
    _source_pack(value)
    for source_id, provider_id in value["source_upload_ids"].items():
        if not isinstance(source_id, str) or not source_id:
            raise ContractError("Continue LIVE: neplatná identita přílohy.")
        try:
            OpenAIClient._validate_resource_id(provider_id)
        except ValueError as exc:
            raise ContractError("Continue LIVE: neplatné provider ID přílohy.") from exc
    return value


def _inherited_order(root, request_hash, item):
    """Ověří nepřerušený řetězec archivovaných předání až k vlastníkovi práce."""
    path = artifact_path(root, "manifests/live_continuation")
    if not path:
        return None
    inherited = _artifact(root, "manifests/live_continuation")
    order = inherited.get("work_orders", {}).get(request_hash)
    if order is None:
        return None
    current = root
    visited = set()
    operation = inherited.get("provider_operations", {}).get(request_hash)
    if (not isinstance(operation, dict) or operation.get("request_hash") != request_hash
            or operation.get("provider_id") != item.get("id")
            or operation.get("attempt_id") != order.get("attempt_id")
            or operation.get("endpoint") != order.get("provider_endpoint")):
        raise ContractError("Continue LIVE: lineage nedokládá identitu provider operace.")
    while current.name != order.get("run_id"):
        if current.name in visited:
            raise ContractError("Continue LIVE: cyklus v původu provider operace.")
        visited.add(current.name)
        handoff = _artifact(current, "manifests/live_continuation")
        source = handoff.get("source_run_id")
        if (handoff.get("target_run_id") != current.name
                or not isinstance(source, str) or not source or source in {".", ".."}
                or Path(source).name != source or "/" in source or "\\" in source
                or handoff.get("work_orders", {}).get(request_hash) != order):
            raise ContractError("Continue LIVE: WorkOrder nemá doloženou lineage vlastníka.")
        bound = handoff.get("provider_operations", {}).get(request_hash)
        if not isinstance(bound, dict) or not isinstance(operation, dict) or any(
            bound.get(field) != operation.get(field)
            for field in ("attempt_id", "endpoint", "request_hash", "provider_id")
        ):
            raise ContractError("Continue LIVE: lineage změnila identitu provider operace.")
        frozen = handoff.get("journal", {}).get("entries", {}).get(request_hash, {})
        if frozen.get("payload") != item.get("payload") or frozen.get("id") != item.get("id"):
            raise ContractError("Continue LIVE: lineage změnila zmrazený požadavek.")
        current = (root.parent / source).resolve()
        if current.parent != root.parent:
            raise ContractError("Continue LIVE: původ práce není v původním adresáři LOG.")
        original = _artifact(current, "manifests/response_journal").get("entries", {}).get(request_hash, {})
        if original.get("payload") != item.get("payload") or original.get("id") != item.get("id"):
            raise ContractError("Continue LIVE: původní vlastník nedokládá převzatý požadavek.")
    return order


def read_evidence(root):
    """Čte pouze doložené podklady; nezakládá databázi ani nemění rodiče."""
    root = Path(root).resolve()
    state = load_run_state(root)
    pending = state.get("response_pending") or state.get("live_replay_pending") or {}
    journal = _artifact(root, "manifests/response_journal")
    entries = journal.get("entries")
    if journal.get("version") != 1 or not isinstance(entries, dict):
        raise ContractError("Continue LIVE: neplatný journal.")
    key = pending.get("hash")
    entry = entries.get(key)
    if not isinstance(entry, dict) or not pending.get("id") or entry.get("id") != pending["id"]:
        raise ContractError("Continue LIVE: chybí jednoznačná vazba Response ID na journal.")
    local_pending = state.get("live_replay_pending") or {}
    completed_replay = (entry.get("status") == "completed" and isinstance(local_pending, dict)
                        and local_pending.get("hash") == key and local_pending.get("id") == entry.get("id"))
    if entry.get("status") not in {"queued", "in_progress"} and not completed_replay:
        raise ContractError("Continue LIVE: journal nedokládá rozpracovanou odpověď.")
    database = root.parent / "orchestration.sqlite3"
    if not database.is_file():
        raise ContractError("Continue LIVE: chybí databáze identity provider operací; nový POST je zakázán.")
    orders = {}
    operations = {}
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        for request_hash, item in entries.items():
            if not isinstance(item, dict) or digest(item.get("payload")) != request_hash:
                raise ContractError("Continue LIVE: porušený hash zmrazeného payloadu.")
            inherited_order = _inherited_order(root, request_hash, item)
            owner_run_id = inherited_order["run_id"] if inherited_order else root.name
            rows = db.execute(
                "SELECT w.work_order_json,w.work_order_hash,p.attempt_id,p.endpoint,"
                "p.request_hash,p.state,p.provider_id FROM provider_operations p "
                "JOIN work_orders w ON w.work_order_hash=p.work_order_hash "
                "WHERE p.request_hash=? AND p.provider_id=? AND w.run_id=?",
                (request_hash, item.get("id"), owner_run_id),
            ).fetchall()
            if len(rows) != 1:
                raise ContractError("Continue LIVE: chybí jednoznačná provider operace a pracovní fáze.")
            raw, order_hash, attempt_id, endpoint, body_hash, phase, provider_id = rows[0]
            from ..orchestration.request_binding import validate_response_work_order
            from ..orchestration.work_order import work_order_from_mapping
            order = work_order_from_mapping({**parse_json_strict(raw), "order_hash": order_hash})
            if order.run_id != owner_run_id or (inherited_order is not None and
                    inherited_order != {**order.to_dict(), "order_hash": order_hash}):
                raise ContractError("Continue LIVE: provider operace nepatří doloženému vlastníkovi.")
            validate_response_work_order(order, item["payload"])
            if (endpoint != "/v1/responses" or order.route != "responses_live"
                    or order.attempt_id != attempt_id or phase not in {"submitted", "completed"}):
                raise ContractError("Continue LIVE: provider operace nemá potvrzený LIVE submit.")
            orders[request_hash] = {**order.to_dict(), "order_hash": order_hash}
            operations[request_hash] = {
                "attempt_id": attempt_id, "endpoint": endpoint, "request_hash": body_hash,
                "state": phase, "provider_id": provider_id,
            }
    runtime = _artifact(root, "manifests/response_runtime")
    from .recovery import _validate_runtime_manifest
    _validate_runtime_manifest(runtime)
    # Poslední přípravný checkpoint je novější než počáteční runtime manifest.
    runtime["preparation_snapshot"] = copy.deepcopy(state.get("preparation_snapshot"))
    source_pack = _artifact(root, "manifests/source_pack_v1")
    if digest(source_pack) != state.get("source_pack_hash"):
        raise ContractError("Continue LIVE: SourcePack má neplatný otisk.")
    source_upload_ids: dict[str, str] = {}
    index = parse_json_strict((root / "artifacts" / "index.json").read_text("utf-8"))
    for name in index["entries"]:
        if name.startswith("manifests/source_delivery_"):
            for source in _artifact(root, name).get("sources", []):
                source_id, provider_id = source["source_id"], source["provider_file_id"]
                if source_id in source_upload_ids and source_upload_ids[source_id] != provider_id:
                    raise ContractError("Continue LIVE: příloha má nejednoznačnou provider identitu.")
                source_upload_ids[source_id] = provider_id
    return {
        "version": 1, "source_run_id": root.name, "pending_hash": key,
        "response_id": pending["id"], "journal": journal, "work_orders": orders,
        "provider_operations": operations, "runtime": runtime, "source_pack": source_pack,
        "source_state": state, "source_upload_ids": source_upload_ids,
    }


@contextmanager
def response_claim(log_root, response_id, run_id, *, source_run_id=None, require_owner=False):
    """Procesní zámek a trvalý vlastník leží mimo neměnné Run Bundly."""
    root = Path(log_root) / ".response_claims"
    key = digest({"response_id": response_id})
    with ExecutionLock(root / (key + ".lock")):
        path = root / (key + ".json")
        owner = parse_json_strict(path.read_text("utf-8")) if path.exists() else {}
        if require_owner and not owner:
            raise ContractError("Continue LIVE: předání potomkovi nebylo dokončeno; pokračujte z rodiče.")
        if owner and owner.get("response_id") != response_id:
            raise ContractError("Continue LIVE: claim nepatří požadované odpovědi.")
        allowed = {run_id} if source_run_id is None else {source_run_id, run_id}
        if owner and owner.get("run_id") not in allowed:
            raise ContractError(
                f"Continue LIVE: odpověď již převzal běh {owner.get('run_id')}; pokračujte z něj."
            )
        yield
        if source_run_id is not None:
            atomic_write_text(str(path), json.dumps({"response_id": response_id, "run_id": run_id}))


def inherit_pending(adapter, logger, evidence, *, ui_state=None):
    """Předá vlastnictví až po trvalém uložení podkladů pod společným zámkem."""
    manifest = validate_live_continuation({**evidence, "target_run_id": logger.run_id}, target_run_id=logger.run_id)
    if Path(logger.paths.run_dir).resolve().parent != Path(adapter.root).resolve().parent:
        raise ContractError("Continue LIVE vyžaduje původní adresář LOG a jeho evidenci provider operací.")
    with response_claim(Path(logger.paths.run_dir).parent, evidence["response_id"],
                        logger.run_id, source_run_id=adapter.run_id):
        # Znovu ověřit aktuální identitu uvnitř zámku sdíleného s pollingem.
        current = read_evidence(adapter.root)
        if current != evidence:
            raise ContractError("Continue LIVE: podklady se od náhledu změnily.")
        from ..utils import safe_join_under_root
        input_ids = []
        for artifact in adapter.bundle.artifacts():
            if (artifact.get("metadata") or {}).get("source_id"):
                path = safe_join_under_root(str(adapter.root), artifact["path_in_bundle"])
                archived = logger.bundle.archive_artifact(
                    path, role=artifact.get("role") or "source", kind=artifact.get("kind") or "source_pack_input",
                    reconstruction_role=artifact.get("reconstruction_role") or "",
                    metadata=artifact.get("metadata") or {},
                )
                if artifact.get("role") == "in_project_file":
                    input_ids.append(archived["artifact_id"])
        logger.save_json("manifests", "live_continuation", manifest)
        logger.save_json("manifests", "response_journal", evidence["journal"])
        logger.save_json("manifests", "response_runtime", evidence["runtime"])
        logger.save_json("manifests", "source_pack_v1", evidence["source_pack"])
        logger.save_json("manifests", "source_delivery_inherited", {
            "sources": [{"source_id": key, "provider_file_id": value}
                        for key, value in evidence["source_upload_ids"].items()],
        })
        snapshot = evidence["runtime"]["preparation_snapshot"] or {}
        expectations = evidence["source_state"].get("production_expected_target_hashes")
        if expectations is not None and snapshot.get("graph"):
            logger.update_state({
                "inherited_target_expectations": copy.deepcopy(expectations),
                "inherited_graph_hash": digest(snapshot["graph"]),
            })
        logger.update_state({
            "mode": evidence["source_state"].get("mode"),
            "ui_state": copy.deepcopy(ui_state or evidence["source_state"].get("ui_state") or {}),
            "input_archive": {"version": 1, "complete": True, "artifact_ids": input_ids},
            "preparation_snapshot": copy.deepcopy(evidence["runtime"]["preparation_snapshot"]),
            "source_pack_hash": digest(evidence["source_pack"]),
            "live_continuation": True,
            "live_replay_complete": False,
            "live_replay_pending": {"hash": evidence["pending_hash"], "id": evidence["response_id"]},
            "response_pending": copy.deepcopy(evidence["source_state"].get("response_pending")
                                              or evidence["source_state"].get("live_replay_pending")),
            "response_file_ids": copy.deepcopy(evidence["source_state"].get("response_file_ids", {})),
            "status": "response_pending",
        })
        read_evidence(logger.paths.run_dir)
        restore_sources(SimpleNamespace(log=logger), evidence)


def _source_pack(evidence):
    from ..orchestration.source_pack import FrozenSource, SourcePack, SourceSegment
    raw = evidence["source_pack"]
    try:
        if (type(raw["version"]) is not int or raw["version"] != 1
                or type(raw["scope_revision"]) is not int or raw["scope_revision"] < 1
                or not isinstance(raw["pack_id"], str) or not raw["pack_id"]
                or not isinstance(raw["sources"], list)):
            raise ValueError("Neplatná verze nebo typ SourcePacku.")
        pack = SourcePack(**{**raw, "sources": tuple(
            FrozenSource(**{**row, "segments": tuple(SourceSegment(**segment) for segment in row["segments"])})
            for row in raw["sources"]
        )})
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("Continue LIVE: neplatný SourcePack.") from exc
    if pack.hash != evidence["source_state"].get("source_pack_hash"):
        raise ContractError("Continue LIVE: SourcePack neodpovídá původnímu běhu.")
    return pack


def restore_sources(worker, evidence):
    from ..orchestration.source_pack import source_context
    pack = _source_pack(evidence)
    worker.source_pack = pack
    worker.source_context = source_context(worker.log, pack)
    worker._source_upload_ids = copy.deepcopy(evidence.get("source_upload_ids") or {})
    return pack
