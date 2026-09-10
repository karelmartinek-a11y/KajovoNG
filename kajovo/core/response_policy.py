"""Pevná pravidla a oddělené zkušební požadavky na skutečný endpoint."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import time
import uuid

from .model_registry import matrix_version, model_spec
from .request_rules import validate_response_payload
from .structured_output import prepare_payload, validate_output
from .utils import atomic_write_text


class PreflightPending(RuntimeError):
    """Dávka dosud neskončila; další spuštění naváže na stejné vzdálené ID."""


class PreflightTransport:
    def __init__(self, client):
        self.client = client

    def validate_access(self, payload, batch=False):
        prepare_payload(payload)
        validate_response_payload(payload, batch=batch)

    def count_input_tokens(self, payload):
        return self.client.count_input_tokens(payload, _preflight=True)

    def create_response(self, payload):
        self.validate_access(payload)
        return self.client._send_response(payload)


def rejection(model, exc, batch=False):
    """Zachová pole API; chyba účtu či souboru se nevydává za chybu matice."""
    from .openai_client import OpenAIError
    param = getattr(exc, "param", None)
    code = getattr(exc, "code", None)
    mismatch = code in ("unsupported_parameter", "unsupported_value", "unknown_parameter")
    reason = "Rozpor s pevnou maticí" if mismatch else "Zkušební požadavek selhal"
    error = OpenAIError(
        f"{reason}: model={model}, režim={'BATCH' if batch else 'LIVE'}, "
        f"parametr={param or 'API neuvedlo'}, kód={code or 'API neuvedlo'}. {exc}",
        getattr(exc, "status_code", None), param=param, code=code)
    error.request_id = getattr(exc, "request_id", None)
    error.phase = "preflight_batch" if batch else "preflight_live"
    return error


class ResponsePolicy:
    def __init__(self, client, cache_dir="cache", log_dir="LOG"):
        self.client = client
        identity = client.base_url.rstrip("/") + "\0" + client.api_key + "\0" + matrix_version()
        self.identity = hashlib.sha256(identity.encode()).hexdigest()
        self.path = Path(cache_dir) / "response_validation" / (self.identity + ".json")
        self.log_dir = Path(log_dir)
        self.catalog = None
        self.proofs = {}
        self._live_verified = set()
        try:
            root = json.loads(self.path.read_text(encoding="utf-8"))
            if root.get("version") == matrix_version() and root.get("identity") == self.identity:
                self.proofs = root.get("proofs", {})
        except (OSError, ValueError, TypeError):
            pass

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(self.path), json.dumps({"version": matrix_version(), "identity": self.identity,
            "proofs": self.proofs}, ensure_ascii=False))

    def invalidate(self, model):
        self._live_verified.clear()
        self.catalog = None

    def model_spec(self, model):
        return model_spec(model)

    def key(self, payload, batch=False):
        return hashlib.sha256(json.dumps({"payload": payload, "batch": batch, "matrix": matrix_version()},
            sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()

    def check_documented(self, payload, batch=False):
        prepare_payload(payload)
        validate_response_payload(payload, batch=batch)
        if self.catalog is None:
            self.catalog = {row["id"] for row in self.client.list_models()}
        if payload["model"] not in self.catalog:
            raise ValueError(f"{payload['model']}: model není v aktuálním katalogu účtu.")
        if self.model_spec(payload["model"])["canonical"] == "gpt-6-astra" and "eu.api.openai.com" in self.client.base_url and payload.get("service_tier") == "priority":
            raise ValueError("gpt-6-astra: service_tier=priority není podporován s EU data residency.")
        return self.model_spec(payload["model"])

    def ensure(self, payload, batch=False, preparing=False):
        self.check_documented(payload, batch)
        # Příprava může ještě obsahovat zástupná ID. Zkušební volání patří až hotovému payloadu.
        if preparing or batch:
            return
        self.client.validate_resources(payload)
        key = self.key(payload)
        if key in self._live_verified:
            return
        trial = copy.deepcopy(payload)
        try:
            result = self.trial_live(trial)
            if trial != payload:
                raise ValueError("Nastavení se při zkušebním volání změnilo; pracovní požadavek nebyl odeslán.")
            self._live_verified.add(key)
            self.proofs[key] = {"model": payload["model"], "state": "verified", "tested_at": time.time(),
                "endpoint": "/v1/responses", "response_id": result.get("id")}
        except Exception as exc:
            self.proofs[key] = {"model": payload["model"], "state": "failed", "tested_at": time.time(), "error": str(exc)}
            self.save()
            raise rejection(payload["model"], exc) from exc
        self.save()

    def consume_live(self, payload):
        self._live_verified.discard(self.key(payload))

    def trial_live(self, payload):
        from .runlog import RunLogger
        log = RunLogger(str(self.log_dir), "PREFLIGHT_" + uuid.uuid4().hex, "Zkušební volání")
        log.save_json("requests", "preflight", payload)
        try:
            adapter = PreflightTransport(self.client)
            controller = getattr(self.client, "cost_control", None)
            result = controller.execute(adapter, payload, stage="PREFLIGHT") if controller else adapter.create_response(payload)
            log.save_json("responses", "preflight", result)
            self.record_usage(result, payload, log.run_id, False)
            validate_output(result, payload)
            log.update_state({"status": "completed", "model": payload["model"], "response_id": result.get("id")})
            return result
        except Exception as exc:
            log.update_state({"status": "failed", "model": payload["model"], "error": str(exc)})
            raise

    def record_usage(self, result, payload, run_id, batch):
        if not getattr(self, "db_path", None) or (not batch and getattr(self.client, "cost_control", None)):
            return
        from .receipt import Receipt, ReceiptDB
        usage = result.get("usage") or {}
        ReceiptDB(self.db_path).insert(Receipt(run_id, time.time(), "Zkušební volání", result.get("model") or payload["model"],
            "PREFLIGHT", "PREFLIGHT_BATCH" if batch else "PREFLIGHT", result.get("id"), None,
            usage.get("input_tokens", 0), usage.get("output_tokens", 0), None, None, None, batch,
            "Spotřeba zkušebního volání; cena nebyla potvrzena ceníkem.", {}, usage))

    def ensure_batch(self, payloads):
        """Jedna skutečná zkušební dávka pro všechny dosud neověřené řádky."""
        by_key = {}
        for payload in payloads:
            self.check_documented(payload, batch=True)
            self.client.validate_resources(payload)
            key = self.key(payload, True)
            by_key[key] = payload
            proof = self.proofs.get(key, {})
            if proof.get("state") == "verified" and time.time() - proof.get("tested_at", 0) > 3600:
                self.proofs.pop(key)
        missing = {key: body for key, body in by_key.items() if key not in self.proofs}
        if missing:
            trial_id = uuid.uuid4().hex
            controller = getattr(self.client, "cost_control", None)
            operation = None
            if controller is not None:
                copies = copy.deepcopy(list(missing.values()))
                operation, _ = controller.prepare(PreflightTransport(self.client), copies,
                    batch=True, stage="PREFLIGHT_BATCH", custom_ids=list(missing))
                if copies != list(missing.values()):
                    controller.ledger.mark(operation, "released")
                    raise ValueError("Limit výstupu zkušební dávky se změnil; připravte pracovní JSONL se stejným limitem.")
            rows = [{"custom_id": key, "method": "POST", "url": "/v1/responses", "body": body} for key, body in missing.items()]
            data = ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode()
            if len(data) > 200_000_000:
                if operation is not None:
                    controller.ledger.mark(operation, "released")
                raise ValueError("Zkušební JSONL překračuje 200 MB; rozdělte dávku.")
            trial_dir = self.log_dir / ("PREFLIGHT_BATCH_" + trial_id)
            trial_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_text(str(trial_dir / "input.jsonl"), data.decode("utf-8"))
            # Soubory byly plně validovány před vstupem do transportu.
            try:
                uploaded = self.client._req("POST", "/files", json_body={"purpose": "batch"},
                    files={"file": (f"preflight-{trial_id}.jsonl", io.BytesIO(data), "application/jsonl")})
            except Exception:
                if operation is not None:
                    controller.ledger.mark(operation, "released")
                raise
            file_id = uploaded["id"]
            try:
                job = self.client._req("POST", "/batches", json_body={"input_file_id": file_id,
                    "endpoint": "/v1/responses", "completion_window": "24h", "metadata": {"purpose": "kajovong_preflight", "matrix": matrix_version()}})
            except Exception as exc:
                definitive = getattr(exc, "status_code", None) in (400, 401, 403, 404, 422)
                if operation is not None:
                    controller.ledger.mark(operation, "released" if definitive else "unknown")
                # Neurčitý transportní výsledek nelze automaticky opakovat a účtovat znovu.
                for key, body in missing.items():
                    self.proofs[key] = {"model": body["model"], "state": "failed" if definitive else "submission_unknown",
                        "input_file_id": file_id, "error": str(exc), "status_code": getattr(exc, "status_code", None),
                        "param": getattr(exc, "param", None), "code": getattr(exc, "code", None)}
                self.save()
                raise rejection(rows[0]["body"]["model"], exc, True) from exc
            if operation is not None:
                controller.ledger.mark(operation, "pending", job["id"])
            for key, body in missing.items():
                self.proofs[key] = {"model": body["model"], "state": "pending", "batch_id": job["id"],
                    "input_file_id": file_id, "tested_at": time.time(), "cost_operation": operation}
            self.save()
        batch_ids = {self.proofs[key]["batch_id"] for key in by_key if self.proofs[key]["state"] == "pending"}
        deadline = time.monotonic() + min(self.client.timeout_s, 60)
        for batch_id in batch_ids:
            while True:
                controller = getattr(self.client, "cost_control", None)
                if controller is not None and (controller.cancelled or controller.stopped()):
                    raise PreflightPending(f"Zkušební Batch {batch_id} zůstává uložený. Pracovní dávka nebyla odeslána.")
                job = self.client.retrieve_batch(batch_id)
                if job.get("status") in ("completed", "failed", "expired", "cancelled"):
                    self.finish_batch(job, by_key)
                    break
                if time.monotonic() >= deadline:
                    raise PreflightPending(f"Zkušební Batch {batch_id}: {job.get('status')}. Pracovní dávka nebyla odeslána; "
                        "spusťte odeslání znovu pro převzetí výsledku. OpenAI má okno až 24 hodin.")
                time.sleep(min(2, max(0, deadline - time.monotonic())))
        for key in by_key:
            proof = self.proofs[key]
            if proof["state"] != "verified":
                from .openai_client import OpenAIError
                raise rejection(by_key[key]["model"], OpenAIError(proof.get("error", "Batch nebyl ověřen"),
                    proof.get("status_code"), param=proof.get("param"), code=proof.get("code")), True)

    def finish_batch(self, job, payloads):
        log_dir = self.log_dir / ("PREFLIGHT_BATCH_" + job["id"])
        log_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(log_dir / "batch.json"), json.dumps(job, ensure_ascii=False))
        records = {}
        for field in ("output_file_id", "error_file_id"):
            if not job.get(field):
                continue
            content = self.client.file_content(job[field]).decode("utf-8")
            atomic_write_text(str(log_dir / (field + ".jsonl")), content)
            for line in content.splitlines():
                if line.strip():
                    row = json.loads(line)
                    key = row.get("custom_id")
                    if key in records:
                        raise ValueError(f"Zkušební Batch: duplicitní custom_id={key}.")
                    records[key] = row
        for key, proof in self.proofs.items():
            if proof.get("batch_id") != job["id"] or key not in payloads:
                continue
            row = records.get(key, {})
            response = row.get("response") or {}
            body = response.get("body") or {}
            error = row.get("error") or body.get("error") or {}
            proof.update(state="failed", status_code=response.get("status_code"),
                error=error.get("message") or str(job.get("errors") or f"Batch {job['status']}: chybí úspěšný řádek"),
                param=error.get("param"), code=error.get("code"))
            if job["status"] == "completed" and response.get("status_code") == 200 and not error:
                if not proof.get("cost_operation"):
                    self.record_usage(body, payloads[key], "PREFLIGHT_BATCH_" + key, True)
                try:
                    validate_output(body, payloads[key])
                except Exception as exc:
                    proof["error"] = str(exc)
                else:
                    proof.update(state="verified", response_id=body.get("id"), tested_at=time.time())
        self.save()
        if any(proof.get("batch_id") == job["id"] and proof.get("cost_operation") for proof in self.proofs.values()):
            from .batch_costs import finalize_batches
            finalize_batches(self.client, self.db_path)
        # Úklid pouze vlastních souborů až po převzetí všech odpovědí a vyúčtování.
        related = [p for p in self.proofs.values() if p.get("batch_id") == job["id"]]
        if all(p["state"] != "pending" for p in related):
            owned = {job.get("output_file_id"), job.get("error_file_id"), *(p.get("input_file_id") for p in related)} - {None}
            for file_id in owned:
                try:
                    self.client.delete_file(file_id)
                except Exception as exc:
                    atomic_write_text(str(log_dir / (file_id + "_cleanup.txt")), str(exc))
