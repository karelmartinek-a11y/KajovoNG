"""Kanonická forenzní evidence běhu a odvozený index Historie.

Run Bundle je bezeztrátová evidence. Tento modul záměrně neprovádí obsahovou
redakci; důvěrnost řeší přístup k pracovnímu prostředí, nikoli změna důkazních dat.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

BUNDLE_SCHEMA_VERSION = 1
BUNDLE_COMPATIBILITY_VERSION = 1
INDEX_SCHEMA_VERSION = 3

RUN_STATUSES = {
    "created",
    "preparing",
    "running",
    "response_pending",
    "batch_prepared",
    "batch_pending",
    "importing",
    "completed",
    "partial",
    "failed",
    "cancelled",
    "stopped",
    "dry_run",
    "submission_unknown",
    "files_complete_unverified",
    "completed_unverified",
    "plan_ready",
    "qfile_plan_ready",
    "closed",
    "corrupt_state",
    "unknown",
}
TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled", "stopped", "closed",
                     "files_complete_unverified", "completed_unverified", "dry_run", "plan_ready", "qfile_plan_ready"}
ARTIFACT_BUCKETS = {"inputs", "intermediate", "outputs", "external"}


def _now_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else timestamp
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8") + b"\n")


def _append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _safe_name(value: str, limit: int = 120) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in str(value or ""))
    return (cleaned.strip("._") or "artifact")[:limit]


def _response_text(response: dict[str, Any]) -> str:
    text = response.get("output_text")
    if isinstance(text, str):
        return text
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for entry in content:
            if not isinstance(entry, dict):
                continue
            candidate = entry.get("text")
            if isinstance(candidate, str):
                parts.append(candidate)
    return "\n".join(parts)


def _structured_value(text: str) -> Any:
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _usage_value(usage: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int):
            return value
    return None


@dataclass
class RunRecord:
    schema_version: int
    run_id: str
    project: str = ""
    mode: str = ""
    created_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    status: str = "created"
    result_class: str = ""
    user_label: str = ""
    parent_run_id: str = ""
    lineage_reason: str = ""
    cloned_from_run_id: str = ""
    continued_from_checkpoint_id: str = ""
    related_batch_ids: list[str] = field(default_factory=list)
    root_response_id: str = ""
    last_response_id: str = ""
    model_summary: list[str] = field(default_factory=list)
    input_summary: str = ""
    output_summary: str = ""
    artifact_bundle_id: str = ""
    configuration_snapshot_hash: str = ""
    run_scope_hash: str = ""
    run_bundle_hash: str = ""
    kind: str = "run"
    legacy: bool = False


@dataclass
class StepRecord:
    schema_version: int
    step_id: str
    run_id: str
    sequence: int
    stage: str
    title: str
    kind: str
    started_at: str
    finished_at: str = ""
    status: str = "running"
    progress: int = 0
    model: str = ""
    reasoning_effort: str = ""
    request_ids: list[str] = field(default_factory=list)
    response_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    validation_ids: list[str] = field(default_factory=list)
    retry_group_id: str = ""
    parent_step_id: str = ""
    checkpoint_id: str = ""
    human_summary: str = ""
    technical_summary: str = ""


@dataclass
class EventRecord:
    schema_version: int
    event_id: str
    run_id: str
    step_id: str
    sequence: int
    timestamp: str
    event_type: str
    severity: str
    source_module: str
    operation: str
    human_message: str
    technical_message: str
    data: dict[str, Any]
    related_request_id: str = ""
    related_response_id: str = ""
    related_artifact_ids: list[str] = field(default_factory=list)


@dataclass
class RequestRecord:
    schema_version: int
    request_record_id: str
    run_id: str
    step_id: str
    created_at: str
    sent_at: str
    endpoint: str
    method: str
    model: str
    full_payload: Any
    payload_sha256: str
    input_token_count: int | None
    projected_output_tokens: int | None
    reasoning_effort: str
    retry_attempt: int
    request_role: str
    remote_request_id: str
    source_path: str = ""


@dataclass
class ResponseRecord:
    schema_version: int
    response_record_id: str
    run_id: str
    step_id: str
    received_at: str
    response_id: str
    status: str
    full_response: Any
    response_sha256: str
    output_text: str
    structured_value: Any
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    cached_tokens: int | None
    incomplete_reason: str
    error: Any
    validation_status: str
    artifact_ids: list[str] = field(default_factory=list)
    source_path: str = ""


@dataclass
class ValidationRecord:
    schema_version: int
    validation_id: str
    run_id: str
    step_id: str
    target_type: str
    target_id: str
    validator: str
    timestamp: str
    status: str
    errors: list[str]
    warnings: list[str]
    evidence: Any


@dataclass
class ArtifactRecord:
    schema_version: int
    artifact_id: str
    run_id: str
    step_id: str
    role: str
    kind: str
    path_in_bundle: str
    original_path: str
    display_name: str
    mime_type: str
    size_bytes: int
    sha256: str
    created_at: str
    source: str
    source_request_id: str
    source_response_id: str
    parent_artifact_id: str
    reusable: bool
    reconstruction_role: str
    metadata: dict[str, Any]
    available_local: bool = True


@dataclass
class CheckpointRecord:
    schema_version: int
    checkpoint_id: str
    run_id: str
    step_id: str
    created_at: str
    checkpoint_type: str
    safe_to_continue: bool
    reason: str
    required_artifact_ids: list[str]
    required_response_ids: list[str]
    state_snapshot: Any
    state_hash: str
    compatibility_version: int
    invalidation_rules: list[str]


@dataclass
class LineageRecord:
    schema_version: int
    lineage_id: str
    source_run_id: str
    target_run_id: str
    relation_type: str
    source_checkpoint_id: str
    created_at: str
    user_action: str
    inherited_artifact_ids: list[str]
    inherited_configuration: Any
    notes: str


class RunBundle:
    """Čte a zapisuje kanonickou evidenci jednoho nového běhu."""

    def __init__(
        self,
        run_dir: str | Path,
        run_id: str | None = None,
        project: str = "",
        mode: str = "",
        *,
        create: bool = False,
        kind: str = "run",
    ):
        self.root = Path(run_dir)
        self.run_id = str(run_id or self.root.name)
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        self.requests_dir = self.root / "requests"
        self.responses_dir = self.root / "responses"
        self.validations_dir = self.root / "validations"
        self.checkpoints_dir = self.root / "checkpoints"
        self.artifacts_dir = self.root / "artifacts"
        self.manifests_dir = self.root / "manifests"
        self.reports_dir = self.root / "reports"
        for directory in (
            self.requests_dir,
            self.responses_dir,
            self.validations_dir,
            self.checkpoints_dir,
            self.artifacts_dir,
            self.manifests_dir,
            self.reports_dir,
        ):
            if create:
                directory.mkdir(parents=True, exist_ok=True)
        for bucket in ARTIFACT_BUCKETS:
            if create:
                (self.artifacts_dir / bucket).mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self.steps_path = self.root / "steps.jsonl"
        self.artifact_index_path = self.artifacts_dir / "records.jsonl"
        self.bundle_path = self.root / "bundle.json"
        self.run_path = self.root / "run.json"
        self.lineage_path = self.root / "lineage.json"
        self.checksums_path = self.root / "checksums.json"
        # Čtení metadat neprochází celý proud událostí. Čítač je zapotřebí
        # teprve při prvním skutečném zápisu do znovu otevřeného bundle.
        self._event_sequence = None
        self._step_sequence = None
        if create and not self.bundle_path.exists():
            bundle = {
                "schema_version": BUNDLE_SCHEMA_VERSION,
                "bundle_id": "bundle_" + uuid.uuid4().hex,
                "run_id": self.run_id,
                "created_at": _now_iso(),
                "compatibility_version": BUNDLE_COMPATIBILITY_VERSION,
                "integrity_status": "unsealed",
                "bundle_hash": "",
                "run_record_sha256": "",
            }
            _atomic_json(self.bundle_path, bundle)
        if create and not self.run_path.exists():
            metadata = _read_json(self.bundle_path, {}) or {}
            record = RunRecord(
                schema_version=BUNDLE_SCHEMA_VERSION,
                run_id=self.run_id,
                project=project,
                mode=mode,
                created_at=_now_iso(),
                status="created",
                artifact_bundle_id=str(metadata.get("bundle_id") or ""),
                kind=kind,
            )
            _atomic_json(self.run_path, asdict(record))
        if not self.lineage_path.exists() and create:
            _atomic_json(self.lineage_path, {"schema_version": 1, "records": []})

    def run_record(self) -> dict[str, Any]:
        value = _read_json(self.run_path, {})
        return value if isinstance(value, dict) else {}

    def update_run(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.run_record()
        if not current:
            current = asdict(
                RunRecord(BUNDLE_SCHEMA_VERSION, self.run_id, created_at=_now_iso())
            )
        current.update(patch)
        current["schema_version"] = BUNDLE_SCHEMA_VERSION
        current["run_id"] = self.run_id
        _atomic_json(self.run_path, current)
        return current

    def append_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        step_id: str = "",
        severity: str = "info",
        source_module: str = "",
        operation: str = "",
        human_message: str = "",
        technical_message: str = "",
        related_request_id: str = "",
        related_response_id: str = "",
        related_artifact_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        if self._event_sequence is None:
            self._event_sequence = max([int(item.get("sequence") or 0) for item in _read_jsonl(self.events_path)] or [0])
        self._event_sequence += 1
        timestamp_epoch = time.time()
        record = asdict(
            EventRecord(
                BUNDLE_SCHEMA_VERSION,
                "evt_" + uuid.uuid4().hex,
                self.run_id,
                step_id,
                self._event_sequence,
                _now_iso(timestamp_epoch),
                event_type,
                severity,
                source_module,
                operation,
                human_message,
                technical_message,
                dict(data or {}),
                related_request_id,
                related_response_id,
                list(related_artifact_ids),
            )
        )
        # Kompatibilní aliasy zachovávají čitelnost starších recovery cest.
        record["ts"] = timestamp_epoch
        record["type"] = event_type
        _append_jsonl(self.events_path, record)
        return record

    def steps(self) -> list[dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for record in _read_jsonl(self.steps_path):
            identifier = str(record.get("step_id") or "")
            if identifier:
                latest[identifier] = record
        return sorted(latest.values(), key=lambda item: int(item.get("sequence") or 0))

    def ensure_step(
        self,
        stage: str,
        *,
        title: str | None = None,
        kind: str = "operation",
        model: str = "",
        reasoning_effort: str = "",
        parent_step_id: str = "",
    ) -> dict[str, Any]:
        for record in reversed(self.steps()):
            if record.get("stage") == stage and record.get("status") not in {"failed", "cancelled"}:
                return record
        if self._step_sequence is None:
            self._step_sequence = max([int(item.get("sequence") or 0) for item in self.steps()] or [0])
        self._step_sequence += 1
        record = asdict(
            StepRecord(
                BUNDLE_SCHEMA_VERSION,
                "step_" + uuid.uuid4().hex,
                self.run_id,
                self._step_sequence,
                stage,
                title or stage or "Operace",
                kind,
                _now_iso(),
                model=model,
                reasoning_effort=reasoning_effort,
            )
        )
        record["parent_step_id"] = parent_step_id
        _append_jsonl(self.steps_path, record)
        self.append_event(
            "step.started",
            {"stage": stage, "title": record["title"]},
            step_id=record["step_id"],
            operation=stage,
            human_message=f"Zahájen krok {record['title']}.",
        )
        return record

    def update_step(self, step_id: str, **patch: Any) -> dict[str, Any]:
        current = next((item for item in self.steps() if item.get("step_id") == step_id), None)
        if current is None:
            raise KeyError(step_id)
        current = dict(current)
        for key, value in patch.items():
            if key in {"request_ids", "response_ids", "artifact_ids", "validation_ids"}:
                current[key] = list(dict.fromkeys([*(current.get(key) or []), *(value or [])]))
            else:
                current[key] = value
        _append_jsonl(self.steps_path, current)
        return current

    def _stage_from_name(self, name: str) -> str:
        token = str(name or "").split("_", 1)[0].strip()
        return token[:48] or "API"

    def record_request(
        self,
        full_payload: Any,
        *,
        name: str = "request",
        step_id: str = "",
        endpoint: str = "/v1/responses",
        method: str = "POST",
        retry_attempt: int = 0,
        request_role: str = "work",
        remote_request_id: str = "",
        source_path: str = "",
    ) -> dict[str, Any]:
        payload = full_payload.get("payload") if isinstance(full_payload, dict) and "payload" in full_payload else full_payload
        payload = payload if payload is not None else full_payload
        model = str(payload.get("model") or "") if isinstance(payload, dict) else ""
        reasoning = payload.get("reasoning") if isinstance(payload, dict) else None
        reasoning_effort = str(reasoning.get("effort") or "") if isinstance(reasoning, dict) else ""
        projected_output = payload.get("max_output_tokens") if isinstance(payload, dict) else None
        stage = self._stage_from_name(name)
        if isinstance(payload, dict):
            retry_attempt = int((payload.get("metadata") or {}).get("kajovo_repair_attempt", retry_attempt))
        if not step_id and request_role != "transport":
            step = self.ensure_step(stage, title=stage, kind="api", model=model, reasoning_effort=reasoning_effort)
            step_id = str(step["step_id"])
        identifier = "req_" + uuid.uuid4().hex
        record = asdict(
            RequestRecord(
                BUNDLE_SCHEMA_VERSION,
                identifier,
                self.run_id,
                step_id,
                _now_iso(),
                _now_iso(),
                endpoint,
                method,
                model,
                full_payload,
                _sha256_bytes(_json_bytes(full_payload)),
                None,
                int(projected_output) if isinstance(projected_output, int) else None,
                reasoning_effort,
                retry_attempt,
                request_role,
                remote_request_id,
                source_path,
            )
        )
        path = self.requests_dir / f"_record_{identifier}.json"
        _atomic_json(path, record)
        if step_id:
            self.update_step(step_id, request_ids=[identifier], model=model or None, reasoning_effort=reasoning_effort or None)
        self.append_event(
            "request.sent",
            {"endpoint": endpoint, "method": method, "model": model, "payload_sha256": record["payload_sha256"]},
            step_id=step_id,
            operation=stage,
            related_request_id=identifier,
            human_message=f"Odeslán požadavek {stage}.",
        )
        return record

    def record_response(
        self,
        full_response: Any,
        *,
        name: str = "response",
        step_id: str = "",
        source_path: str = "",
    ) -> dict[str, Any]:
        response = full_response if isinstance(full_response, dict) else {"value": full_response}
        response_id = str(response.get("id") or "")
        status = str(response.get("status") or "unknown")
        output_text = _response_text(response)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        output_details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
        input_details = usage.get("input_tokens_details") if isinstance(usage.get("input_tokens_details"), dict) else {}
        incomplete = response.get("incomplete_details")
        incomplete_reason = ""
        if isinstance(incomplete, dict):
            incomplete_reason = str(incomplete.get("reason") or "")
        stage = self._stage_from_name(name)
        if not step_id and not name.startswith(("provider_", "received_")):
            step = self.ensure_step(stage, title=stage, kind="api")
            step_id = str(step["step_id"])
        identifier = "res_" + uuid.uuid4().hex
        record = asdict(
            ResponseRecord(
                BUNDLE_SCHEMA_VERSION,
                identifier,
                self.run_id,
                step_id,
                _now_iso(),
                response_id,
                status,
                full_response,
                _sha256_bytes(_json_bytes(full_response)),
                output_text,
                _structured_value(output_text),
                _usage_value(usage, "input_tokens"),
                _usage_value(usage, "output_tokens"),
                _usage_value(output_details, "reasoning_tokens"),
                _usage_value(input_details, "cached_tokens"),
                incomplete_reason,
                response.get("error"),
                "pending",
                [],
                source_path,
            )
        )
        path = self.responses_dir / f"_record_{identifier}.json"
        _atomic_json(path, record)
        if step_id:
            current_step = next((row for row in self.steps() if row["step_id"] == step_id), {})
            pending_validation = bool(current_step.get("validation_required")) and status == "completed"
            self.update_step(
                step_id,
                response_ids=[identifier],
                status="validating_result" if pending_validation else status,
                finished_at=_now_iso() if status not in {"queued", "in_progress"} and not pending_validation else "",
                progress=100 if status == "completed" and not pending_validation else 0,
            )
        self.append_event(
            "response.received",
            {"response_id": response_id, "status": status, "incomplete_reason": incomplete_reason},
            step_id=step_id,
            operation=stage,
            related_response_id=identifier,
            human_message=f"Přijata odpověď {response_id or identifier} ({status}).",
            severity="error" if response.get("error") else "info",
        )
        return record

    def record_validation(
        self,
        *,
        step_id: str = "",
        target_type: str,
        target_id: str,
        validator: str,
        status: str,
        errors: Iterable[str] = (),
        warnings: Iterable[str] = (),
        evidence: Any = None,
    ) -> dict[str, Any]:
        identifier = "val_" + uuid.uuid4().hex
        record = asdict(
            ValidationRecord(
                BUNDLE_SCHEMA_VERSION,
                identifier,
                self.run_id,
                step_id,
                target_type,
                target_id,
                validator,
                _now_iso(),
                status,
                list(errors),
                list(warnings),
                evidence,
            )
        )
        _atomic_json(self.validations_dir / f"{identifier}.json", record)
        if target_type == "preparation":
            for path in self.responses_dir.glob("_record_*.json"):
                response = _read_json(path, {})
                if response.get("step_id") == step_id and response.get("response_id") == target_id:
                    response["validation_status"] = status
                    _atomic_json(path, response)
        if step_id:
            try:
                self.update_step(step_id, validation_ids=[identifier])
            except KeyError:
                pass
        self.append_event(
            "validation.completed",
            {"validator": validator, "status": status, "target_type": target_type, "target_id": target_id},
            step_id=step_id,
            severity="error" if status == "failed" else "warning" if list(warnings) else "info",
            human_message=f"Validace {validator}: {status}.",
        )
        return record

    def _artifact_bucket(self, role: str) -> str:
        if role in {"user_input", "attached_file", "in_project_file"}:
            return "inputs"
        if role in {"generated_file", "modified_file", "batch_output", "log_export"}:
            return "outputs"
        if role in {"remote_file", "vector_store", "external_reference"}:
            return "external"
        return "intermediate"

    def register_artifact(
        self,
        *,
        role: str,
        kind: str,
        path_in_bundle: str,
        original_path: str = "",
        display_name: str = "",
        step_id: str = "",
        source: str = "",
        source_request_id: str = "",
        source_response_id: str = "",
        parent_artifact_id: str = "",
        reusable: bool = False,
        reconstruction_role: str = "",
        metadata: dict[str, Any] | None = None,
        available_local: bool = True,
        sha256: str = "",
        size_bytes: int = 0,
    ) -> dict[str, Any]:
        identifier = "art_" + uuid.uuid4().hex
        local = self.root / path_in_bundle if path_in_bundle else None
        if local is not None and local.is_file():
            size_bytes = local.stat().st_size
            sha256 = _sha256_file(local)
        record = asdict(
            ArtifactRecord(
                BUNDLE_SCHEMA_VERSION,
                identifier,
                self.run_id,
                step_id,
                role,
                kind,
                path_in_bundle,
                original_path,
                display_name or Path(original_path or path_in_bundle or identifier).name,
                mimetypes.guess_type(display_name or original_path or path_in_bundle)[0] or "application/octet-stream",
                int(size_bytes or 0),
                sha256,
                _now_iso(),
                source,
                source_request_id,
                source_response_id,
                parent_artifact_id,
                bool(reusable),
                reconstruction_role,
                dict(metadata or {}),
                bool(available_local),
            )
        )
        _append_jsonl(self.artifact_index_path, record)
        if step_id:
            try:
                self.update_step(step_id, artifact_ids=[identifier])
            except KeyError:
                pass
        self.append_event(
            "artifact.recorded",
            {"role": role, "kind": kind, "path_in_bundle": path_in_bundle, "sha256": sha256},
            step_id=step_id,
            related_artifact_ids=[identifier],
            human_message=f"Evidován artefakt {record['display_name']}.",
        )
        return record

    def archive_artifact(
        self,
        source_path: str | Path,
        *,
        role: str,
        kind: str = "file",
        step_id: str = "",
        source: str = "filesystem",
        source_request_id: str = "",
        source_response_id: str = "",
        parent_artifact_id: str = "",
        reusable: bool = True,
        reconstruction_role: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = Path(source_path)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        bucket = self._artifact_bucket(role)
        digest = _sha256_file(path)
        filename = f"{digest[:16]}_{_safe_name(path.name)}"
        target = self.artifacts_dir / bucket / filename
        if target.exists():
            if _sha256_file(target) != digest:
                raise ValueError("Kolize archivovaného artefaktu.")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".tmp_art_", dir=str(target.parent))
            os.close(descriptor)
            try:
                shutil.copy2(path, temporary)
                if _sha256_file(Path(temporary)) != digest:
                    raise ValueError("Hash kopie artefaktu neodpovídá zdroji.")
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.remove(temporary)
        return self.register_artifact(
            role=role,
            kind=kind,
            path_in_bundle=target.relative_to(self.root).as_posix(),
            original_path=str(path),
            display_name=path.name,
            step_id=step_id,
            source=source,
            source_request_id=source_request_id,
            source_response_id=source_response_id,
            parent_artifact_id=parent_artifact_id,
            reusable=reusable,
            reconstruction_role=reconstruction_role,
            metadata=metadata,
            available_local=True,
            sha256=digest,
            size_bytes=path.stat().st_size,
        )

    def record_external_artifact(
        self,
        identifier: str,
        *,
        role: str = "external_reference",
        kind: str = "remote_file",
        step_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.register_artifact(
            role=role,
            kind=kind,
            path_in_bundle="",
            original_path=identifier,
            display_name=identifier,
            step_id=step_id,
            source="external",
            reusable=False,
            reconstruction_role="reference_only",
            metadata=metadata,
            available_local=False,
        )

    def artifacts(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.artifact_index_path)

    def checkpoint(
        self,
        checkpoint_type: str,
        *,
        state_snapshot: Any,
        safe_to_continue: bool,
        reason: str,
        step_id: str = "",
        required_artifact_ids: Iterable[str] = (),
        required_response_ids: Iterable[str] = (),
        invalidation_rules: Iterable[str] = (),
    ) -> dict[str, Any]:
        identifier = "cp_" + uuid.uuid4().hex
        record = asdict(
            CheckpointRecord(
                BUNDLE_SCHEMA_VERSION,
                identifier,
                self.run_id,
                step_id,
                _now_iso(),
                checkpoint_type,
                bool(safe_to_continue),
                reason,
                list(required_artifact_ids),
                list(required_response_ids),
                state_snapshot,
                _sha256_bytes(_json_bytes(state_snapshot)),
                BUNDLE_COMPATIBILITY_VERSION,
                list(invalidation_rules),
            )
        )
        _atomic_json(self.checkpoints_dir / f"{identifier}.json", record)
        if step_id:
            try:
                self.update_step(step_id, checkpoint_id=identifier)
            except KeyError:
                pass
        self.append_event(
            "checkpoint.created",
            {"checkpoint_id": identifier, "type": checkpoint_type, "safe": bool(safe_to_continue), "reason": reason},
            step_id=step_id,
            human_message=("Vytvořen bezpečný checkpoint " if safe_to_continue else "Vytvořen informativní checkpoint ") + checkpoint_type + ".",
        )
        return record

    def checkpoints(self) -> list[dict[str, Any]]:
        values = []
        for path in sorted(self.checkpoints_dir.glob("cp_*.json")):
            value = _read_json(path, {})
            if isinstance(value, dict):
                values.append(value)
        return sorted(values, key=lambda item: str(item.get("created_at") or ""))

    def validate_checkpoint(self, checkpoint_id: str, *, artifacts=None, responses=None, hash_cache=None) -> dict[str, Any]:
        from .utils import safe_join_under_root

        if not checkpoint_id or Path(checkpoint_id).name != checkpoint_id or "\\" in checkpoint_id:
            raise ValueError("Neplatný identifikátor checkpointu.")
        path = self.checkpoints_dir / f"{checkpoint_id}.json"
        record = _read_json(path, {})
        if not isinstance(record, dict) or record.get("checkpoint_id") != checkpoint_id:
            raise ValueError("Checkpoint nebyl nalezen.")
        if record.get("compatibility_version") != BUNDLE_COMPATIBILITY_VERSION:
            raise ValueError("Checkpoint není kompatibilní s touto verzí aplikace.")
        if not record.get("safe_to_continue"):
            raise ValueError("Tento checkpoint není označen jako bezpečný pro pokračování.")
        snapshot = record.get("state_snapshot")
        if _sha256_bytes(_json_bytes(snapshot)) != record.get("state_hash"):
            raise ValueError("Checkpoint má neplatný hash stavu.")
        artifacts = {item.get("artifact_id"): item for item in (self.artifacts() if artifacts is None else artifacts)}
        hash_cache = {} if hash_cache is None else hash_cache
        for identifier in record.get("required_artifact_ids") or []:
            artifact = artifacts.get(identifier)
            if not artifact:
                raise ValueError(f"Checkpointu chybí artefakt {identifier}.")
            relative = artifact.get("path_in_bundle")
            if not relative or artifact.get("available_local") is False:
                raise ValueError(f"Artefakt checkpointu {identifier} není místně dostupný.")
            target = Path(safe_join_under_root(str(self.root), relative))
            if not target.is_file():
                raise ValueError(f"Artefakt checkpointu {identifier} není integritní.")
            stat = target.stat()
            key = (str(target), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if key not in hash_cache:
                hash_cache[key] = _sha256_file(target)
            if hash_cache[key] != artifact.get("sha256"):
                raise ValueError(f"Artefakt checkpointu {identifier} není integritní.")
        response_records = {item.get("response_record_id"): item
                            for item in (LegacyRunAdapter(self.root).responses() if responses is None else responses)}
        missing = set(record.get("required_response_ids") or []) - response_records.keys()
        if missing:
            raise ValueError("Checkpointu chybí požadované response záznamy: " + ", ".join(sorted(missing)))
        for identifier in record.get("required_response_ids") or []:
            response = response_records[identifier]
            if _sha256_bytes(_json_bytes(response.get("full_response"))) != response.get("response_sha256"):
                raise ValueError("Odpověď checkpointu nemá platný hash: " + identifier)
        return record

    def record_lineage(
        self,
        source_run_id: str,
        relation_type: str,
        *,
        source_checkpoint_id: str = "",
        user_action: str = "",
        inherited_artifact_ids: Iterable[str] = (),
        inherited_configuration: Any = None,
        notes: str = "",
    ) -> dict[str, Any]:
        allowed = {"continue", "clone", "rerun", "repair", "reuse_artifacts", "retry_batch", "regenerate_partial"}
        if relation_type not in allowed:
            raise ValueError("Neplatný typ lineage.")
        record = asdict(
            LineageRecord(
                BUNDLE_SCHEMA_VERSION,
                "lin_" + uuid.uuid4().hex,
                source_run_id,
                self.run_id,
                relation_type,
                source_checkpoint_id,
                _now_iso(),
                user_action or relation_type,
                list(inherited_artifact_ids),
                inherited_configuration,
                notes,
            )
        )
        container = _read_json(self.lineage_path, {"schema_version": 1, "records": []})
        if not isinstance(container, dict) or not isinstance(container.get("records"), list):
            container = {"schema_version": 1, "records": []}
        container["records"].append(record)
        _atomic_json(self.lineage_path, container)
        patch = {"parent_run_id": source_run_id, "lineage_reason": relation_type}
        if relation_type == "clone":
            patch["cloned_from_run_id"] = source_run_id
        if source_checkpoint_id:
            patch["continued_from_checkpoint_id"] = source_checkpoint_id
        self.update_run(patch)
        self.append_event(
            "lineage.created",
            record,
            human_message=f"Běh navazuje na {source_run_id} ({relation_type}).",
        )
        return record

    def lineage(self) -> list[dict[str, Any]]:
        value = _read_json(self.lineage_path, {})
        records = value.get("records") if isinstance(value, dict) else None
        return [item for item in (records or []) if isinstance(item, dict)]

    def _checksum_files(self) -> list[Path]:
        ignored = {self.bundle_path.resolve(), self.checksums_path.resolve(),
                   (self.root / "execution.lock").resolve()}
        files: list[Path] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in ignored:
                continue
            files.append(path)
        return sorted(files, key=lambda item: item.relative_to(self.root).as_posix())

    def seal(self) -> dict[str, Any]:
        checksums: dict[str, str] = {}
        for path in self._checksum_files():
            checksums[path.relative_to(self.root).as_posix()] = _sha256_file(path)
        content_hashes = {key: value for key, value in checksums.items() if key != "run.json"}
        bundle_hash = _sha256_bytes(_json_bytes(content_hashes))
        run = self.run_record()
        run["run_bundle_hash"] = bundle_hash
        _atomic_json(self.run_path, run)
        checksums["run.json"] = _sha256_file(self.run_path)
        _atomic_json(
            self.checksums_path,
            {"schema_version": 1, "bundle_hash": bundle_hash, "files": checksums},
        )
        metadata = _read_json(self.bundle_path, {}) or {}
        metadata.update(
            integrity_status="sealed",
            sealed_at=_now_iso(),
            bundle_hash=bundle_hash,
            run_record_sha256=checksums["run.json"],
        )
        _atomic_json(self.bundle_path, metadata)
        return metadata

    def verify_integrity(self) -> dict[str, Any]:
        expected = _read_json(self.checksums_path, {})
        if not isinstance(expected, dict) or not isinstance(expected.get("files"), dict):
            return {"status": "unsealed", "valid": False, "errors": ["Run Bundle ještě nemá integritní manifest."]}
        errors: list[str] = []
        files = expected["files"]
        for relative, digest in files.items():
            # Provozní QLockFile se po ukončení pracovníka odstraní; není důkazním artefaktem.
            if relative == "execution.lock":
                continue
            target = self.root / relative
            if not target.is_file():
                errors.append(f"Chybí {relative}.")
            elif _sha256_file(target) != digest:
                errors.append(f"Hash nesouhlasí: {relative}.")
        current = {key: value for key, value in files.items() if key != "run.json"}
        actual_bundle_hash = _sha256_bytes(_json_bytes(current))
        if actual_bundle_hash != expected.get("bundle_hash"):
            errors.append("Nesouhlasí hash Run Bundle.")
        return {
            "status": "verified" if not errors else "changed",
            "valid": not errors,
            "bundle_hash": expected.get("bundle_hash", ""),
            "errors": errors,
        }


class LegacyRunAdapter:
    """Read-only adaptér: chybějící legacy fakta nikdy nedoplňuje odhadem."""

    def __init__(self, run_dir: str | Path):
        self.root = Path(run_dir)
        self.run_id = self.root.name
        self.bundle = RunBundle(self.root) if (self.root / "bundle.json").exists() else None

    @property
    def legacy(self) -> bool:
        return self.bundle is None

    def state(self) -> dict[str, Any]:
        value = _read_json(self.root / "run_state.json", {})
        return value if isinstance(value, dict) else {}

    def run_record(self) -> dict[str, Any]:
        if self.bundle:
            record = self.bundle.run_record()
            record["legacy"] = False
            return record
        state = self.state()
        ui = state.get("ui_state") if isinstance(state.get("ui_state"), dict) else {}
        created = state.get("created_at")
        created_at = _now_iso(created) if isinstance(created, (int, float)) else ""
        completed = state.get("completed_at")
        finished_at = _now_iso(completed) if isinstance(completed, (int, float)) else ""
        models = []
        for key in ("model", "model_a1", "model_a2", "model_a3"):
            value = ui.get(key)
            if isinstance(value, str) and value and value not in models:
                models.append(value)
        prompt = ui.get("prompt") if isinstance(ui.get("prompt"), str) else ""
        return asdict(
            RunRecord(
                0,
                self.run_id,
                project=str(state.get("project") or ui.get("project") or ""),
                mode=str(ui.get("mode") or state.get("mode") or ""),
                created_at=created_at,
                finished_at=finished_at,
                status=str(state.get("status") or "unknown"),
                result_class=str(state.get("status") or "unknown"),
                related_batch_ids=[value for value in [state.get("batch_id")] if isinstance(value, str) and value],
                last_response_id=str(state.get("last_response_id") or ""),
                model_summary=models,
                input_summary=prompt[:500],
                output_summary=str(state.get("error") or ""),
                kind=str(state.get("kind") or "run"),
                legacy=True,
            )
        )

    def steps(self) -> list[dict[str, Any]]:
        return self.bundle.steps() if self.bundle else []

    def events(self) -> list[dict[str, Any]]:
        records = _read_jsonl(self.root / "events.jsonl")
        if not self.legacy:
            return records
        normalized = []
        for index, value in enumerate(records, 1):
            record = dict(value)
            record.setdefault("sequence", index)
            record.setdefault("event_id", "")
            record.setdefault("event_type", str(record.get("type") or "legacy"))
            record.setdefault("timestamp", _now_iso(record["ts"]) if isinstance(record.get("ts"), (int, float)) else "")
            record.setdefault("severity", "unknown")
            record.setdefault("step_id", "")
            record.setdefault("human_message", "")
            record.setdefault("technical_message", "")
            normalized.append(record)
        return normalized

    def _evidence_records(self, directory: Path, key: str) -> list[dict[str, Any]]:
        records = []
        pattern = "*.json" if self.legacy else "_record_*.json"
        for path in sorted(directory.glob(pattern)):
            value = _read_json(path, None)
            if isinstance(value, dict) and value.get(key):
                records.append(value)
            elif self.legacy and isinstance(value, dict):
                records.append({
                    "schema_version": 0,
                    key: "",
                    "run_id": self.run_id,
                    "step_id": "",
                    "source_path": str(path),
                    "legacy": True,
                    "full_payload" if key == "request_record_id" else "full_response": value,
                    "response_id": str(value.get("id") or "") if key == "response_record_id" else "",
                    "status": str(value.get("status") or "unknown") if key == "response_record_id" else "unknown",
                    "output_text": _response_text(value) if key == "response_record_id" else "",
                })
        return sorted(records, key=lambda item: str(item.get("created_at") or item.get("received_at") or ""))

    def requests(self) -> list[dict[str, Any]]:
        return self._evidence_records(self.root / "requests", "request_record_id")

    def responses(self) -> list[dict[str, Any]]:
        return self._evidence_records(self.root / "responses", "response_record_id")

    def validations(self) -> list[dict[str, Any]]:
        if self.legacy:
            return []
        values = []
        for path in sorted((self.root / "validations").glob("*.json")):
            value = _read_json(path, {})
            if isinstance(value, dict):
                values.append(value)
        return values

    def artifacts(self) -> list[dict[str, Any]]:
        if self.bundle:
            return self.bundle.artifacts()
        records = []
        for folder, role in (("requests", "request_payload"), ("responses", "response_raw"), ("manifests", "manifest"), ("misc", "misc")):
            directory = self.root / folder
            if not directory.is_dir():
                continue
            for path in sorted(directory.iterdir()):
                if not path.is_file():
                    continue
                records.append(
                    asdict(
                        ArtifactRecord(
                            0,
                            "",
                            self.run_id,
                            "",
                            role,
                            "legacy_file",
                            path.relative_to(self.root).as_posix(),
                            str(path),
                            path.name,
                            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                            path.stat().st_size,
                            _sha256_file(path),
                            _now_iso(path.stat().st_mtime),
                            "legacy",
                            "",
                            "",
                            "",
                            True,
                            "legacy_evidence",
                            {"legacy": True},
                            True,
                        )
                    )
                )
        return records

    def checkpoints(self) -> list[dict[str, Any]]:
        return self.bundle.checkpoints() if self.bundle else []

    def lineage(self) -> list[dict[str, Any]]:
        return self.bundle.lineage() if self.bundle else []

    def integrity(self) -> dict[str, Any]:
        if not self.bundle:
            return {"status": "legacy", "valid": False, "errors": ["Legacy běh nemá Run Bundle integritní manifest."]}
        return self.bundle.verify_integrity()


class HistoryIndex:
    """Odvozený read-model. Kanonická pravda zůstává v jednotlivých Run Bundle."""

    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir)
        self.path = self.log_dir / "history_index.json"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        value = _read_json(self.path, {})
        if not isinstance(value, dict) or value.get("schema_version") != INDEX_SCHEMA_VERSION:
            return {"schema_version": INDEX_SCHEMA_VERSION, "runs": {}}
        if not isinstance(value.get("runs"), dict):
            return {"schema_version": INDEX_SCHEMA_VERSION, "runs": {}}
        return value

    def _source_mtime(self, directory: Path) -> int:
        values = []
        for name in ("run.json", "run_state.json", "events.jsonl", "steps.jsonl", "lineage.json", "checksums.json"):
            path = directory / name
            try:
                values.append(path.stat().st_mtime_ns)
            except OSError:
                pass
        for folder in ("requests", "responses", "checkpoints", "validations", "artifacts"):
            path = directory / folder
            try:
                values.append(path.stat().st_mtime_ns)
            except OSError:
                pass
        return max(values or [0])

    def _summary(self, directory: Path) -> dict[str, Any]:
        adapter = LegacyRunAdapter(directory)
        run = adapter.run_record()
        responses = adapter.responses()
        artifacts = adapter.artifacts()
        checkpoints = adapter.checkpoints()
        events = adapter.events()
        steps = adapter.steps()
        lineage = adapter.lineage()
        state = adapter.state()
        response_ids = [str(item.get("response_id") or "") for item in responses if item.get("response_id")]
        search_parts = [
            str(run.get("run_id") or ""),
            str(run.get("project") or ""),
            str(run.get("mode") or ""),
            str(run.get("status") or ""),
            str(run.get("input_summary") or ""),
            str(run.get("output_summary") or ""),
            " ".join(response_ids),
            " ".join(str(item.get("display_name") or "") for item in artifacts),
            " ".join(str(item.get("event_type") or item.get("type") or "") for item in events),
        ]
        has_error = any(
            str(event.get("severity") or "").lower() == "error"
            or "error" in str(event.get("event_type") or event.get("type") or "").lower()
            for event in events
        ) or str(run.get("status") or "") in {"failed", "partial", "submission_unknown"}
        return {
            "run_id": self.run_id_from(directory),
            "source_mtime_ns": self._source_mtime(directory),
            "legacy": adapter.legacy,
            "project": str(run.get("project") or ""),
            "mode": str(run.get("mode") or ""),
            "status": str(run.get("status") or "unknown"),
            "created_at": str(run.get("created_at") or ""),
            "finished_at": str(run.get("finished_at") or ""),
            "models": list(run.get("model_summary") or []),
            "result_class": str(run.get("result_class") or ""),
            "steps": len(steps),
            "timeline_steps": [
                {
                    key: item.get(key)
                    for key in (
                        "step_id", "sequence", "stage", "title", "kind", "started_at", "finished_at",
                        "status", "progress", "model", "reasoning_effort", "request_ids", "response_ids",
                        "artifact_ids", "validation_ids", "checkpoint_id", "human_summary", "technical_summary",
                    )
                }
                for item in steps
            ],
            "responses": len(responses),
            "artifacts": len(artifacts),
            "input_count": sum(
                1 for item in artifacts
                if item.get("role") in {"user_input", "attached_file", "in_project_file", "input"}
            ),
            "output_count": sum(
                1 for item in artifacts
                if item.get("role") in {"generated_file", "modified_file", "batch_output", "log_export"}
            ),
            "error_count": sum(
                1 for event in events
                if str(event.get("severity") or "").lower() == "error"
            ),
            "checkpoints": len(checkpoints),
            "checkpoint_markers": [{key: item.get(key) for key in ("checkpoint_id", "checkpoint_type", "step_id")}
                                   for item in checkpoints],
            "has_checkpoint": bool(checkpoints),
            "has_error": has_error,
            "has_batch": bool(run.get("related_batch_ids")) or bool(state.get("batch_id") or state.get("generate_batches")),
            "has_output": any(item.get("role") in {"generated_file", "modified_file", "batch_output", "log_export"} for item in artifacts),
            "has_lineage": bool(lineage or run.get("parent_run_id")),
            "parent_run_id": str(run.get("parent_run_id") or (lineage[-1].get("source_run_id") if lineage else "")),
            "lineage_records": lineage,
            "related_batch_ids": list(run.get("related_batch_ids") or []),
            "batch_imports": state.get("batch_imports") if isinstance(state.get("batch_imports"), dict) else {},
            "batch_records": state.get("batch_records") if isinstance(state.get("batch_records"), dict) else {},
            "response_ids": response_ids,
            "search_text": "\n".join(search_parts).casefold(),
        }

    @staticmethod
    def run_id_from(directory: Path) -> str:
        return directory.name

    def refresh(self, *, force: bool = False) -> list[dict[str, Any]]:
        index = self._load()
        runs = index["runs"]
        changed = not self.path.exists()
        existing = set()
        for directory in sorted(self.log_dir.glob("RUN_*")):
            if not directory.is_dir():
                continue
            run_id = directory.name
            existing.add(run_id)
            mtime = self._source_mtime(directory)
            if force or run_id not in runs or int(runs[run_id].get("source_mtime_ns") or 0) != mtime:
                runs[run_id] = self._summary(directory)
                changed = True
        for stale in set(runs) - existing:
            runs.pop(stale, None)
            changed = True
        if changed:
            index["updated_at"] = _now_iso()
            _atomic_json(self.path, index)
        self._current_records = list(runs.values())
        return sorted(runs.values(), key=lambda item: (str(item.get("created_at") or ""), item["run_id"]), reverse=True)

    def rebuild(self) -> list[dict[str, Any]]:
        if self.path.exists():
            self.path.unlink()
        return self.refresh(force=True)

    def reverse_lineage(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for run in getattr(self, "_current_records", None) or self.refresh():
            for record in run.get("lineage_records") or []:
                source = str(record.get("source_run_id") or "")
                if source:
                    result.setdefault(source, []).append(record)
        return result
