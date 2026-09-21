from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING, Any

from ..batch_submit import response_batch_submit_payload, submit_verified_batch
from ..contracts import ContractError
from ..generate_batch import encode_requests
from ..orchestration.batch_manifest import from_file_manifest, transition
from ..orchestration.provider_operations import (
    bind_physical_request,
    mark_not_submitted,
    mark_submission,
    mark_submission_started,
    prepare_batch,
)
from ..orchestration.work_order import WorkOrder, work_order_from_mapping
from ..progress import ProgressEvent

if TYPE_CHECKING:
    from .context import RunContext


def _work_orders(manifest: dict[str, Any]) -> dict[str, WorkOrder]:
    result: dict[str, WorkOrder] = {}
    for custom_id, raw in (manifest.get("work_orders") or {}).items():
        if not isinstance(raw, dict):
            raise ContractError(f"BATCH {custom_id}: neplatný WORK_ORDER_V2.")
        try:
            order = work_order_from_mapping(raw)
            if (
                "attempt_id" in raw
                and str(raw.get("order_hash") or "") != order.order_hash
            ):
                raise ContractError(f"BATCH {custom_id}: WorkOrder hash nesouhlasí.")
            result[str(custom_id)] = order
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(f"BATCH {custom_id}: neplatný WORK_ORDER_V2.") from exc
    if set(result) != {str(row["custom_id"]) for row in manifest.get("requests", [])}:
        raise ContractError("BATCH WorkOrder mapování neodpovídá pracovním položkám.")
    return result


def _save_v4(log, manifest_v4: dict[str, Any]) -> None:
    import json
    from pathlib import Path

    log.save_json(
        "manifests",
        f"batch_manifest_v4_{manifest_v4['manifest_id']}",
        manifest_v4,
    )
    try:
        state = json.loads(Path(log.state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    manifests = dict(state.get("batch_manifests_v4") or {})
    manifests[manifest_v4["manifest_id"]] = manifest_v4
    log.update_state(
        {
            "batch_manifest_v4": manifest_v4,
            "batch_manifests_v4": manifests,
        }
    )


def _submit_generate_batch(self: RunContext, client, manifest):
    from ..orchestration.repository import repository_for_logger
    from ..recoverable_artifacts import load_run_state

    current_state = load_run_state(self.log.paths.run_dir)
    if (
        current_state.get("submission_unknown")
        or current_state.get("status") == "submission_unknown"
    ):
        raise ContractError(
            "Předchozí neurčitý submit musí být dohledán před dalším odesláním."
        )
    if manifest.get("version") != 3:
        raise ContractError(
            "Nové odeslání legacy snapshotové dávky je zakázáno; "
            "je nutná explicitní příprava FileContext."
        )

    work_orders = _work_orders(manifest)
    encode_requests(manifest)
    manifest_v4 = from_file_manifest(self.log.run_id, manifest)
    _save_v4(self.log, manifest_v4)

    # Obnovený checkpoint může pocházet z odděleného procesu importu a jeho
    # WorkOrder proto nemusí mít parent run už zapsaný v lokální databázi.
    # WorkOrder zůstává immutable; pouze doplníme důvěryhodný parent z jeho
    # vlastní identity, aby následná FK vazba nebyla nahrazena nečitelnou
    # chybou při registraci provider operace.
    repo = repository_for_logger(self.log)
    for order in work_orders.values():
        if repo.has_run(order.run_id):
            continue
        repo.register_run(
            order.run_id,
            lineage_id=order.run_id,
            scope_hash=str(manifest.get("snapshot_hash") or order.source_snapshot_hash),
            policy_hash=order.policy_hash,
            config=dict(current_state.get("run_config_v2") or {}),
            approval_id=order.approval_id,
            status="running",
        )

    prepare_batch(
        self.log,
        self.cfg,
        manifest["requests"],
        manifest["context_reports"],
        work_orders=work_orders,
    )
    total_input = sum(
        r["input_tokens"] for r in manifest["context_reports"]
    )
    self.progress_event.emit(
        ProgressEvent(
            "Kontext BATCH",
            detail=(
                f"{len(manifest['requests'])} úloh · odhad vstupu "
                f"{total_input:,} tokenů · {manifest['requests'][0]['body']['model']}"
            ),
        )
    )
    self._verify_completed_files()
    completed = {
        path: value
        for path, value in (getattr(self.cfg, "completed_hashes", None) or {}).items()
        if path in (self.cfg.skip_paths or [])
        and path in manifest.get("omitted", [])
    }
    if completed:
        manifest["completed_hashes"] = completed
        manifest["omitted"] = [
            path for path in manifest["omitted"] if path not in completed
        ]

    encode_requests(manifest)
    for row in manifest["requests"]:
        client.validate_access(row["body"], batch=True)
    data = encode_requests(manifest)
    path = os.path.join(self.log.paths.requests_dir, "generate_batch.jsonl")
    with open(path, "wb") as stream:
        stream.write(data)
    self.log.update_state(
        {
            "generate_batch": manifest,
            "generate_batch_adapter_version": 3,
            "status": "batch_prepared",
        }
    )
    self._check_stop()
    self._set(
        45,
        0,
        "Lokálně kontroluji a nahrávám pracovní BATCH…",
        stage="Příprava BATCH",
    )
    uploaded = client.upload_file(path, purpose="batch")
    input_file_id = str(uploaded["id"])
    manifest_v4 = transition(
        manifest_v4, "input_uploaded", input_file_id=input_file_id
    )
    _save_v4(self.log, manifest_v4)
    evidence = {
        "batch_input_file_id": input_file_id,
        "submission_input_file_id": input_file_id,
        "submission_endpoint": "/v1/responses",
        "submission_jsonl_sha256": hashlib.sha256(data).hexdigest(),
        "batch_manifest_v4_id": manifest_v4["manifest_id"],
    }
    self.log.update_state(evidence)
    submit_payload = response_batch_submit_payload(input_file_id)
    for order in work_orders.values():
        bind_physical_request(
            self.log,
            order,
            submit_payload,
            remote_input_file_id=input_file_id,
        )

    self._set(55, 0, "Odesílám pracovní dávku…", stage="BATCH SUBMIT")
    manifest_v4 = transition(manifest_v4, "submitting")
    _save_v4(self.log, manifest_v4)
    self.log.update_state({"submission_unknown": True})
    for order in work_orders.values():
        mark_submission_started(self.log, order)
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
            for order in work_orders.values():
                mark_not_submitted(self.log, order)
            manifest_v4 = transition(manifest_v4, "failed")
            _save_v4(self.log, manifest_v4)
            self.log.update_state({"submission_unknown": False})
        else:
            for order in work_orders.values():
                mark_submission(self.log, order, None, unknown=True)
            manifest_v4 = transition(manifest_v4, "submission_unknown")
            _save_v4(self.log, manifest_v4)
            self.log.update_state(
                {
                    "status": "submission_unknown",
                    "submission_unknown": True,
                }
            )
        raise

    batch_id = str(batch.get("id") or "")
    if (
        str(batch.get("input_file_id") or input_file_id) != input_file_id
        or str(batch.get("endpoint") or "/v1/responses") != "/v1/responses"
    ):
        for order in work_orders.values():
            mark_submission(self.log, order, None, unknown=True)
        manifest_v4 = transition(manifest_v4, "submission_unknown")
        _save_v4(self.log, manifest_v4)
        raise ContractError(
            "BATCH provider odpověď neodpovídá zmrazenému input_file_id/endpointu."
        )
    if not batch_id:
        for order in work_orders.values():
            mark_submission(self.log, order, None, unknown=True)
        manifest_v4 = transition(manifest_v4, "submission_unknown")
        _save_v4(self.log, manifest_v4)
        raise ContractError(
            "BATCH submit nemá potvrzené provider ID; automatické opakování je zablokováno."
        )

    for order in work_orders.values():
        mark_submission(self.log, order, batch_id, unknown=False)
    manifest_v4 = transition(
        manifest_v4,
        "submitted",
        provider_batch_id=batch_id,
    )
    _save_v4(self.log, manifest_v4)
    self.log.update_state(
        {
            "batch_id": batch_id,
            "status": "batch_pending",
            "submission_unknown": False,
            "batch_records": {batch_id: batch},
        }
    )
    self.log.save_json("manifests", "generate_batch_created", batch)
    self._set(
        100,
        0,
        "Příprava dokončena; souborové úlohy čekají na zpracování dávky.",
        stage="Čekání na dávku",
    )
    return {
        "mode": manifest.get("mode", "GENERATE"),
        "batch_id": batch_id,
        "input_file_id": input_file_id,
        "batch_manifest_id": manifest_v4["manifest_id"],
        "status": "batch_pending",
        "files": len(manifest["expected"]),
    }


# Režim MODIFY.
