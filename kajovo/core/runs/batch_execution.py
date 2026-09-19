from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

from ..batch_submit import submit_verified_batch
from ..contracts import (
    ContractError,
)
from ..generate_batch import encode_requests
from ..progress import ProgressEvent

if TYPE_CHECKING:
    from .context import RunContext

def _submit_generate_batch(self: RunContext, client, manifest):
    from ..cost_context_report import CostContextReport
    from ..recoverable_artifacts import load_run_state
    current_state = load_run_state(self.log.paths.run_dir)
    if current_state.get("submission_unknown") or current_state.get("status") == "submission_unknown":
        raise ContractError("Předchozí neurčitý submit musí být dohledán před dalším odesláním.")
    if manifest.get("version") != 3:
        raise ContractError("Nové odeslání legacy snapshotové dávky je zakázáno; je nutná explicitní příprava FileContext.")
    report = CostContextReport(self.log.paths.run_dir)
    from ..orchestration.ledger import reserve_batch
    reserve_batch(
        self.log, self.cfg, client,
        manifest["requests"], manifest["cost_context_reports"],
    )
    for row, measurement in zip(manifest["requests"], manifest["cost_context_reports"], strict=True):
        report.record(row["body"], custom_id=row["custom_id"],
                      path=manifest["expected"][row["custom_id"]], measurement=measurement)
    total_input = sum(r["input_tokens"] for r in manifest["cost_context_reports"])
    self.progress_event.emit(ProgressEvent("Kontext BATCH", detail=
        f"{len(manifest['requests'])} úloh · odhad vstupu {total_input:,} tokenů · "
        f"{manifest['requests'][0]['body']['model']} · podrobnosti v cost_context_report.json"))
    self._verify_completed_files()
    completed = {path: value for path, value in (getattr(self.cfg, "completed_hashes", None) or {}).items()
                 if path in (self.cfg.skip_paths or []) and path in manifest.get("omitted", [])}
    if completed:
        manifest["completed_hashes"] = completed
        manifest["omitted"] = [path for path in manifest["omitted"] if path not in completed]
    encode_requests(manifest)
    for row in manifest["requests"]:
        client.validate_access(row["body"], batch=True)
    data = encode_requests(manifest)
    path = os.path.join(self.log.paths.requests_dir, "generate_batch.jsonl")
    with open(path, "wb") as stream:
        stream.write(data)
    self.log.update_state({"generate_batch": manifest, "status": "batch_prepared"})
    self._check_stop()
    self._set(45, 0, "Lokálně kontroluji a nahrávám pracovní BATCH…", stage="Příprava BATCH")
    # Nahrává se přímo skutečný pracovní JSONL. upload_file provede pouze
    # lokální validaci obsahu a nevytváří žádnou zkušební dávku.
    uploaded = client.upload_file(path, purpose='batch')
    evidence = {
        "batch_input_file_id": uploaded["id"],
        "submission_input_file_id": uploaded["id"],
        "submission_endpoint": "/v1/responses",
        "submission_jsonl_sha256": hashlib.sha256(data).hexdigest(),
    }
    self.log.update_state(evidence)
    self._set(55, 0, "Odesílám pracovní dávku…", stage="BATCH SUBMIT")
    self.log.update_state({"submission_unknown": True})
    batch = submit_verified_batch(client, uploaded["id"], manifest["requests"])
    self.log.update_state({"batch_id": batch["id"], "status": "batch_pending", "submission_unknown": False,
                           "batch_records": {batch["id"]: batch}})
    self.log.save_json("manifests", "generate_batch_created", batch)
    self._set(100, 0, "Příprava dokončena; souborové úlohy čekají na zpracování dávky.", stage="Čekání na dávku")
    return {"mode": manifest.get("mode", "GENERATE"), "batch_id": batch["id"], "input_file_id": uploaded["id"],
            "status": "batch_pending", "files": len(manifest["expected"])}

# Režim MODIFY.
