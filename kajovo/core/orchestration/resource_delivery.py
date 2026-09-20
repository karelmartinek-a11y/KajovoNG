"""Typed resource producers for non-text IMPLEMENTATION_GRAPH_V3 targets."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from ..comic_types import IMAGE_MODEL
from ..contracts import ContractError
from ..image_runtime import inspect_image, validate_image_request
from ..model_registry import model_spec
from ..openai_transport import SubmissionOutcomeUnknown
from ..utils import ensure_dir, safe_join_under_root, sha256_file
from .contracts import canonical_sha256
from .repository import repository_for_logger
from .work_order import freeze_order


_SUPPORTED_IMAGE_SUFFIXES = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".kajovo_resource_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def resource_delivery_index(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    spine = graph.get("spine") or {}
    rows = spine.get("resource_deliveries") or []
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row.get("path") or "")
        if not path or path in index:
            raise ContractError("Resource delivery musí mít jedinečnou neprázdnou cestu.")
        index[path] = dict(row)
    return index


def _source_descriptors(worker) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for source in (getattr(worker, "source_context", {}) or {}).get(
        "_provider_inputs", []
    ):
        if isinstance(source, dict):
            values.append(dict(source))
    for source in (getattr(worker, "source_context", {}) or {}).get(
        "attachments", []
    ):
        if isinstance(source, dict):
            values.append(dict(source))
    return values


def _known_source(worker, identifier: str) -> dict[str, Any] | None:
    for row in _source_descriptors(worker):
        if identifier in {
            str(row.get("source_id") or ""),
            str(row.get("filename") or ""),
        }:
            return row
    for artifact in worker.log.bundle.artifacts():
        metadata = artifact.get("metadata") or {}
        if identifier in {
            str(metadata.get("source_id") or ""),
            str(metadata.get("relative_path") or ""),
            str(metadata.get("filename") or ""),
            str(artifact.get("reconstruction_role") or ""),
        }:
            return {
                "source_id": metadata.get("source_id"),
                "filename": (
                    metadata.get("filename")
                    or metadata.get("relative_path")
                    or artifact.get("reconstruction_role")
                ),
                "sha256": metadata.get("sha256") or artifact.get("sha256"),
                "path_in_bundle": artifact.get("path_in_bundle"),
            }
    return None


def validate_resource_plan(worker, graph: dict[str, Any]) -> None:
    """Reject unsupported producer declarations before A3/B3 can start."""
    spine = graph.get("spine") or {}
    files = {
        str(row.get("path") or ""): row for row in spine.get("files") or []
    }
    deliveries = resource_delivery_index(graph)
    production_actions = (
        {"generate"}
        if graph.get("mode") == "GENERATE"
        else {"add", "modify"}
    )
    resources = {
        path: row
        for path, row in files.items()
        if row.get("action") in production_actions and row.get("kind") != "text"
    }
    if set(deliveries) != set(resources):
        missing = sorted(set(resources) - set(deliveries))
        extra = sorted(set(deliveries) - set(resources))
        details = []
        if missing:
            details.append("chybí producer: " + ", ".join(missing))
        if extra:
            details.append("producer pro netarget: " + ", ".join(extra))
        raise ContractError(
            "Resource deliveries musí přesně pokrýt netextové výrobní cíle"
            + (": " + "; ".join(details) if details else ".")
        )

    for path, target in resources.items():
        delivery = deliveries[path]
        producer = str(delivery.get("producer") or "")
        source = str(delivery.get("source_or_task_id") or "").strip()
        if not source:
            raise ContractError(f"{path}: resource producer nemá source_or_task_id.")
        if target.get("content_dependencies"):
            raise ContractError(
                f"{path}: binární/local resource nesmí být verified-content "
                "provider pro textový FileContext."
            )
        suffix = Path(path).suffix.lower()
        if producer == "existing_asset":
            if _known_source(worker, source) is None:
                raise ContractError(
                    f"{path}: existing_asset neodkazuje na schválený SourcePack: {source}"
                )
        elif producer == "image_workflow":
            if suffix not in _SUPPORTED_IMAGE_SUFFIXES:
                raise ContractError(
                    f"{path}: image_workflow podporuje PNG/JPEG/WebP."
                )
        elif producer == "local_renderer":
            generated = files.get(source)
            known = _known_source(worker, source)
            source_name = (
                str((generated or {}).get("path") or "")
                if generated is not None
                else str((known or {}).get("filename") or "")
            )
            if Path(source_name).suffix.lower() != ".svg" or suffix != ".png":
                raise ContractError(
                    f"{path}: podporovaný local_renderer je pouze SVG -> PNG "
                    "ze schváleného nebo plánovaného SVG."
                )
        elif producer == "manual_input":
            pass
        else:
            raise ContractError(f"{path}: nepodporovaný resource producer {producer!r}.")


def _bundle_source_bytes(worker, identifier: str) -> tuple[bytes, str]:
    run_root = Path(worker.log.paths.run_dir).resolve()
    for artifact in worker.log.bundle.artifacts():
        metadata = artifact.get("metadata") or {}
        if identifier not in {
            str(metadata.get("source_id") or ""),
            str(metadata.get("relative_path") or ""),
            str(metadata.get("filename") or ""),
            str(artifact.get("reconstruction_role") or ""),
        }:
            continue
        relative = artifact.get("path_in_bundle")
        if not isinstance(relative, str) or not relative:
            continue
        path = (run_root / relative).resolve()
        try:
            path.relative_to(run_root)
        except ValueError as exc:
            raise ContractError("Resource SourcePack artifact uniká z RunBundle.") from exc
        if not path.is_file():
            raise ContractError(f"Schválený resource source chybí: {identifier}")
        data = path.read_bytes()
        expected = str(metadata.get("sha256") or artifact.get("sha256") or "")
        if not expected or hashlib.sha256(data).hexdigest() != expected:
            raise ContractError(f"Schválený resource source změnil hash: {identifier}")
        return data, str(
            metadata.get("filename")
            or metadata.get("relative_path")
            or artifact.get("reconstruction_role")
            or identifier
        )
    raise ContractError(f"Resource source nebyl nalezen v immutable SourcePacku: {identifier}")


def _render_svg_png(data: bytes) -> bytes:
    renderer = QSvgRenderer(QByteArray(data))
    if not renderer.isValid():
        raise ContractError("local_renderer: zdroj není platné SVG.")
    size = renderer.defaultSize()
    width = int(size.width())
    height = int(size.height())
    if width <= 0 or height <= 0:
        raise ContractError("local_renderer: SVG nemá platnou výchozí velikost.")
    if width * height > 64_000_000 or max(width, height) > 8192:
        raise ContractError("local_renderer: SVG přesahuje bezpečný render limit.")
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ContractError("local_renderer: nelze otevřít PNG buffer.")
    if not image.save(buffer, "PNG"):
        raise ContractError("local_renderer: PNG render selhal.")
    result = bytes(buffer.data())
    inspect_image(result)
    return result


def _image_prompt(worker, graph: dict[str, Any], target: dict[str, Any], task_id: str) -> str:
    requirements = (
        getattr(worker, "_delivery_snapshot", {}) or {}
    ).get("requirements") or {}
    index = {
        str(row.get("id") or ""): row
        for row in requirements.get("requirements", [])
        if isinstance(row, dict)
    }
    selected = [
        index[value]
        for value in target.get("requirement_ids", [])
        if value in index
    ]
    requirement_text = "\n".join(
        "- " + str(
            row.get("statement")
            or row.get("description")
            or row.get("title")
            or row.get("id")
        )
        for row in selected
    )
    prompt = (
        "Vytvoř finální produkční obrazový asset pro softwarový projekt. "
        f"Cílová cesta: {target['path']}. "
        f"Účel: {target.get('purpose') or 'projektový asset'}. "
        f"Task ID: {task_id}. "
        "Nevytvářej placeholder, mock ani textový popis místo výsledného obrazu."
    )
    if requirement_text:
        prompt += "\nPožadavky:\n" + requirement_text
    return prompt


def _prepare_image_order(worker, path: str, body: dict[str, Any], projection: dict[str, Any]):
    repo = repository_for_logger(worker.log)
    task_id = "resource-image:" + path
    with repo.connect() as db:
        row = db.execute(
            "SELECT w.attempt_no,p.state FROM work_orders w "
            "LEFT JOIN provider_operations p ON p.work_order_hash=w.work_order_hash "
            "WHERE w.run_id=? AND w.task_id=? "
            "ORDER BY w.attempt_no DESC LIMIT 1",
            (worker.log.run_id, task_id),
        ).fetchone()
    attempt_no = 1
    if row:
        prior_attempt, state = int(row[0]), str(row[1] or "")
        if state == "not_submitted":
            attempt_no = prior_attempt + 1
            if attempt_no > 3:
                raise ContractError(f"{path}: image_workflow vyčerpal tři pokusy.")
        else:
            raise ContractError(
                f"{path}: předchozí image_workflow již mohl být odeslán; "
                "automatický resubmit je zablokován."
            )
    expected = getattr(worker, "_delivery_expected_target_hashes", {})
    if path not in expected:
        raise ContractError(f"{path}: chybí původní očekávaný stav cíle.")
    order = freeze_order(
        worker.cfg,
        {
            "run_id": worker.log.run_id,
            "step_id": "resource:" + path,
            "task_id": task_id,
            "stage": "RESOURCE_IMAGE",
            "route": "image_live",
            "target_id": path,
            "target_path": path,
            "expected_target_hash": expected[path],
            "contract_name": "PROJECT_IMAGE_RESOURCE_V1",
            "schema": {
                "endpoint": "/v1/images/generations",
                "model": IMAGE_MODEL,
            },
            "prompt": body["prompt"],
            "model": IMAGE_MODEL,
            "model_capability": model_spec(IMAGE_MODEL),
            "source_snapshot": projection,
            "attempt_no": attempt_no,
            "approval_id": (
                getattr(worker.cfg, "execution_approval_id", "")
                or f"user-start:{worker.log.run_id}"
            ),
        },
        projection,
    )
    persisted = repo.register_work_order(
        order,
        body_ref=canonical_sha256(body),
        input_hash=order.input_projection_hash,
    )
    repo.prepare_provider_operation(
        attempt_id=order.attempt_id,
        work_order_hash=persisted,
        endpoint="/v1/images/generations",
        request_hash=canonical_sha256(body),
    )
    worker.log.save_json(
        "manifests",
        "resource_work_order_" + hashlib.sha256(path.encode()).hexdigest()[:16],
        {**order.to_dict(), "order_hash": order.order_hash},
    )
    return repo, order


def _generate_image(worker, client, graph, target, delivery) -> bytes:
    suffix = Path(target["path"]).suffix.lower()
    output_format = _SUPPORTED_IMAGE_SUFFIXES[suffix]
    body = {
        "model": IMAGE_MODEL,
        "prompt": _image_prompt(
            worker, graph, target, str(delivery["source_or_task_id"])
        ),
        "size": "1024x1024",
        "quality": "max",
        "n": 1,
        "output_format": output_format,
        "background": "opaque",
    }
    validate_image_request("/v1/images/generations", body)
    projection = {
        "target_path": target["path"],
        "producer": "image_workflow",
        "task_id": delivery["source_or_task_id"],
        "graph_hash": canonical_sha256(graph),
    }
    repo, order = _prepare_image_order(
        worker, target["path"], body, projection
    )
    repo.mark_submission_started(order.attempt_id)
    try:
        response = client.create_image("/v1/images/generations", body)
    except Exception as exc:
        definite = (
            getattr(exc, "request_sent", None) is False
            or getattr(exc, "status_code", None)
            in {400, 401, 403, 404, 422, 429}
        )
        if definite:
            repo.mark_not_submitted(order.attempt_id)
        else:
            repo.mark_submitted(order.attempt_id, None, unknown=True)
            worker.log.update_state({"status": "submission_unknown"})
        if isinstance(exc, SubmissionOutcomeUnknown):
            raise
        raise

    provider_id = str(
        response.get("id")
        or response.get("_request_id")
        or (
            "image-response-"
            + canonical_sha256(
                {"target": target["path"], "response": response}
            )[:32]
        )
    )
    repo.mark_submitted(order.attempt_id, provider_id, unknown=False)
    if isinstance(response.get("usage"), dict):
        repo.record_usage(
            order.attempt_id,
            provider="openai-image",
            provider_item_id=provider_id,
            usage=response["usage"],
            raw_response_ref="image-response:" + provider_id,
        )
    items = response.get("data")
    if (
        not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or not items[0].get("b64_json")
    ):
        raise ContractError(
            f"{target['path']}: image_workflow nevrátil jeden úplný obraz."
        )
    try:
        binary = base64.b64decode(items[0]["b64_json"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ContractError(
            f"{target['path']}: image_workflow vrátil poškozený base64 obraz."
        ) from exc
    inspect_image(binary, IMAGE_MODEL)
    evidence = dict(response)
    evidence["data"] = [
        {
            key: value
            for key, value in items[0].items()
            if key != "b64_json"
        }
    ]
    worker.log.save_json(
        "responses",
        "RESOURCE_IMAGE_" + hashlib.sha256(target["path"].encode()).hexdigest()[:16],
        {
            "provider_id": provider_id,
            "response": evidence,
            "binary_sha256": hashlib.sha256(binary).hexdigest(),
        },
    )
    return binary


def _stage_resource(worker, path: str, data: bytes, producer: str) -> dict[str, Any]:
    expected = getattr(worker, "_delivery_expected_target_hashes", {})
    if path not in expected:
        raise ContractError(f"{path}: chybí původní očekávaný stav cíle.")
    run_root = Path(worker.log.paths.run_dir).resolve()
    stage_root = run_root / "staging" / (
        "dry_run"
        if worker.cfg.mode == "MODIFY" and worker.cfg.dry_run
        else "candidate"
    ) / "generated"
    destination = Path(safe_join_under_root(str(stage_root), path))
    ensure_dir(str(destination.parent))
    digest = hashlib.sha256(data).hexdigest()
    if destination.is_file():
        if sha256_file(str(destination)) != digest:
            raise ContractError(
                f"{path}: immutable staging již obsahuje jiný resource."
            )
    else:
        _atomic_write_bytes(destination, data)
    if sha256_file(str(destination)) != digest:
        raise ContractError(f"{path}: staged resource změnil hash.")
    artifact = worker.log.bundle.archive_artifact(
        destination,
        role="staged_output",
        kind="output_file",
        reconstruction_role=path,
        reusable=True,
        metadata={
            "relative_path": path,
            "sha256": digest,
            "expected_target_hash": expected[path],
            "publication": "not_published",
            "resource_producer": producer,
        },
    )
    row = {
        "path": path,
        "staged_path": destination.relative_to(run_root).as_posix(),
        "bytes": len(data),
        "sha256": digest,
        "expected_target_hash": expected[path],
        "action": "resource",
        "purpose": producer,
        "artifact_id": artifact.get("artifact_id"),
        "resource_producer": producer,
    }
    current = dict(getattr(worker, "_resource_staged_files", {}) or {})
    current[path] = row
    worker._resource_staged_files = current
    worker.log.update_state(
        {
            "resource_staged_files": [
                current[key] for key in sorted(current)
            ],
            "resource_states": {
                **dict(getattr(worker, "_resource_states", {}) or {}),
                path: {
                    "status": "completed_unverified",
                    "producer": producer,
                    "sha256": digest,
                },
            },
        }
    )
    return row


def prepare_production_scope(
    worker,
    graph: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[str], list[dict[str, Any]]]:
    """Freeze approved production targets and reject dependency gaps caused by skips."""
    mode = str(graph.get("mode") or "")
    production_actions = {"generate"} if mode == "GENERATE" else {"add", "modify"}
    all_files = {
        str(row["path"]): row
        for row in graph["spine"]["files"]
    }
    production = {
        path: row
        for path, row in all_files.items()
        if row.get("action") in production_actions
    }
    selected: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []
    completed: set[str] = {
        path
        for path, row in all_files.items()
        if row.get("action") == "preserve"
    }
    source_artifacts = {
        str((artifact.get("metadata") or {}).get("relative_path")
            or artifact.get("reconstruction_role")
            or ""): artifact
        for artifact in worker.log.bundle.artifacts()
        if artifact.get("role") == "in_project_file"
    }

    for path, row in production.items():
        suffix = Path(path).suffix.lower()
        excluded = (
            path in (worker.cfg.skip_paths or [])
            or suffix in (worker.cfg.skip_exts or [])
        )
        if not excluded:
            selected[path] = row
            continue
        approved_original = source_artifacts.get(path)
        completed_hash = (worker.cfg.completed_hashes or {}).get(path)
        suitable = False
        if approved_original is not None:
            metadata = approved_original.get("metadata") or {}
            expected = str(metadata.get("sha256") or approved_original.get("sha256") or "")
            current = Path(safe_join_under_root(worker.cfg.in_dir, path)) if worker.cfg.in_dir else None
            suitable = bool(
                current
                and current.is_file()
                and expected
                and sha256_file(str(current)) == expected
            )
        if not suitable and completed_hash and worker.cfg.out_dir:
            output = Path(safe_join_under_root(worker.cfg.out_dir, path))
            suitable = output.is_file() and sha256_file(str(output)) == completed_hash
        if suitable:
            completed.add(path)
        skipped.append({
            "path": path,
            "reason": (
                "skip_path"
                if path in (worker.cfg.skip_paths or [])
                else f"skip_ext:{suffix}"
            ),
            "approved_original_available": suitable,
        })

    for path, row in selected.items():
        blocked = {
            dep
            for dep in row.get("dependencies", [])
            if dep in production and dep not in selected and dep not in completed
        }
        if blocked:
            raise ContractError(
                f"{path}: přeskočené dependency nemají schválený originál: "
                + ", ".join(sorted(blocked))
            )

    expected: dict[str, str | None] = {}
    for path in selected:
        destination = Path(safe_join_under_root(worker.cfg.out_dir, path))
        expected[path] = (
            sha256_file(str(destination)) if destination.is_file() else None
        )
    worker._delivery_expected_target_hashes = expected
    worker._resource_staged_files = {}
    worker._resource_states = {}
    return selected, completed, skipped


def dispatch_resource_target(
    worker,
    client,
    graph: dict[str, Any],
    path: str,
    *,
    generated_text: dict[str, str] | None = None,
) -> dict[str, Any]:
    files = {
        str(row["path"]): row
        for row in graph["spine"]["files"]
    }
    deliveries = resource_delivery_index(graph)
    target = files.get(path)
    delivery = deliveries.get(path)
    if target is None or delivery is None:
        raise ContractError(f"{path}: chybí resource target nebo producer.")
    producer = str(delivery["producer"])
    source_id = str(delivery["source_or_task_id"])
    generated_text = generated_text or {}

    if producer == "manual_input":
        states = dict(getattr(worker, "_resource_states", {}) or {})
        states[path] = {
            "status": "waiting_manual",
            "producer": producer,
            "source_or_task_id": source_id,
        }
        worker._resource_states = states
        worker.log.update_state(
            {
                "resource_states": states,
                "status": "waiting_manual_resource",
            }
        )
        return {
            "path": path,
            "status": "waiting_manual",
            "producer": producer,
        }

    if producer == "existing_asset":
        data, _name = _bundle_source_bytes(worker, source_id)
    elif producer == "local_renderer":
        if source_id in generated_text:
            data = generated_text[source_id].encode("utf-8")
        else:
            data, _name = _bundle_source_bytes(worker, source_id)
        data = _render_svg_png(data)
    elif producer == "image_workflow":
        data = _generate_image(worker, client, graph, target, delivery)
    else:
        raise ContractError(f"{path}: nepodporovaný resource producer {producer!r}.")

    row = _stage_resource(worker, path, data, producer)
    states = dict(getattr(worker, "_resource_states", {}) or {})
    states[path] = {
        "status": "completed_unverified",
        "producer": producer,
        "sha256": row["sha256"],
    }
    worker._resource_states = states
    return {
        "path": path,
        "status": "completed_unverified",
        "producer": producer,
        "staged": row,
    }
