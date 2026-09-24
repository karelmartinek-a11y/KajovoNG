"""Výroba binárních kaskádových výstupů skutečnými pracovními nástroji."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

from .cascade_contract import output_machine_key, step_signature
from .comic_types import IMAGE_MODEL
from .contracts import ContractError, parse_json_strict
from .image_runtime import validate_image_request
from .model_registry import model_spec
from .orchestration.contracts import canonical_sha256
from .orchestration.repository import repository_for_logger
from .orchestration.image_slots import image_policy
from .orchestration.work_order import freeze_order
from .structured_output import obj, response_format, validate_output

TEXT_TYPES = {"txt", "md", "json", "csv"}
IMAGE_TYPES = {"png", "jpg", "jpeg"}


def _cached(logger, name):
    path = logger.find_json("manifests", name)
    if path is None and getattr(logger, "_cascade_resume_root", ""):
        from .recoverable_artifacts import artifact_path
        path = artifact_path(logger._cascade_resume_root, "manifests/" + name)
        if path:
            value = parse_json_strict(Path(path).read_text(encoding="utf-8"))
            logger.save_json("manifests", name, value)
            return value
    if path is None:
        return None
    return parse_json_strict(Path(path).read_text(encoding="utf-8"))


def _image(worker, client, step, output, descriptor, bindings, seed, name):
    images = [row for row in bindings if row["type"] == "input_image"]
    if output.file_mode == "modify":
        original = next((row for row in images if row["input_id"] == output.modify_input_id), None)
        if original is None:
            raise ContractError("Obrazový MODIFY vyžaduje konkrétní obrazový originál.")
        images = [original, *(row for row in images if row is not original)]
    physical = list(dict.fromkeys(row["file_id"] for row in images))
    slots = [{**row, "image_index": physical.index(row["file_id"]) + 1} for row in images]
    endpoint = "/v1/images/edits" if images else "/v1/images/generations"
    body = {
        "model": IMAGE_MODEL,
        "prompt": json.dumps({"task": descriptor, "image_bindings": slots,
                              "modify_input_id": output.modify_input_id}, ensure_ascii=False),
        "n": 1, "size": "1024x1024", "quality": image_policy()["quality"],
        "output_format": "jpeg" if output.file_type in {"jpg", "jpeg"} else "png",
        "background": "opaque",
    }
    if physical:
        body["images"] = [{"file_id": file_id} for file_id in physical]
    validate_image_request(endpoint, body)
    logger = worker.logger
    saved = _cached(logger, name + "_response")
    if saved is not None:
        if saved.get("seed") != seed:
            raise ContractError("Archivovaná obrazová práce má jiný kontrakt.")
        response = saved["response"]
        order = None
    else:
        cfg = worker._operation_cfg(IMAGE_MODEL, worker.cfg.execution_approval_id)
        order = freeze_order(cfg, {
            "run_id": logger.run_id, "step_id": worker._current_step_record_id,
            "task_id": step.id + ":binary:" + output.id, "stage": "CASCADE_IMAGE",
            "route": "image_live", "provider_endpoint": endpoint,
            "target_id": output.id, "target_path": output.file_name,
            "expected_target_hash": worker._cascade_expected_hashes[output.file_name],
            "contract_name": "CASCADE_BINARY_TASK_V1", "schema": {"endpoint": endpoint},
            "prompt": body["prompt"], "request_payload": body, "model": IMAGE_MODEL,
            "model_capability": model_spec(IMAGE_MODEL), "source_snapshot": seed,
            "attempt_no": 1, "approval_id": worker.cfg.execution_approval_id,
        }, seed)
        repo = repository_for_logger(logger)
        persisted = repo.register_work_order(order, body_ref=canonical_sha256(body), input_hash=order.input_projection_hash)
        repo.prepare_provider_operation(attempt_id=order.attempt_id, work_order_hash=persisted,
                                        endpoint=endpoint, request_hash=canonical_sha256(body))
        logger.save_json("requests", name, body, step_id=worker._current_step_record_id)
        logger.save_json("manifests", name + "_order", {**order.to_dict(), "order_hash": order.order_hash})
        repo.mark_submission_started(order.attempt_id)
        try:
            response = client.create_image(endpoint, body)
        except Exception as exc:
            if getattr(exc, "request_sent", None) is False or getattr(exc, "status_code", None) in {400, 401, 403, 404, 422, 429}:
                repo.mark_not_submitted(order.attempt_id)
            else:
                repo.mark_submitted(order.attempt_id, None, unknown=True)
                logger.update_state({"status": "submission_unknown"})
                worker._write_runtime_state({"status": "submission_unknown", "run_id": logger.run_id,
                                             "failed_step_id": step.id})
            raise
        logger.save_json("manifests", name + "_response", {"seed": seed, "response": response})
    identity = str(response.get("id") or response.get("_request_id") or "image-evidence-" + canonical_sha256(response))
    if order is not None:
        repo.mark_submitted(order.attempt_id, identity, unknown=False)
        if isinstance(response.get("usage"), dict):
            repo.record_usage(order.attempt_id, provider="openai-image", provider_item_id=identity, usage=response["usage"])
        repo.mark_terminal(order.attempt_id, identity)
    rows = response.get("data")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise ContractError("Obrazová výroba nevrátila jednoznačný artefakt.")
    try:
        return base64.b64decode(rows[0]["b64_json"], validate=True)
    except (KeyError, ValueError, TypeError) as exc:
        raise ContractError("Obrazová výroba nevrátila platné bajty artefaktu.") from exc


def _document(worker, client, step, idx, output, descriptor, input_payload, bindings, seed, name):
    if "code_interpreter" not in model_spec(step.model).get("features", []):
        raise ContractError("Zvolený model nepodporuje výrobu dokumentu nástrojem.")
    artifact_schema = obj({"filename": {"type": "string", "enum": [Path(output.file_name).name]}})
    payload = {
        "model": step.model,
        "instructions": "Použij Code Interpreter pro skutečné vytvoření souboru. "
                        "Nevracej base64. Přilož výsledný soubor jako container_file_citation. "
                        "Vrať jeho jméno podle JSON kontraktu. MODIFY použije výhradně určený originál.",
        "input": json.dumps({"task": descriptor, "source_input": input_payload["input"],
                             "bindings": bindings, "modify_input_id": output.modify_input_id}, ensure_ascii=False),
        "tools": [{"type": "code_interpreter", "container": {
            "type": "auto", "file_ids": list(dict.fromkeys(row["file_id"] for row in bindings)),
        }}],
        "tool_choice": "required", "text": response_format("CASCADE_DOCUMENT_ARTIFACT_V1", artifact_schema),
        "truncation": "disabled", "max_output_tokens": model_spec(step.model)["max_output_tokens"],
    }
    saved = _cached(worker.logger, name + "_response")
    if saved is None:
        response = worker._schema_request(client, step, idx, payload, 1, task_suffix="binary:" + output.id)
        worker.logger.save_json("manifests", name + "_response", {"seed": seed, "response": response})
    else:
        if saved.get("seed") != seed:
            raise ContractError("Archivovaná výroba má jiný kontrakt.")
        response = saved["response"]
    validate_output(response, payload)
    containers = {row.get("container_id") for row in response.get("output", [])
                  if row.get("type") == "code_interpreter_call" and row.get("status") == "completed"}
    citations = set()
    for message in response.get("output", []):
        if message.get("type") != "message":
            continue
        for part in message.get("content", []):
            for row in part.get("annotations", []):
                if row.get("type") == "container_file_citation" and row.get("filename") == Path(output.file_name).name:
                    citations.add((row.get("container_id"), row.get("file_id")))
    if len(citations) != 1:
        raise ContractError("Dokument nemá jednoznačnou provider identitu artefaktu.")
    container_id, file_id = citations.pop()
    if container_id not in containers:
        raise ContractError("Artefakt nepatří dokončenému pracovnímu kontejneru.")
    # Chyba či expirace tohoto GET nikdy nespouští nové placené generování.
    return client.container_file_content(container_id, file_id)


def produce_binary_outputs(worker, client, step, idx, payload, decoded):
    result = copy.deepcopy(decoded)
    bindings = getattr(worker, "_step_input_bindings", [])
    for output in step.outputs:
        if output.kind != "file" or output.file_type in TEXT_TYPES:
            continue
        key = output_machine_key(output)
        descriptor = result[key]
        seed = {"step_signature": step_signature(step), "output": output.to_dict(),
                "descriptor": descriptor, "input": payload["input"], "bindings": bindings}
        name = "cascade_binary_" + canonical_sha256(seed)[:32]
        saved = _cached(worker.logger, name + "_artifact")
        if saved is not None:
            raw = base64.b64decode(saved["base64"], validate=True)
            if saved.get("seed") != seed or hashlib.sha256(raw).hexdigest() != saved.get("sha256"):
                raise ContractError("Archivovaný binární artefakt změnil hash.")
        else:
            raw = (_image(worker, client, step, output, descriptor, bindings, seed, name)
                   if output.file_type in IMAGE_TYPES else
                   _document(worker, client, step, idx, output, descriptor, payload, bindings, seed, name))
            if not isinstance(raw, bytes) or not raw:
                raise ContractError("Výrobní nástroj nevrátil obsah souboru.")
            worker.logger.save_json("manifests", name + "_artifact", {
                "contract": "CASCADE_BINARY_ARTIFACT_V1", "seed": seed,
                "sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode("ascii"),
            })
        result[key] = {"path": output.file_name, "encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}
    return result
