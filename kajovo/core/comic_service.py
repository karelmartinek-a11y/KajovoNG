"""Pracovní příkazy komiksu, snapshoty a obnovitelné obrazové dávky."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import gzip
import sqlite3
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from .batch_submit import exact_batch_matches
from .comic_store import ComicStore, now, uid
from .comic_types import (
    BIBLE_SCHEMA,
    CONTINUITY_SCHEMA,
    DESCRIPTOR_SCHEMA,
    IMAGE_MODEL,
    SCRIPT_SCHEMA,
    STORYBOARD_SCHEMA,
    STORY_SCHEMA,
    ComicError,
    PanelFormat,
    canonical,
    checked_text,
    normalize_bible,
    validate_continuity,
    validate_document,
    validate_overlays,
    validate_script,
    validate_story,
    validate_storyboard,
)
from .image_runtime import image_capability, inspect_image, normalized_image, postprocess, source_bytes, validate_image_request
from .model_registry import model_spec, models_for_usage
from .orchestration.contracts import canonical_sha256
from .orchestration.errors import OrchestrationError
from .orchestration.provider_operations import (
    mark_not_submitted,
    mark_submission,
    mark_submission_started,
    prepare_provider_request,
    record_usage,
)
from .orchestration.repository import repository_for_logger
from .orchestration.run_config import build_run_config_v2
from .orchestration.work_order import freeze_order
from .orchestration.image_slots import (
    FrozenAssetIndex, FrozenImageAsset, ImageRole, compile_slots, normalization_policy_hash,
)
from .progress import ProgressEvent
from .response_journal import ResponseJournal, ResponsePending, SubmissionUnknown
from .runlog import RunLogger
from .structured_output import response_format, validate_output

TERMINAL = {"completed", "failed", "cancelled", "expired"}


class ComicService:
    def __init__(self, settings, client=None, emit=None, stopped=None):
        self.settings = copy.deepcopy(settings)
        self.store = ComicStore(settings.comic_library_dir)
        self.client = client
        self.emit = emit or (lambda event: None)
        self.stopped = stopped or (lambda: False)

    def progress(self, stage, completed=None, total=None, detail=""):
        stage = {"COMIC_RESPONSES": "Sestavuji textová pravidla", "COMIC_REFERENCE": "Vytvářím obrazovou referenci",
                 "COMIC_PREPARING": "Připravuji panely", "COMIC_SUBMITTING": "Odesílám dávku",
                 "COMIC_RETRIEVING": "Přebírám výsledky"}.get(stage, stage)
        self.emit(ProgressEvent(stage, "active", completed, total, "položek", detail))

    def check_stop(self):
        if self.stopped():
            raise ComicError("stopped", "Místní zpracování je zastaveno; uloženou operaci lze obnovit.", True)

    def new_operation(self, project, kind, snapshot, target=None):
        record = self.store.get("projects", project)
        if record["deleted"]:
            raise ComicError("project_deleted", "Nejprve obnovte komiks z koše.")
        identifier, run_id, stamp = uid(), "RUN_COMIC_" + uid(), now()
        with self.store.transaction() as db:
            if target and db.execute("SELECT 1 FROM operations WHERE target_id=? AND status NOT IN ('completed','failed','cancelled','partial')", (target,)).fetchone():
                raise ComicError("operation_active", "Cíl již má nedokončenou operaci.")
            db.execute("INSERT INTO operations(id,project_id,kind,target_id,status,snapshot,run_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (identifier, project, kind, target, "preparing", canonical(snapshot), run_id, stamp, stamp))
            self.store.event(db, project, "start_" + kind, {"operation_id": identifier})
        return identifier

    def logger(self, operation):
        project = self.store.get("projects", operation["project_id"])
        exists = (Path(self.settings.log_dir) / operation["run_id"] / "run_state.json").exists()
        log = RunLogger(self.settings.log_dir, operation["run_id"], project_name=project["name"], resume=exists, comic_operation_id=operation["id"])
        log.update_state({"mode": "COMIC", "comic_operation_id": operation["id"], "comic_library_dir": str(self.store.root),
                          "project": project["name"], "status": operation["status"]})
        return log

    def update_operation(self, identifier, status=None, snapshot=None, error=None):
        with self.store.transaction() as db:
            if status:
                db.execute("UPDATE operations SET status=?,updated_at=? WHERE id=?", (status, now(), identifier))
            if snapshot is not None:
                db.execute("UPDATE operations SET snapshot=? WHERE id=?", (canonical(snapshot), identifier))
            if error is not None:
                db.execute("UPDATE operations SET error=? WHERE id=?", (canonical(error), identifier))

    def import_references(self, project, paths, entity=None):
        paths = list(paths)
        existing = [r for r in self.store.references(project, entity) if r["role"] != "original"]
        if len(paths) + len(existing) > image_capability()["max_references"]:
            raise ComicError("too_many_references", "Knihovna dovoluje nejvýše 16 pracovních referencí na položku.")
        # Před prvním zápisem ověřit celý výběr, nikoli jen přípony.
        images = [(source_bytes(path), str(path)) for path in paths]
        for data, path in images:
            self.check_stop()
            original = self.store.asset(project, data, {**inspect_image(data), "role": "original", "name": path.rsplit("/", 1)[-1]})
            normalized = self.store.asset(project, normalized_image(data), {"role": "working", "original_asset": original})
            if entity:
                self.store.add_reference(project, original, entity, "original")
            self.store.add_reference(project, normalized, entity)

    def upload(self, asset, log):
        path = self.store.asset_path(asset)
        inspect_image(path.read_bytes())
        account = hashlib.sha256((self.client.base_url + self.client.api_key).encode()).hexdigest()
        with self.store.connect() as db:
            row = db.execute("SELECT file_id FROM uploads WHERE asset_id=? AND account=?", (asset, account)).fetchone()
        if row:
            try:
                self.client.retrieve_file(row[0])
                return row[0]
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
        self.check_stop()
        result = self.client.upload_file(str(path), purpose="user_data")
        file_id = result.get("id")
        self.client._validate_resource_id(file_id)
        with self.store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO uploads VALUES(?,?,?)", (asset, account, file_id))
        log.bundle.archive_artifact(path, role="input", kind="image", metadata={"file_id": file_id})
        return file_id

    def _image_cfg(self, operation):
        return SimpleNamespace(
            mode="COMIC",
            model=IMAGE_MODEL,
            model_a1="",
            model_a2="",
            model_a3="",
            send_as_c=operation["kind"] in ("panels", "edit"),
            maximum_quality=True,
            auto_repair="off",
            verification_profile_ids=[],
            stop_after_plan=False,
            dry_run=False,
            execution_approval_id=f"user-start:{operation['run_id']}",
            project=operation["project_id"],
            prompt=operation["kind"],
            in_dir="",
            out_dir="",
            attached_file_ids=[],
            input_file_ids=[],
            attached_vector_store_ids=[],
            qfile_output_path="",
            qfile_output_format="",
            qfile_suggest_path=False,
            qa_continue_conversation=False,
            response_id="",
        )

    def _ensure_image_run(self, operation, log, *, execution):
        repo = repository_for_logger(log)
        if repo.has_run(operation["run_id"]):
            return repo
        cfg = self._image_cfg(operation)
        cfg.send_as_c = execution == "batch"
        run_config = build_run_config_v2(cfg)
        repo.register_run(
            operation["run_id"],
            lineage_id=operation["run_id"],
            scope_hash=canonical_sha256(
                {
                    "run_config_v2": run_config,
                    "operation_id": operation["id"],
                    "snapshot": operation["snapshot"],
                }
            ),
            policy_hash=canonical_sha256(run_config),
            config=run_config,
            approval_id=cfg.execution_approval_id,
            status="running",
        )
        log.update_state({"run_config_v2": run_config})
        return repo

    def _prepare_image_effect(
        self,
        operation,
        log,
        *,
        task_id,
        route,
        target_id,
        body,
        projection,
    ):
        execution = "batch" if route == "image_batch" else "live"
        repo = self._ensure_image_run(
            operation, log, execution=execution
        )
        cfg = self._image_cfg(operation)
        with repo.connect() as db:
            previous = db.execute(
                "SELECT w.attempt_no,p.state "
                "FROM work_orders w LEFT JOIN provider_operations p "
                "ON p.work_order_hash=w.work_order_hash "
                "WHERE w.run_id=? AND w.task_id=? "
                "ORDER BY w.attempt_no DESC LIMIT 1",
                (operation["run_id"], task_id),
            ).fetchone()
        attempt_no = 1
        if previous:
            prior_attempt = int(previous[0])
            prior_state = str(previous[1] or "")
            if prior_state == "not_submitted":
                attempt_no = prior_attempt + 1
                if attempt_no > 3:
                    raise ComicError(
                        "retry_limit",
                        "Obrazová operace již vyčerpala tři povolené pokusy.",
                    )
            else:
                raise SubmissionUnknown(
                    "Předchozí obrazový WorkOrder již mohl být odeslán; "
                    "automatický resubmit je zablokován."
                )
        order = freeze_order(
            cfg,
            {
                "run_id": operation["run_id"],
                "step_id": task_id,
                "task_id": task_id,
                "stage": "COMIC_IMAGE",
                "route": route,
                "provider_endpoint": (
                    "/v1/batches"
                    if route == "image_batch"
                    else str(body.get("endpoint") or "/v1/images/edits")
                ),
                "target_id": target_id,
                "target_path": None,
                "expected_target_hash": None,
                "contract_name": (
                    "COMIC_IMAGE_BATCH_V1"
                    if route == "image_batch"
                    else "COMIC_IMAGE_V1"
                ),
                "schema": {
                    "endpoint": body.get("endpoint")
                    or body.get("url")
                    or "/v1/images",
                    "model": IMAGE_MODEL,
                },
                "prompt": str(
                    body.get("prompt")
                    or body.get("body", {}).get("prompt")
                    or ""
                ),
                "model": IMAGE_MODEL,
                "model_capability": model_spec(IMAGE_MODEL),
                "source_snapshot": projection,
                "attempt_no": attempt_no,
                "approval_id": cfg.execution_approval_id,
            },
            projection,
        )
        persisted_hash = repo.register_work_order(
            order,
            body_ref=canonical_sha256(body),
            input_hash=order.input_projection_hash,
        )
        repo.prepare_provider_operation(
            attempt_id=order.attempt_id,
            work_order_hash=persisted_hash,
            endpoint=order.provider_endpoint,
            request_hash=canonical_sha256(body),
        )
        log.save_json(
            "manifests",
            "comic_work_order_" + task_id,
            {**order.to_dict(), "order_hash": order.order_hash},
        )
        return repo, order

    def _image_attempt_for_task(self, log, task_id):
        repo = repository_for_logger(log)
        with repo.connect() as db:
            row = db.execute(
                "SELECT p.attempt_id "
                "FROM provider_operations p JOIN work_orders w "
                "ON w.work_order_hash=p.work_order_hash "
                "WHERE w.run_id=? AND w.task_id=? "
                "ORDER BY w.attempt_no DESC LIMIT 1",
                (log.run_id, task_id),
            ).fetchone()
        return repo, (str(row[0]) if row else "")

    def _verify_batch_operation_binding(
        self,
        log,
        batch,
        *,
        provider_id: str | None = None,
    ) -> str:
        repo = repository_for_logger(log)
        task_id = "batch-" + str(batch["id"])
        with repo.connect() as db:
            row = db.execute(
                """
                SELECT p.attempt_id,w.route,w.provider_endpoint,p.endpoint,
                       p.remote_input_file_id,p.provider_id,p.state
                FROM provider_operations p
                JOIN work_orders w ON w.work_order_hash=p.work_order_hash
                WHERE w.run_id=? AND w.task_id=?
                ORDER BY w.attempt_no DESC LIMIT 1
                """,
                (log.run_id, task_id),
            ).fetchone()
        if not row:
            raise ComicError(
                "batch_binding_missing",
                "COMIC batch nemá centrální provider-operation kontrakt.",
            )
        attempt_id, route, work_endpoint, operation_endpoint, remote_file, recorded_provider, state = row
        if route != "image_batch" or work_endpoint != "/v1/batches" or operation_endpoint != "/v1/batches":
            raise ComicError(
                "batch_binding_mismatch",
                "COMIC batch má neplatnou fyzickou cestu provider operace.",
            )
        if not batch.get("input_file_id") or remote_file != batch.get("input_file_id"):
            raise ComicError(
                "batch_binding_mismatch",
                "COMIC batch nemá shodné vzdálené input_file_id v obou databázových kontraktech.",
            )
        local_provider = str(provider_id or batch.get("provider_id") or "")
        if recorded_provider and local_provider and recorded_provider != local_provider:
            raise ComicError(
                "batch_binding_mismatch",
                "COMIC batch má rozdílné provider_id v lokální a centrální evidenci.",
            )
        if state == "completed" and not (recorded_provider or local_provider):
            raise ComicError(
                "batch_binding_mismatch",
                "Dokončená COMIC provider operace nemá provider_id.",
            )
        return str(attempt_id)

    def _record_image_usage(
        self,
        log,
        attempt_id,
        *,
        provider_item_id,
        usage,
    ):
        if not attempt_id or not isinstance(usage, dict):
            return
        repository_for_logger(log).record_usage(
            attempt_id,
            provider="openai-image",
            provider_item_id=provider_item_id,
            usage=usage,
            raw_response_ref="image:" + provider_item_id,
        )

    def _text_model(self):
        if self.client is None:
            raise ComicError(
                "api_unavailable",
                "Komiksová textová operace vyžaduje API klienta.",
            )
        cached = getattr(self, "_text_model_cache", "")
        if cached:
            return cached
        try:
            available = [
                str(row.get("id") or "")
                for row in self.client.list_models()
                if isinstance(row, dict) and row.get("id")
            ]
        except Exception as exc:
            raise ComicError(
                "model_catalog_unavailable",
                "Nelze bezpečně načíst katalog textových modelů účtu.",
            ) from exc
        candidates = models_for_usage(available, "responses")
        if not candidates:
            raise ComicError(
                "model_unavailable",
                "Účet nemá pro COMIC kompatibilní Responses model.",
            )
        self._text_model_cache = candidates[0]
        return candidates[0]

    def _comic_text_cfg(self, operation, model):
        return SimpleNamespace(
            mode="COMIC",
            model=model,
            model_a1="",
            model_a2="",
            model_a3="",
            send_as_c=False,
            maximum_quality=False,
            auto_repair="off",
            verification_profile_ids=[],
            stop_after_plan=False,
            dry_run=False,
            execution_approval_id=f"user-start:{operation['run_id']}",
            project=operation["project_id"],
            prompt=operation["kind"],
            in_dir="",
            out_dir="",
            attached_file_ids=[],
            input_file_ids=[],
            attached_vector_store_ids=[],
            qfile_output_path="",
            qfile_output_format="",
            qfile_suggest_path=False,
            qa_continue_conversation=False,
            response_id="",
        )

    def text_request(self, operation, instructions, schema, inputs, assets):
        log = self.logger(operation)
        model = self._text_model()
        file_ids = [self.upload(asset, log) for asset in assets]
        body = {
            "model": model,
            "instructions": instructions,
            "input": [{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": canonical(inputs)},
                    *[
                        {"type": "input_image", "file_id": value}
                        for value in file_ids
                    ],
                ],
            }],
            "text": response_format(
                "COMIC_" + operation["kind"].upper(), schema
            ),
            "max_output_tokens": 12000,
        }
        cfg = self._comic_text_cfg(operation, model)
        projection = {
            "operation_id": operation["id"],
            "kind": operation["kind"],
            "snapshot": operation["snapshot"],
            "input": inputs,
            "asset_hashes": [
                self.store.get("assets", asset)["sha256"]
                for asset in assets
            ],
        }
        order = freeze_order(
            cfg,
            {
                "run_id": operation["run_id"],
                "step_id": "COMIC_" + operation["kind"].upper(),
                "task_id": operation["id"],
                "stage": "COMIC",
                "route": "responses_live",
                "provider_endpoint": "/v1/responses",
                "target_id": str(
                    operation.get("target_id")
                    or operation["project_id"]
                ),
                "target_path": None,
                "expected_target_hash": None,
                "contract_name": (
                    "COMIC_" + operation["kind"].upper()
                ),
                "schema": schema,
                "prompt": instructions,
                "model": model,
                "model_capability": model_spec(model),
                "source_snapshot": projection,
                "attempt_no": 1,
                "approval_id": cfg.execution_approval_id,
            },
            projection,
        )
        journal = ResponseJournal(log, self.settings.response_poll_timeout_s)
        resume_existing = journal.has_entry(body)
        transport_body = {**body, "background": True, "store": True}
        prepare_provider_request(
            log, cfg, self.client, transport_body, work_order=order,
            allow_existing=resume_existing,
        )
        if not resume_existing:
            mark_submission_started(log, order)
        try:
            response = journal.execute(
                self.client,
                body,
                stopped=self.stopped,
                cancelled=lambda: False,
                progress=lambda state, seconds: self.progress(
                    "COMIC_RESPONSES",
                    detail=f"{state}, {seconds} s",
                ),
            )
        except ResponsePending:
            response_id = journal.confirmed_id(body)
            mark_submission(
                log,
                order,
                response_id or None,
                unknown=not bool(response_id),
            )
            raise
        except SubmissionUnknown:
            mark_submission(log, order, None, unknown=True)
            raise
        except Exception as exc:
            definite_reject = (
                getattr(exc, "request_sent", None) is False
                or getattr(exc, "status_code", None)
                in {400, 401, 403, 404, 422, 429}
            )
            if definite_reject:
                mark_not_submitted(log, order)
            else:
                mark_submission(log, order, None, unknown=True)
            raise
        provider_id = str(response.get("id") or "")
        if not provider_id:
            mark_submission(log, order, None, unknown=True)
            raise ComicError(
                "submission_unknown",
                "COMIC Responses operace nemá potvrzené response ID; nový submit je zablokován.",
                True,
            )
        mark_submission(log, order, provider_id, unknown=False)
        record_usage(log, order, response)
        return validate_output(response, body), {
            "model": model,
            "parameters": body,
            "response_id": provider_id,
            "usage": response.get("usage"),
            "run_id": operation["run_id"],
            "work_order_hash": order.order_hash,
        }

    def start_bible(self, project):
        p = self.store.get("projects", project)
        snapshot = {"project_revision": p["revision"], "style": p["style"], "description": p["description"],
                    "assets": [r["asset_id"] for r in self.store.references(project)]}
        return self.new_operation(project, "bible", snapshot, project)

    def _entity_context(self, project_id):
        values = []
        for entity in self.store.rows(
            "entities",
            "project_id=? AND archived=0",
            (project_id,),
            order="created_at",
        ):
            descriptor = ""
            if entity["active_revision"]:
                descriptor = self.store.get(
                    "entity_revisions", entity["active_revision"]
                )["descriptor"]
            values.append(
                {
                    "id": entity["id"],
                    "kind": entity["kind"],
                    "name": entity["name"],
                    "description": entity["description"],
                    "descriptor": descriptor,
                }
            )
        return values

    def latest_document(self, project_id, kind):
        rows = self.store.rows(
            "comic_documents",
            "project_id=? AND kind=?",
            (project_id, kind),
            order="created_at DESC",
        )
        return rows[0] if rows else None

    def _require_bible(self, project_id):
        project = self.store.get("projects", project_id)
        if not project["bible_id"]:
            raise ComicError("bible_missing", "Nejprve sestavte bibli komiksu.")
        return project, self.store.get("bibles", project["bible_id"])

    def start_story(self, project_id):
        project, bible = self._require_bible(project_id)
        snapshot = {
            "project_revision": project["revision"],
            "project": {
                "id": project["id"],
                "name": project["name"],
                "description": project["description"],
                "style": project["style"],
            },
            "bible_id": bible["id"],
            "bible": bible["result"],
            "entities": self._entity_context(project_id),
        }
        return self.new_operation(project_id, "story", snapshot, project_id)

    def start_script(self, project_id):
        project, bible = self._require_bible(project_id)
        story = self.latest_document(project_id, "story")
        if story is None:
            raise ComicError("story_missing", "Nejprve vytvořte Story.")
        snapshot = {
            "project_revision": project["revision"],
            "bible_id": bible["id"],
            "bible": bible["result"],
            "source_document_id": story["id"],
            "story": story["result"],
            "entities": self._entity_context(project_id),
        }
        return self.new_operation(project_id, "script", snapshot, project_id)

    def start_storyboard(self, project_id):
        project, bible = self._require_bible(project_id)
        script = self.latest_document(project_id, "script")
        if script is None:
            raise ComicError("script_missing", "Nejprve vytvořte Script.")
        snapshot = {
            "project_revision": project["revision"],
            "bible_id": bible["id"],
            "bible": bible["result"],
            "source_document_id": script["id"],
            "script": script["result"],
            "entities": self._entity_context(project_id),
        }
        return self.new_operation(project_id, "storyboard", snapshot, project_id)

    def start_continuity(self, project_id):
        project, bible = self._require_bible(project_id)
        storyboard = self.latest_document(project_id, "storyboard")
        script = self.latest_document(project_id, "script")
        if storyboard is None or script is None:
            raise ComicError(
                "storyboard_missing",
                "Continuity vyžaduje uložený Script a Storyboard.",
            )
        snapshot = {
            "project_revision": project["revision"],
            "bible_id": bible["id"],
            "bible": bible["result"],
            "source_document_id": storyboard["id"],
            "storyboard": storyboard["result"],
            "script": script["result"],
            "entities": self._entity_context(project_id),
        }
        return self.new_operation(project_id, "continuity", snapshot, project_id)

    def start_entity(self, entity_id):
        entity = self.store.get("entities", entity_id)
        project = self.store.get("projects", entity["project_id"])
        if not project["bible_id"]:
            raise ComicError("bible_missing", "Nejprve sestavte bibli komiksu.")
        refs = [r["asset_id"] for r in self.store.references(project["id"], entity_id) if r["role"] == "working"]
        style = [r["asset_id"] for r in self.store.references(project["id"])]
        if not refs:
            raise ComicError("missing_reference", "Přidejte alespoň jednu referenci entity.")
        if len(refs) + len(style) > image_capability()["max_references"]:
            raise ComicError("too_many_references", "Reference entity a stylu dohromady přesahují 16 obrázků.")
        bible = self.store.get("bibles", project["bible_id"])
        return self.new_operation(project["id"], "entity", {"entity": entity, "bible": bible, "assets": refs + style}, entity_id)

    def run(self, identifier, *, allow_submit=True):
        with self.store.execution_lock(identifier):
            operation = self.store.get("operations", identifier)
            if operation["status"] == "completed":
                return {"status": "completed", "operation_id": identifier}
            log = self.logger(operation)
            try:
                if operation["kind"] == "bible":
                    self._bible(operation)
                elif operation["kind"] == "entity":
                    self._entity(operation)
                elif operation["kind"] == "story":
                    self._story(operation)
                elif operation["kind"] == "script":
                    self._script(operation)
                elif operation["kind"] == "storyboard":
                    self._storyboard(operation)
                elif operation["kind"] == "continuity":
                    self._continuity(operation)
                else:
                    self._batch(operation, allow_submit=allow_submit)
            except Exception as exc:
                from .user_errors import describe_error
                described = describe_error(exc)
                error = {"code": getattr(exc, "code", None) or described.code, "message": described.message,
                         "retryable": getattr(exc, "retryable", described.retry_safe)}
                if isinstance(exc, sqlite3.Error):
                    error.update(code="db_failure", message="Zápis nebo čtení knihovny selhalo. Ověřte oprávnění a integritu databáze.", retryable=True)
                elif isinstance(exc, OSError):
                    error.update(code="storage_failure", retryable=True)
                status = "response_pending" if isinstance(exc, ResponsePending) else "submission_unknown" if isinstance(exc, SubmissionUnknown) else "failed"
                if getattr(exc, "code", None) == "stopped":
                    status = "stopped"
                self.update_operation(identifier, status, error=error)
                log.event("comic.error", error)
                log.update_state({"status": status})
                raise
            result = self.store.get("operations", identifier)
            self.update_operation(identifier, error={})
            log.update_state({"status": result["status"]})
            if result["status"] in ("completed", "partial", "failed", "cancelled"):
                log.bundle.seal()
            return {"status": result["status"], "operation_id": identifier}

    def _bible(self, operation):
        snapshot = operation["snapshot"]
        existing = [b for b in self.store.rows("bibles", "project_id=?", (operation["project_id"],)) if b["provenance"].get("operation_id") == operation["id"]]
        if existing:
            self.update_operation(operation["id"], "completed")
            return
        result, provenance = self.text_request(operation,
            "Vytvoř profesionální strojově použitelnou bibli komiksu česky. Všechny explicitní volby jsou závazné, "
            "včetně zákazu SFX. Chybějící stylistické detaily doplň konzistentně. Každou kapitolu vyplň konkrétními pravidly.",
            BIBLE_SCHEMA, snapshot, snapshot["assets"])
        normalized = normalize_bible(result, snapshot["style"])
        provenance["operation_id"] = operation["id"]
        bible_id = uid()
        with self.store.transaction() as db:
            db.execute("INSERT INTO bibles VALUES(?,?,?,?,?,?)", (bible_id, operation["project_id"], canonical(snapshot), canonical(normalized), canonical(provenance), now()))
            db.execute("UPDATE projects SET bible_id=? WHERE id=? AND revision=?", (bible_id, operation["project_id"], snapshot["project_revision"]))
            self.store.event(db, operation["project_id"], "bible_created", {"bible_id": bible_id})
        self.update_operation(operation["id"], "completed")

    def _document_for_operation(self, operation):
        rows = self.store.rows(
            "comic_documents",
            "project_id=? AND json_extract(provenance, '$.operation_id')=?",
            (operation["project_id"], operation["id"]),
            order="created_at DESC",
        )
        return rows[0] if rows else None

    def _save_text_document(
        self,
        operation,
        *,
        kind,
        schema,
        instructions,
        semantic,
    ):
        existing = self._document_for_operation(operation)
        if existing is not None:
            self.update_operation(operation["id"], "completed")
            return existing
        snapshot = operation["snapshot"]
        result, provenance = self.text_request(
            operation,
            instructions,
            schema,
            snapshot,
            [],
        )
        normalized = semantic(result)
        provenance["operation_id"] = operation["id"]
        document_id = uid()
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO comic_documents"
                "(id,project_id,kind,source_id,input,result,provenance,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    operation["project_id"],
                    kind,
                    snapshot.get("source_document_id"),
                    canonical(snapshot),
                    canonical(normalized),
                    canonical(provenance),
                    now(),
                ),
            )
            self.store.event(
                db,
                operation["project_id"],
                kind + "_created",
                {
                    "document_id": document_id,
                    "source_document_id": snapshot.get("source_document_id"),
                },
            )
        self.update_operation(operation["id"], "completed")
        return self.store.get("comic_documents", document_id)

    def _story(self, operation):
        return self._save_text_document(
            operation,
            kind="story",
            schema=STORY_SCHEMA,
            instructions=(
                "Vytvoř kanonický příběh komiksu podle bible, explicitního popisu "
                "projektu a známých entit. Výstup musí mít stabilní story beat ID, "
                "úplný dějový oblouk a nesmí přidávat postavy ani prostředí, která "
                "nejsou doložena vstupem, pokud je projekt výslovně nepožaduje."
            ),
            semantic=validate_story,
        )

    def _script(self, operation):
        story = operation["snapshot"]["story"]
        entity_ids = {
            row["id"] for row in operation["snapshot"].get("entities", [])
        }

        def semantic(result):
            value = validate_script(result, story)
            for scene in value["scenes"]:
                if not set(scene["entity_ids"]) <= entity_ids:
                    raise ComicError(
                        "invalid_output",
                        "Scénář odkazuje na neznámou entitu projektu.",
                    )
            return value

        return self._save_text_document(
            operation,
            kind="script",
            schema=SCRIPT_SCHEMA,
            instructions=(
                "Převeď schválený Story do produkčního scénáře. Každá scéna musí "
                "odkazovat na existující story beat, zachovat bible/entity identity "
                "a explicitně uvést lokaci, čas, akci, dialogy a použité entity. "
                "Nevynechej žádný story beat."
            ),
            semantic=semantic,
        )

    def _storyboard(self, operation):
        script = operation["snapshot"]["script"]
        entity_ids = {
            row["id"] for row in operation["snapshot"].get("entities", [])
        }
        return self._save_text_document(
            operation,
            kind="storyboard",
            schema=STORYBOARD_SCHEMA,
            instructions=(
                "Rozděl schválený scénář do úplného storyboardu panelů. Každý "
                "panel musí mít stabilní ID, navazovat na existující scénu, přesnou "
                "pozici, typ záběru, vizuální popis, entity, dialog a caption. "
                "Pokryj každou scénu a zachovej kontinuitu identity a prostředí."
            ),
            semantic=lambda result: validate_storyboard(
                result, script, entity_ids
            ),
        )

    def _continuity(self, operation):
        storyboard = operation["snapshot"]["storyboard"]
        return self._save_text_document(
            operation,
            kind="continuity",
            schema=CONTINUITY_SCHEMA,
            instructions=(
                "Proveď nezávislou continuity kontrolu Scriptu a Storyboardu proti "
                "bible a kanonickým entitám. PASS smí být vrácen jen pokud jsou "
                "schváleny všechny storyboard panely a neexistuje blocking nález. "
                "Jinak vrať needs_changes a konkrétní opravy."
            ),
            semantic=lambda result: validate_continuity(result, storyboard),
        )

    def materialize_storyboard(self, project_id):
        storyboard = self.latest_document(project_id, "storyboard")
        continuity = self.latest_document(project_id, "continuity")
        if storyboard is None:
            raise ComicError("storyboard_missing", "Nejprve vytvořte Storyboard.")
        if (
            continuity is None
            or continuity.get("source_id") != storyboard["id"]
            or continuity["result"].get("status") != "pass"
        ):
            raise ComicError(
                "continuity_missing",
                "Storyboard lze převést na panely až po PASS continuity "
                "kontrole stejné verze.",
            )

        # Starší explicitní materializační event je platná provenance. Pouhé
        # podobné názvy panelů se nikdy nepovažují za důkaz původu.
        existing = self.store.rows(
            "events",
            "project_id=? AND operation='storyboard_materialized'",
            (project_id,),
            order="created_at DESC",
        )
        for event in existing:
            if event["data"].get("storyboard_id") != storyboard["id"]:
                continue
            panel_ids = list(event["data"].get("panel_ids") or [])
            try:
                panels = [self.store.get("panels", panel_id) for panel_id in panel_ids]
            except ComicError as exc:
                raise ComicError(
                    "materialization_conflict",
                    "Historická materializace je neúplná; automatická oprava "
                    "původu panelů je zablokována.",
                ) from exc
            if panel_ids and all(
                panel["project_id"] == project_id for panel in panels
            ):
                return panel_ids
            raise ComicError(
                "materialization_conflict",
                "Historická materializace nemá úplnou sadu panelů.",
            )

        entities = {
            row["id"]: row
            for row in self.store.rows(
                "entities",
                "project_id=? AND archived=0",
                (project_id,),
                order="created_at",
            )
        }
        specs = sorted(
            storyboard["result"]["panels"],
            key=lambda row: row["position"],
        )
        positions = [int(row["position"]) for row in specs]
        if len(positions) != len(set(positions)):
            raise ComicError(
                "invalid_storyboard",
                "Storyboard obsahuje duplicitní pozici panelu.",
            )

        default_format = {
            "width": 2048,
            "height": 2048,
            "dpi": 300,
            "fit": "pad",
            "experimental": False,
        }
        prepared = []
        for panel_spec in specs:
            nodes = [
                {
                    "type": "text",
                    "text": (
                        f"SHOT: {panel_spec['shot']}\n"
                        f"VISUAL: {panel_spec['visual']}"
                    ),
                }
            ]
            for entity_id in panel_spec["entity_ids"]:
                entity = entities.get(entity_id)
                if entity is None:
                    raise ComicError(
                        "broken_reference",
                        "Storyboard odkazuje na entitu, která již není aktivní.",
                    )
                nodes.append(
                    {
                        "type": entity["kind"] + "_ref",
                        "entity_id": entity_id,
                    }
                )
            overlays = []
            for line in panel_spec["dialogue"]:
                overlays.append(
                    {
                        "kind": "dialog",
                        "text": f"{line['speaker']}: {line['text']}",
                        "x": 0.08,
                        "y": min(0.08 + 0.14 * len(overlays), 0.64),
                        "w": 0.42,
                        "h": 0.12,
                        "tail_x": 0.50,
                        "tail_y": 0.50,
                        "font_size": 0.04,
                    }
                )
            if panel_spec["caption"]:
                overlays.append(
                    {
                        "kind": "caption",
                        "text": panel_spec["caption"],
                        "x": 0.08,
                        "y": 0.82,
                        "w": 0.84,
                        "h": 0.10,
                        "tail_x": 0.50,
                        "tail_y": 0.50,
                        "font_size": 0.035,
                    }
                )
            prepared.append(
                {
                    "storyboard_position": int(panel_spec["position"]),
                    "name": f"Storyboard {int(panel_spec['position']):03d}",
                    "document": {"version": 1, "nodes": nodes},
                    "format": default_format,
                    "overlays": overlays,
                }
            )

        # Store validates every prepared row again inside BEGIN IMMEDIATE and
        # creates the complete set plus provenance event in that one transaction.
        return self.store.materialize_storyboard_panels(
            project_id,
            storyboard["id"],
            continuity["id"],
            prepared,
        )

    def _entity(self, operation):
        snap = operation["snapshot"]
        if "descriptor" not in snap:
            result, provenance = self.text_request(operation,
                "Vytvoř přesný canonical descriptor identity postavy nebo dispozice prostředí podle referencí. "
                "Zachovej uživatelský popis, rozpoznatelné detaily a pravidla bible. Piš česky.",
                DESCRIPTOR_SCHEMA, {"entity": snap["entity"], "bible": snap["bible"]["result"]}, snap["assets"])
            snap.update(descriptor=result["descriptor"], descriptor_provenance=provenance)
            self.update_operation(operation["id"], snapshot=snap)
        log = self.logger(operation)
        if "image_result" not in snap:
            if snap.get("image_archive"):
                response = json.loads(gzip.decompress(self.store.asset_path(snap["image_archive"]).read_bytes()))
                body = snap["image_parameters"]
            else:
                if snap.get("image_submitting"):
                    raise SubmissionUnknown("Živá obrazová operace nemá potvrzený výsledek. Automatické opakování je zablokováno.")
                refs = [self.upload(asset, log) for asset in snap["assets"]]
                sheet = "Jeden přehledný character sheet, tři velké pohledy: zepředu, tříčtvrteční a profil. Zachovej stejnou identitu." if snap["entity"]["kind"] == "character" else "Canonical reference prostředí: hlavní široký pohled a dva výrazné detaily. Zachovej dispozici a objekty."
                prompt = sheet + " Bez nápisů. Styl bible je závazný.\n" + canonical(snap["bible"]["result"]) + "\n" + snap["descriptor"]
                body = self.image_body(prompt, "1536x1024", refs)
                validate_image_request("/v1/images/edits", body)
                log.bundle.record_request(body, endpoint="/v1/images/edits", name="COMIC_REFERENCE")
                self.check_stop()
                snap["image_submitting"] = True
                self.update_operation(operation["id"], snapshot=snap)
                self.progress("COMIC_REFERENCE", detail="Vytvářím obrazovou referenci")
                projection = {
                    "entity_id": snap["entity"]["id"],
                    "bible_id": snap["bible"]["id"],
                    "descriptor_sha256": hashlib.sha256(
                        snap["descriptor"].encode("utf-8")
                    ).hexdigest(),
                    "asset_sha256": [
                        self.store.get("assets", asset)["sha256"]
                        for asset in snap["assets"]
                    ],
                }
                _repo, image_order = self._prepare_image_effect(
                    operation,
                    log,
                    task_id="entity-image-" + snap["entity"]["id"],
                    route="image_live",
                    target_id=snap["entity"]["id"],
                    body={
                        "endpoint": "/v1/images/edits",
                        **body,
                    },
                    projection=projection,
                )
                mark_submission_started(log, image_order)
                try:
                    response = self.client.create_image(
                        "/v1/images/edits", body
                    )
                except Exception as exc:
                    if getattr(exc, "status_code", None) in (
                        400, 401, 403, 404, 422, 429
                    ) or getattr(exc, "request_sent", None) is False:
                        mark_not_submitted(log, image_order)
                        snap["image_submitting"] = False
                        self.update_operation(operation["id"], snapshot=snap)
                        raise
                    mark_submission(
                        log, image_order, None, unknown=True
                    )
                    raise SubmissionUnknown(
                        "Obrazový submit nemá potvrzený výsledek; neposílám jej podruhé."
                    ) from exc
                provider_identity = str(
                    response.get("id")
                    or response.get("_request_id")
                    or (
                        "image-response-"
                        + canonical_sha256(
                            {
                                "operation": operation["id"],
                                "response": response,
                            }
                        )[:32]
                    )
                )
                mark_submission(
                    log,
                    image_order,
                    provider_identity,
                    unknown=False,
                )
                self._record_image_usage(
                    log,
                    image_order.attempt_id,
                    provider_item_id=provider_identity,
                    usage=response.get("usage") or {},
                )
                archive = self.store.asset(operation["project_id"], gzip.compress(canonical(response).encode(), mtime=0), {"role": "provider_archive", "encoding": "gzip"})
                snap.update(image_archive=archive, image_parameters=body)
                self.update_operation(operation["id"], snapshot=snap)
                log.bundle.archive_artifact(self.store.asset_path(archive), role="output", kind="binary", metadata={"encoding": "gzip"})
            snap["image_result"] = self.capture_image(operation, response, log)
            snap["image_parameters"] = body
            self.update_operation(operation["id"], snapshot=snap)
        result = snap["image_result"]
        inspect_image(self.store.asset_path(result["asset_id"]).read_bytes())
        with self.store.transaction() as db:
            existing = db.execute("SELECT id FROM entity_revisions WHERE entity_id=? AND asset_id=?", (snap["entity"]["id"], result["asset_id"])).fetchone()
            if not existing:
                revision = uid()
                db.execute("INSERT INTO entity_revisions VALUES(?,?,?,?,?,?,?)", (revision, snap["entity"]["id"], snap["bible"]["id"], snap["descriptor"], result["asset_id"], canonical({"operation_id": operation["id"], "model": IMAGE_MODEL, "parameters": snap["image_parameters"], "response": result["response"]}), now()))
                db.execute("UPDATE entities SET active_revision=?,revision=revision+1 WHERE id=? AND revision=?", (revision, snap["entity"]["id"], snap["entity"]["revision"]))
                self.store.event(db, operation["project_id"], "entity_ready", {"entity_id": snap["entity"]["id"], "revision_id": revision})
        self.update_operation(operation["id"], "completed")

    def image_body(self, prompt, size, refs):
        body = {"model": IMAGE_MODEL, "prompt": prompt, "size": size, "quality": "max", "n": 1,
                "output_format": "png", "background": "opaque"}
        if refs:
            body.update(images=[{"file_id": ref} for ref in refs])
        return body

    def _frozen_image_index(self, project_id, asset_ids):
        transform_hash = normalization_policy_hash()
        frozen = []
        for asset_id in sorted(set(asset_ids)):
            record = self.store.get("assets", asset_id)
            if record["project_id"] != project_id:
                raise ComicError("broken_reference", "Reference patří jinému komiksu.")
            data = self.store.asset_path(asset_id).read_bytes()
            if hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise ComicError("corrupt_asset", "Kontrolní otisk reference nesouhlasí.")
            inspect_image(data)
            metadata = record["metadata"]
            if metadata.get("normalization_policy_hash") == transform_hash:
                normalized, storage_id = data, asset_id
            else:
                candidates = self.store.rows(
                    "assets", "project_id=? AND json_extract(metadata, '$.original_asset')=? "
                    "AND json_extract(metadata, '$.normalization_policy_hash')=?",
                    (project_id, asset_id, transform_hash),
                )
                if candidates:
                    storage_id = candidates[0]["id"]
                    normalized = self.store.asset_path(storage_id).read_bytes()
                    if hashlib.sha256(normalized).hexdigest() != candidates[0]["sha256"]:
                        raise ComicError("corrupt_asset", "Změněná normalizovaná reference.")
                    inspect_image(normalized)
                else:
                    normalized = normalized_image(data)
                    storage_id = asset_id if normalized == data else self.store.asset(
                        project_id, normalized,
                        {"role": "working", "original_asset": asset_id,
                         "original_sha256": record["sha256"],
                         "normalization_policy_hash": transform_hash},
                    )
            frozen.append(FrozenImageAsset(
                asset_id, storage_id, hashlib.sha256(normalized).hexdigest(), transform_hash,
            ))
        return FrozenAssetIndex(tuple(frozen), image_capability()["max_references"])

    def compile_panel(self, panel_id, edit=""):
        panel = self.store.get("panels", panel_id)
        project = self.store.get("projects", panel["project_id"])
        if panel["deleted"] or not project["bible_id"]:
            raise ComicError("bible_missing", "Panel musí být aktivní a komiks musí mít sestavenou bibli.")
        bible = self.store.get("bibles", project["bible_id"])
        current_refs = [r["asset_id"] for r in self.store.references(project["id"])]
        if bible["input"]["style"] != project["style"] or bible["input"].get("assets", []) != current_refs:
            raise ComicError("bible_stale", "Nastavení stylu se změnilo. Znovu sestavte bibli.")
        document = self.store.get("prompts", panel["prompt_id"])["document"]
        validate_document(document)
        validate_overlays(panel["overlays"], project["style"]["sfx"])
        parts, entities, roles, seen = [], [], [], set()
        base = None
        if edit:
            checked_text(edit, "Požadavek editace", 4000, True)
            if not panel["active_version"]:
                raise ComicError("missing_version", "Panel zatím nemá aktivní verzi.")
            base = self.store.get("panel_versions", panel["active_version"])
            roles.append(ImageRole("EDIT_BASE", base["raw_asset_id"], "edit_base", "EDIT_BASE"))
        for node in document["nodes"]:
            if node["type"] == "text":
                parts.append(node["text"])
                continue
            entity = self.store.get("entities", node["entity_id"])
            if entity["project_id"] != project["id"] or entity["kind"] + "_ref" != node["type"] or entity["archived"] or not entity["active_revision"]:
                raise ComicError("broken_reference", f"{entity['name']}: reference není připravena nebo patří jinému komiksu.")
            parts.append("[" + entity["name"] + "]")
            if entity["id"] not in seen:
                revision = self.store.get("entity_revisions", entity["active_revision"])
                if revision["bible_id"] != project["bible_id"]:
                    raise ComicError("entity_style_stale", f"{entity['name']}: vytvořte referenci pro aktuální bibli.")
                seen.add(entity["id"])
                entities.append({"name": entity["name"], "id": entity["id"], "revision": revision["id"], "descriptor": revision["descriptor"]})
                roles.append(ImageRole("ENTITY-" + entity["id"], revision["asset_id"], "identity_reference", entity["id"]))
        for reference in self.store.references(project["id"]):
            asset_id = reference["asset_id"]
            roles.append(ImageRole("STYLE-" + asset_id, asset_id, "style_reference", asset_id))
        try:
            slots = compile_slots(roles, self._frozen_image_index(project["id"], [role.asset_id for role in roles]))
        except OrchestrationError as exc:
            raise ComicError(exc.code, str(exc)) from exc
        role_slots = {role.role_id: role for role in slots.roles}
        entities.sort(key=lambda entity: entity["id"])
        for entity in entities:
            slot = role_slots["ENTITY-" + entity["id"]]
            entity.update(image_index=slot.index, slot_id=slot.slot_id)
        assets = [slot.asset_id for slot in slots.slots]
        reference_slots = asdict(slots)
        scene = "".join(parts).strip()
        if not scene:
            raise ComicError("invalid_input", "Napište zadání panelu.")
        prompt = "Vytvoř jediný profesionální komiksový panel. Zachovej identitu všech referencí a styl bible. "
        prompt += "Nekresli dialogové bubliny, titulky ani SFX: přesný text přidává aplikace. Vyhraď volné místo podle textových oblastí.\n"
        prompt += canonical({"bible": bible["result"], "scene": scene, "entities": entities,
                             "reference_slots": reference_slots,
                             "text_areas": [{k: v for k, v in layer.items() if k != "text"} for layer in panel["overlays"]]})
        if edit:
            prompt += "\nEDITACE PRVNÍHO OBRÁZKU: Zachovej kompozici a vše mimo výslovnou změnu: " + edit
        fmt = PanelFormat(**panel["format"])
        endpoint = "/v1/images/edits" if assets else "/v1/images/generations"
        body = self.image_body(prompt, fmt.native_size(image_capability()), ["file_pending"] * len(assets))
        validate_image_request(endpoint, body)
        return {"panel_id": panel_id, "panel_revision": panel["revision"], "project_id": project["id"], "bible_id": bible["id"],
                "document": document, "entities": entities, "assets": assets, "reference_slots": reference_slots,
                "body": body, "endpoint": endpoint,
                "format": asdict(fmt), "overlays": panel["overlays"], "base_version": base["id"] if base else None, "edit": edit}

    def start_panels(self, project, panels, edit=""):
        if not panels or len(set(panels)) != len(panels):
            raise ComicError("invalid_input", "Vyberte alespoň jeden panel bez duplicit.")
        snapshots = [self.compile_panel(panel, edit) for panel in panels]
        if any(s["project_id"] != project for s in snapshots):
            raise ComicError("broken_reference", "Výběr obsahuje jiný komiks.")
        with self.store.execution_lock(project):
            for panel in panels:
                if self.store.rows("batch_items", "panel_id=? AND status IN ('prepared','submitted','received')", (panel,)):
                    raise ComicError("operation_active", "Některý panel již čeká na výsledek.")
            if self.store.get("projects", project)["deleted"]:
                raise ComicError("project_deleted", "Nejprve obnovte komiks z koše.")
            operation, run_id, stamp = uid(), "RUN_COMIC_" + uid(), now()
            with self.store.transaction() as db:
                kind = "edit" if edit else "panels"
                db.execute("INSERT INTO operations(id,project_id,kind,status,snapshot,run_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                           (operation, project, kind, "preparing", canonical({"panels": snapshots}), run_id, stamp, stamp))
                self.store.event(db, project, "start_" + kind, {"operation_id": operation})
                groups = {}
                for snapshot in snapshots:
                    groups.setdefault(snapshot["endpoint"], []).append(snapshot)
                for endpoint, items in groups.items():
                    chunk, size, batch_id = 0, 0, None
                    for snap in items:
                        estimate = len(canonical(snap["body"]).encode()) + 4096
                        if batch_id is None or chunk >= 50000 or size + estimate > 195000000:
                            batch_id, chunk, size = uid(), 0, 0
                            db.execute("INSERT INTO batches(id,operation_id,endpoint,status,created_at,updated_at) VALUES(?,?,?,'prepared',?,?)", (batch_id, operation, endpoint, now(), now()))
                        item_id = uid()
                        db.execute("INSERT INTO batch_items(id,batch_id,panel_id,custom_id,snapshot,status) VALUES(?,?,?,?,?,'prepared')",
                                   (item_id, batch_id, snap["panel_id"], "comic_" + item_id, canonical(snap)))
                        chunk += 1
                        size += estimate
            return operation

    def _batch(self, operation, *, allow_submit=True):
        log = self.logger(operation)
        batches = self.store.rows("batches", "operation_id=?", (operation["id"],))
        for batch in batches:
            self.check_stop()
            items = self.store.rows("batch_items", "batch_id=?", (batch["id"],), order="custom_id")
            for item in items:
                if item["status"] == "received":
                    self.store_panel_result(operation, item, log)
            if all(r["status"] == "completed" for r in self.store.rows("batch_items", "batch_id=?", (batch["id"],))):
                continue
            if batch["status"] == "cancelled" and not batch["provider_id"]:
                continue
            if not batch["provider_id"]:
                if batch["status"] == "rejected":
                    raise ComicError(
                        "batch_rejected",
                        "Provider dávku jednoznačně odmítl; nový placený submit vyžaduje explicitní retry.",
                    )
                if batch["status"] in ("submitting", "submission_unknown"):
                    matches = exact_batch_matches(self.client.list_batches(), batch["input_file_id"], batch["endpoint"])
                    if len(matches) != 1:
                        raise SubmissionUnknown("Neurčitý submit nelze jednoznačně dohledat. Novou dávku neposílám.")
                    provider_id = str(matches[0].get("id") or "")
                    if not provider_id:
                        raise SubmissionUnknown(
                            "Dohledaná COMIC dávka nemá provider ID."
                        )
                    repo, attempt_id = self._image_attempt_for_task(
                        log, "batch-" + batch["id"]
                    )
                    if not attempt_id:
                        raise ComicError(
                            "batch_binding_missing",
                            "Dohledaná COMIC dávka nemá centrální provider operaci.",
                        )
                    repo.mark_submitted(
                        attempt_id,
                        provider_id,
                        unknown=False,
                    )
                    self.save_batch(batch["id"], matches[0])
                    recovered_batch = self.store.get("batches", batch["id"])
                    self._verify_batch_operation_binding(
                        log, recovered_batch, provider_id=provider_id
                    )
                else:
                    if not allow_submit:
                        continue
                    rows = []
                    for index, item in enumerate(items):
                        self.check_stop()
                        snap = item["snapshot"]
                        refs = [self.upload(asset, log) for asset in snap["assets"]]
                        body = self.image_body(snap["body"]["prompt"], snap["body"]["size"], refs)
                        validate_image_request(batch["endpoint"], body)
                        rows.append({"custom_id": item["custom_id"], "method": "POST", "url": batch["endpoint"], "body": body})
                        self.progress("COMIC_PREPARING", index + 1, len(items))
                    raw = ("\n".join(canonical(r) for r in rows) + "\n").encode()
                    if len(raw) > 200000000:
                        raise ComicError("batch_too_large", "Pracovní JSONL přesahuje 200 MB.")
                    asset = self.store.asset(operation["project_id"], raw, {"role": "batch_input"})
                    path = self.store.asset_path(asset)
                    log.bundle.archive_artifact(path, role="input", kind="jsonl")
                    uploaded = self.client.upload_file(str(path), purpose="batch")
                    file_id = uploaded["id"]
                    with self.store.transaction() as db:
                        db.execute("UPDATE batches SET input_file_id=?,status='submitting' WHERE id=?", (file_id, batch["id"]))
                    for row in rows:
                        log.bundle.record_request(row["body"], name=row["custom_id"], endpoint=row["url"])
                    projection = {
                        "batch_id": batch["id"],
                        "endpoint": batch["endpoint"],
                        "input_file_id": file_id,
                        "rows": [
                            {
                                "custom_id": row["custom_id"],
                                "body_sha256": canonical_sha256(
                                    row["body"]
                                ),
                            }
                            for row in rows
                        ],
                    }
                    _repo, batch_order = self._prepare_image_effect(
                        operation,
                        log,
                        task_id="batch-" + batch["id"],
                        route="image_batch",
                        target_id=batch["id"],
                        body={
                            "endpoint": batch["endpoint"],
                            "input_file_id": file_id,
                            "rows": rows,
                        },
                        projection=projection,
                    )
                    _repo.set_remote_input_file(batch_order.attempt_id, file_id)
                    mark_submission_started(log, batch_order)
                    self.progress("COMIC_SUBMITTING", detail="Odesílám pracovní dávku")
                    try:
                        payload = self.client.create_image_batch(file_id, rows)
                    except Exception as exc:
                        if getattr(exc, "status_code", None) in (
                            400, 401, 403, 404, 422, 429
                        ) or getattr(exc, "request_sent", None) is False:
                            mark_not_submitted(log, batch_order)
                            with self.store.transaction() as db:
                                db.execute("UPDATE batches SET status='rejected' WHERE id=?", (batch["id"],))
                            raise
                        mark_submission(
                            log,
                            batch_order,
                            None,
                            unknown=True,
                        )
                        with self.store.transaction() as db:
                            db.execute(
                                "UPDATE batches SET status='submission_unknown' WHERE id=?",
                                (batch["id"],),
                            )
                        raise SubmissionUnknown("Výsledek odeslání dávky není znám; obnova jej nejprve dohledá.") from exc
                    if not payload.get("id"):
                        mark_submission(
                            log,
                            batch_order,
                            None,
                            unknown=True,
                        )
                        with self.store.transaction() as db:
                            db.execute(
                                "UPDATE batches SET status='submission_unknown' WHERE id=?",
                                (batch["id"],),
                            )
                        raise SubmissionUnknown("OpenAI nevrátilo ID pracovní dávky.")
                    mark_submission(
                        log,
                        batch_order,
                        str(payload["id"]),
                        unknown=False,
                    )
                    self.save_batch(batch["id"], payload)
            batch = self.store.get("batches", batch["id"])
            if batch["provider_id"]:
                self._verify_batch_operation_binding(
                    log, batch, provider_id=str(batch["provider_id"])
                )
            payload = batch["payload"] if batch["status"] in TERMINAL and batch["payload"].get("_local_results") else self.client.retrieve_batch(batch["provider_id"])
            if payload.get("id") != batch["provider_id"] or payload.get("status") not in TERMINAL | {"validating", "in_progress", "finalizing", "cancelling"}:
                raise ComicError("invalid_batch_response", "OpenAI vrátilo jiné ID dávky nebo neznámý stav.")
            self.save_batch(batch["id"], payload)
            counts = payload.get("request_counts") or {}
            self.progress("BATCH", (counts.get("completed", 0) + counts.get("failed", 0)), counts.get("total", len(items)), payload["status"])
            if payload["status"] in TERMINAL:
                self.ingest(operation, batch, payload, log)
        statuses = [r["status"] for r in self.store.rows("batch_items", "batch_id IN (SELECT id FROM batches WHERE operation_id=?)", (operation["id"],))]
        status = "completed" if statuses and all(s == "completed" for s in statuses) else "partial" if "completed" in statuses and not any(s in ("prepared", "submitted", "received") for s in statuses) else "failed" if statuses and all(s == "failed" for s in statuses) else "batch_pending"
        self.update_operation(operation["id"], status)
        records = self.store.rows("batches", "operation_id=?", (operation["id"],))
        if records and all(b["status"] == "cancelled" for b in records) and "completed" not in statuses:
            self.update_operation(operation["id"], "cancelled")
        log.update_state({"comic_batch_ids": [b["provider_id"] for b in records if b["provider_id"]],
                          "batch_imports": {b["provider_id"]: {"import_status": "comic_completed" if all(i["status"] == "completed" for i in self.store.rows("batch_items", "batch_id=?", (b["id"],))) else "pending"} for b in records if b["provider_id"]}})
        log.bundle.update_run({"related_batch_ids": [b["provider_id"] for b in records if b["provider_id"]]})

    def save_batch(self, identifier, payload):
        if not isinstance(payload, dict):
            raise ComicError("invalid_batch_response", "Provider batch odpověď musí být objekt.")
        payload = copy.deepcopy(payload)
        current = self.store.get("batches", identifier)
        provider_id = str(payload.get("id") or "")
        status = str(payload.get("status") or "")
        allowed_statuses = TERMINAL | {
            "validating", "in_progress", "finalizing", "cancelling"
        }
        if not provider_id or status not in allowed_statuses:
            raise ComicError(
                "invalid_batch_response",
                "Provider batch odpověď nemá platné ID nebo stav.",
            )
        if current["provider_id"] and current["provider_id"] != provider_id:
            raise ComicError(
                "batch_binding_mismatch",
                "Provider batch ID se liší od již uložené identity dávky.",
            )
        response_input = str(payload.get("input_file_id") or "")
        if response_input and response_input != str(current["input_file_id"] or ""):
            raise ComicError(
                "batch_binding_mismatch",
                "Provider batch odpověď odkazuje na jiné input_file_id.",
            )
        response_endpoint = str(payload.get("endpoint") or "")
        if response_endpoint and response_endpoint != str(current["endpoint"] or ""):
            raise ComicError(
                "batch_binding_mismatch",
                "Provider batch odpověď odkazuje na jiný řádkový endpoint.",
            )
        previous = current["payload"]
        if previous.get("_local_results"):
            payload["_local_results"] = previous["_local_results"]
        with self.store.transaction() as db:
            db.execute(
                "UPDATE batches SET provider_id=?,status=?,payload=?,updated_at=? WHERE id=?",
                (provider_id, status, canonical(payload), now(), identifier),
            )
            db.execute(
                "UPDATE batch_items SET status='submitted' WHERE batch_id=? AND status='prepared'",
                (identifier,),
            )

    def capture_image(self, operation, response, log):
        result = copy.deepcopy(response)
        data = result.get("data")
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict) or not data[0].get("b64_json"):
            raise ComicError("missing_output", "API nevrátilo jeden úplný obrazový výsledek.")
        try:
            binary = base64.b64decode(data[0].pop("b64_json"), validate=True)
        except (ValueError, TypeError) as exc:
            raise ComicError("corrupt_output", "Výsledek není platný base64 obrázek.") from exc
        asset = self.store.asset(operation["project_id"], binary, {"role": "generated_raw"})
        artifact = log.bundle.archive_artifact(self.store.asset_path(asset), role="output", kind="image")
        data[0]["binary_artifact"] = {"asset_id": asset, "artifact_id": artifact["artifact_id"], "encoding": "base64"}
        log.save_json(
            "responses",
            "COMIC_IMAGE_" + asset,
            {"image_evidence_version": 1, "response": result},
        )
        return {"asset_id": asset, "response": result}

    def ingest(self, operation, batch, payload, log):
        items = self.store.rows("batch_items", "batch_id=?", (batch["id"],), order="custom_id")
        by_id = {r["custom_id"]: r for r in items}
        rows, seen = [], set()
        for key in ("output_file_id", "error_file_id"):
            if not payload.get(key):
                continue
            self.progress("COMIC_RETRIEVING", detail="Stahuji výsledky")
            local_results = self.store.get("batches", batch["id"])["payload"].get("_local_results", {})
            if key in local_results:
                raw = gzip.decompress(self.store.asset_path(local_results[key]).read_bytes())
            else:
                raw = self.client.file_content(payload[key])
                archive = self.store.asset(operation["project_id"], gzip.compress(raw, mtime=0), {"role": "provider_archive", "encoding": "gzip", "file_id": payload[key]})
                log.bundle.archive_artifact(self.store.asset_path(archive), role="output", kind="binary", metadata={"encoding": "gzip", "file_id": payload[key]})
                local_results[key] = archive
                saved = self.store.get("batches", batch["id"])["payload"]
                saved["_local_results"] = local_results
                with self.store.transaction() as db:
                    db.execute("UPDATE batches SET payload=? WHERE id=?", (canonical(saved), batch["id"]))
            try:
                for line in raw.decode("utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    cid = row.get("custom_id")
                    if cid not in by_id or cid in seen:
                        raise ComicError("corrupt_output", "Výsledek obsahuje neznámé nebo duplicitní custom_id.")
                    seen.add(cid)
                    rows.append(row)
            except (UnicodeError, ValueError, AttributeError) as exc:
                if isinstance(exc, ComicError):
                    raise
                raise ComicError("corrupt_output", "Výsledný JSONL je poškozený.") from exc
        for row in rows:
            item = by_id[row["custom_id"]]
            if item["status"] == "completed":
                continue
            self.check_stop()
            response = row.get("response") or {}
            body = response.get("body") or {}
            repo, attempt_id = self._image_attempt_for_task(
                log, "batch-" + batch["id"]
            )
            if attempt_id and isinstance(body, dict):
                self._record_image_usage(
                    log,
                    attempt_id,
                    provider_item_id=(
                        str(batch.get("provider_id") or payload.get("id") or "")
                        + ":"
                        + str(row["custom_id"])
                    ),
                    usage=body.get("usage") or {},
                )
            if row.get("error") or response.get("status_code", 200) >= 400:
                error = row.get("error") or (response.get("body") or {}).get("error") or {"code": "openai_error"}
                self.item_error(item["id"], error)
                continue
            if item["status"] != "received":
                try:
                    captured = self.capture_image(operation, response.get("body") or {}, log)
                    with self.store.transaction() as db:
                        db.execute("UPDATE batch_items SET result=?,status='received' WHERE id=?", (canonical(captured), item["id"]))
                    item["result"] = captured
                except ComicError as exc:
                    self.item_error(item["id"], {"code": exc.code, "message": str(exc)})
                    continue
            self.store_panel_result(operation, item, log)
        for item in items:
            current = self.store.get("batch_items", item["id"])
            if current["status"] == "received":
                self.store_panel_result(operation, current, log)
            elif item["custom_id"] not in seen and current["status"] != "completed":
                self.item_error(item["id"], {"code": "batch_" + payload["status"], "message": "Dávka neobsahuje výsledek této položky."})

    def item_error(self, item_id, error):
        error = dict(error)
        error.setdefault("code", "openai_error")
        error.setdefault("message", "Poskytovatel nevrátil výsledek panelu.")
        error.setdefault("retryable", error["code"] in ("rate_limit_exceeded", "server_error", "batch_expired", "batch_cancelled", "missing_output", "corrupt_output"))
        item = self.store.get("batch_items", item_id)
        panel = self.store.get("panels", item["panel_id"])
        with self.store.transaction() as db:
            db.execute("UPDATE batch_items SET status='failed',error=? WHERE id=?", (canonical(error), item_id))
            self.store.event(db, panel["project_id"], "panel_failed", {"item_id": item_id, "code": error["code"], "retryable": error["retryable"]})

    def store_panel_result(self, operation, item, log):
        snap, result = item["snapshot"], item["result"]
        raw = self.store.asset_path(result["asset_id"]).read_bytes()
        try:
            info = inspect_image(raw)
            if (info["width"], info["height"]) != tuple(map(int, snap["body"]["size"].split("x"))):
                raise ComicError("unexpected_output_size", "Rozměry výstupu se liší od objednaného generování.")
        except ComicError as exc:
            self.item_error(item["id"], {"code": exc.code, "message": str(exc), "retryable": True})
            return
        processed, transform = postprocess(raw, PanelFormat(**snap["format"]))
        final = self.store.asset(operation["project_id"], processed, {"role": "panel", "transform": transform})
        log.bundle.archive_artifact(self.store.asset_path(final), role="output", kind="image")
        with self.store.transaction() as db:
            previous = db.execute("SELECT id FROM panel_versions WHERE item_id=?", (item["id"],)).fetchone()
            if not previous:
                version = uid()
                db.execute("INSERT INTO panel_versions VALUES(?,?,?,?,?,?,?,?,?)", (version, snap["panel_id"], item["id"], snap["base_version"], final, result["asset_id"], canonical(snap["overlays"]), canonical({"snapshot": snap, "transform": transform, "response": result["response"], "operation_id": operation["id"]}), now()))
                if not snap["base_version"]:
                    db.execute("UPDATE panels SET active_version=? WHERE id=? AND revision=?", (version, snap["panel_id"], snap["panel_revision"]))
                self.store.event(db, operation["project_id"], "panel_version_created", {"panel_id": snap["panel_id"], "version_id": version})
            db.execute("UPDATE batch_items SET status='completed',error='{}' WHERE id=?", (item["id"],))

    def retry_failed(self, operation_id):
        operation = self.store.get("operations", operation_id)
        items = self.store.rows("batch_items", "batch_id IN (SELECT id FROM batches WHERE operation_id=?) AND status='failed'", (operation_id,))
        if not items:
            raise ComicError("nothing_to_retry", "Operace nemá chybné panely.")
        return self.start_panels(operation["project_id"], [i["panel_id"] for i in items], items[0]["snapshot"].get("edit", ""))

    def cancel(self, identifier):
        with self.store.execution_lock(identifier):
            self.store.get("operations", identifier)
            batches = self.store.rows("batches", "operation_id=?", (identifier,))
            if not batches:
                raise ComicError("cancel_unavailable", "U této operace lze zastavit pouze místní čekání.")
            for batch in batches:
                if batch["provider_id"] and batch["status"] not in TERMINAL:
                    self.save_batch(batch["id"], self.client.cancel_batch(batch["provider_id"]))
                elif batch["status"] in ("submitting", "submission_unknown"):
                    raise SubmissionUnknown("Nejprve obnovte dohledání neznámého submitu.")
                elif not batch["provider_id"]:
                    with self.store.transaction() as db:
                        db.execute("UPDATE batches SET status='cancelled' WHERE id=?", (batch["id"],))
                        db.execute("UPDATE batch_items SET status='failed',error=? WHERE batch_id=?", (canonical({"code": "cancelled", "message": "Zrušeno před odesláním."}), batch["id"]))
            self.update_operation(identifier, "batch_pending")
        return self.run(identifier)

    def duplicate_project(self, project_id):
        """Kopie remapuje entity a verze; aktivní dávky nepřebírá."""
        store = self.store
        with store.execution_lock(project_id):
            source = store.get("projects", project_id)
            target = store.project(source["name"] + " – kopie", source["description"], source["style"])
            asset_map, bible_map, entity_map = {}, {}, {}
            def clone_asset(identifier):
                if identifier not in asset_map:
                    old = store.get("assets", identifier)
                    asset_map[identifier] = store.asset(target, store.asset_path(identifier).read_bytes(), {**old["metadata"], "copied_from": identifier})
                return asset_map[identifier]
            for ref in store.references(project_id):
                store.add_reference(target, clone_asset(ref["asset_id"]))
            for bible in store.rows("bibles", "project_id=?", (project_id,)):
                bible_map[bible["id"]] = uid()
                source_input = copy.deepcopy(bible["input"])
                source_input["assets"] = [clone_asset(value) for value in source_input.get("assets", [])]
                with store.transaction() as db:
                    db.execute("INSERT INTO bibles VALUES(?,?,?,?,?,?)", (bible_map[bible["id"]], target, canonical(source_input), canonical(bible["result"]), canonical({"copied_from": bible["id"], "provenance": bible["provenance"]}), now()))
            with store.transaction() as db:
                if source["bible_id"]:
                    db.execute("UPDATE projects SET bible_id=? WHERE id=?", (bible_map[source["bible_id"]], target))
            for entity in store.rows("entities", "project_id=?", (project_id,)):
                self.check_stop()
                new = store.entity(target, entity["kind"], entity["name"], entity["description"])
                entity_map[entity["id"]] = new
                for ref in store.references(project_id, entity["id"]):
                    store.add_reference(target, clone_asset(ref["asset_id"]), new, ref["role"])
                for revision in store.rows("entity_revisions", "entity_id=?", (entity["id"],)):
                    asset = clone_asset(revision["asset_id"])
                    new_revision = uid()
                    with store.transaction() as db:
                        db.execute("INSERT INTO entity_revisions VALUES(?,?,?,?,?,?,?)", (new_revision, new, bible_map[revision["bible_id"]], revision["descriptor"], asset, canonical({"copied_from": revision["id"]}), now()))
                        if revision["id"] == entity["active_revision"]:
                            db.execute("UPDATE entities SET active_revision=? WHERE id=?", (new_revision, new))
                if entity["archived"]:
                    store.archive_entity(new)
            document_map = {}

            def remap_document_value(value):
                if isinstance(value, str):
                    return entity_map.get(value, value)
                if isinstance(value, list):
                    return [remap_document_value(item) for item in value]
                if isinstance(value, dict):
                    return {
                        key: remap_document_value(item)
                        for key, item in value.items()
                    }
                return value

            for document in store.rows(
                "comic_documents",
                "project_id=?",
                (project_id,),
                order="created_at",
            ):
                new_document = uid()
                document_map[document["id"]] = new_document
                with store.transaction() as db:
                    db.execute(
                        "INSERT INTO comic_documents"
                        "(id,project_id,kind,source_id,input,result,provenance,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)",
                        (
                            new_document,
                            target,
                            document["kind"],
                            document_map.get(document["source_id"]),
                            canonical(remap_document_value(document["input"])),
                            canonical(remap_document_value(document["result"])),
                            canonical({
                                "copied_from": document["id"],
                                "provenance": remap_document_value(
                                    document["provenance"]
                                ),
                            }),
                            now(),
                        ),
                    )
            for panel in store.rows("panels", "project_id=? AND deleted=0", (project_id,), order="position,created_at"):
                self.check_stop()
                new = store.panel(target, panel["name"])
                document = store.get("prompts", panel["prompt_id"])["document"]
                for node in document["nodes"]:
                    if node["type"] != "text":
                        node["entity_id"] = entity_map[node["entity_id"]]
                store.save_panel(new, 2, panel["name"], document, panel["format"], panel["overlays"])
                version_map = {}
                for version in store.rows("panel_versions", "panel_id=?", (panel["id"],)):
                    final, raw = clone_asset(version["asset_id"]), clone_asset(version["raw_asset_id"])
                    new_version = uid()
                    version_map[version["id"]] = new_version
                    with store.transaction() as db:
                        db.execute("INSERT INTO panel_versions VALUES(?,?,?,?,?,?,?,?,?)", (new_version, new, None, version_map.get(version["base_version"]), final, raw, canonical(version["overlays"]), canonical({"copied_from": version["id"]}), now()))
                        if version["id"] == panel["active_version"]:
                            db.execute("UPDATE panels SET active_version=? WHERE id=?", (new_version, new))
            return target
