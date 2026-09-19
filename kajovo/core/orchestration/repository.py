"""SQLite orchestration repository with transactional budget/effect identity."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256
from .errors import OrchestrationError

_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY, installed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL, scope_hash TEXT NOT NULL, policy_hash TEXT NOT NULL, config_json TEXT NOT NULL CHECK(json_valid(config_json)), status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, approval_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS work_orders(work_order_hash TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), task_id TEXT NOT NULL, attempt_no INTEGER NOT NULL CHECK(attempt_no BETWEEN 1 AND 3), body_ref TEXT NOT NULL, input_hash TEXT NOT NULL, schema_hash TEXT NOT NULL, prompt_hash TEXT NOT NULL, model TEXT NOT NULL, route TEXT NOT NULL, UNIQUE(run_id,task_id,attempt_no));
CREATE TABLE IF NOT EXISTS reservations(reservation_id TEXT PRIMARY KEY, work_order_hash TEXT NOT NULL UNIQUE REFERENCES work_orders(work_order_hash), state TEXT NOT NULL CHECK(state IN ('reserved','submitted','unknown','settled','released')), cost_microusd INTEGER CHECK(cost_microusd>=0), input_limit INTEGER NOT NULL CHECK(input_limit>=0), output_limit INTEGER NOT NULL CHECK(output_limit>=0), provider_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS provider_operations(attempt_id TEXT PRIMARY KEY, work_order_hash TEXT NOT NULL REFERENCES work_orders(work_order_hash), endpoint TEXT NOT NULL, request_hash TEXT NOT NULL, state TEXT NOT NULL, provider_id TEXT, remote_input_file_id TEXT, raw_response_ref TEXT, UNIQUE(endpoint,provider_id));
CREATE TABLE IF NOT EXISTS usage_records(provider TEXT NOT NULL, provider_item_id TEXT NOT NULL, attempt_id TEXT NOT NULL REFERENCES provider_operations(attempt_id), usage_json TEXT NOT NULL CHECK(json_valid(usage_json)), price_snapshot_hash TEXT, actual_cost_microusd INTEGER CHECK(actual_cost_microusd>=0), PRIMARY KEY(provider,provider_item_id));
CREATE TABLE IF NOT EXISTS task_events(event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), task_id TEXT, sequence INTEGER NOT NULL, type TEXT NOT NULL, payload_ref TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id,sequence));
CREATE TABLE IF NOT EXISTS artifacts(artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), kind TEXT NOT NULL, contract_name TEXT NOT NULL, payload_sha256 TEXT NOT NULL, envelope_ref TEXT NOT NULL, validation_ref TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifact_dependencies(artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id), dependency_id TEXT NOT NULL REFERENCES artifacts(artifact_id), dependency_hash TEXT NOT NULL, PRIMARY KEY(artifact_id,dependency_id));
CREATE TABLE IF NOT EXISTS verification_reports(report_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), target_hash TEXT NOT NULL, profile_hash TEXT NOT NULL, result TEXT NOT NULL, report_ref TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS domain_outbox(event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), domain TEXT NOT NULL, target_id TEXT NOT NULL, expected_revision INTEGER NOT NULL, payload_ref TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','applied','conflict')));
CREATE INDEX IF NOT EXISTS idx_provider_state ON provider_operations(state);
CREATE INDEX IF NOT EXISTS idx_runs_lineage ON runs(lineage_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id,sequence);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrchestrationRepository:
    def __init__(self, path: str | Path):
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(_SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO schema_version(version,installed_at) VALUES(1,?)",
                (_now(),),
            )

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def has_run(self, run_id: str) -> bool:
        with self.connect() as db:
            return db.execute(
                "SELECT 1 FROM runs WHERE run_id=?", (run_id,)
            ).fetchone() is not None

    def register_run(
        self,
        run_id: str,
        *,
        lineage_id: str,
        scope_hash: str,
        policy_hash: str,
        config: dict[str, Any],
        approval_id: str,
        status: str = "preparing",
    ) -> None:
        payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT lineage_id,scope_hash,policy_hash,config_json,approval_id FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            expected = (lineage_id, scope_hash, policy_hash, payload, approval_id)
            if row and tuple(row) != expected:
                db.rollback()
                raise OrchestrationError("RUN_ID_CONFLICT", "Run ID má jinou zmrazenou konfiguraci.")
            if not row:
                db.execute(
                    "INSERT INTO runs(run_id,lineage_id,scope_hash,policy_hash,config_json,status,approval_id) VALUES(?,?,?,?,?,?,?)",
                    (run_id, lineage_id, scope_hash, policy_hash, payload, status, approval_id),
                )
            db.commit()

    def register_work_order(self, order, *, body_ref: str, input_hash: str) -> None:
        value = order.to_dict() if hasattr(order, "to_dict") else dict(order)
        order_hash = getattr(order, "order_hash", None) or canonical_sha256(value)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT run_id,task_id,attempt_no,input_hash,schema_hash,prompt_hash,model,route FROM work_orders WHERE work_order_hash=?",
                (order_hash,),
            ).fetchone()
            expected = (
                value["run_id"], value["task_id"], value["attempt_no"], input_hash,
                value["schema_hash"], value["prompt_hash"], value["model"], value["route"],
            )
            if row and tuple(row) != expected:
                db.rollback()
                raise OrchestrationError("WORK_ORDER_CONFLICT", order_hash)
            if not row:
                db.execute(
                    "INSERT INTO work_orders(work_order_hash,run_id,task_id,attempt_no,body_ref,input_hash,schema_hash,prompt_hash,model,route) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (order_hash, value["run_id"], value["task_id"], value["attempt_no"], body_ref, input_hash,
                     value["schema_hash"], value["prompt_hash"], value["model"], value["route"]),
                )
            db.commit()

    def reserve(
        self,
        *,
        reservation_id: str,
        work_order_hash: str,
        cost_microusd: int | None,
        input_limit: int,
        output_limit: int,
        max_cost_microusd: int | None,
        max_input_tokens: int,
        max_output_tokens: int,
        max_paid_requests: int,
    ) -> bool:
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT cost_microusd,input_limit,output_limit,state FROM reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                expected = (cost_microusd, input_limit, output_limit)
                if existing:
                    if tuple(existing[:3]) != expected:
                        raise OrchestrationError("RESERVATION_CONFLICT", reservation_id)
                    db.commit()
                    return False
                run_id_row = db.execute(
                    "SELECT run_id FROM work_orders WHERE work_order_hash=?",
                    (work_order_hash,),
                ).fetchone()
                if not run_id_row:
                    raise OrchestrationError("WORK_ORDER_UNKNOWN", work_order_hash)
                run_id = run_id_row[0]
                summary = db.execute(
                    """
                    SELECT COUNT(*),
                           COALESCE(SUM(r.input_limit),0),
                           COALESCE(SUM(r.output_limit),0),
                           COALESCE(SUM(CASE WHEN r.state='released' THEN 0 ELSE r.cost_microusd END),0)
                    FROM reservations r
                    JOIN work_orders w ON w.work_order_hash=r.work_order_hash
                    WHERE w.run_id=? AND r.state!='released'
                    """,
                    (run_id,),
                ).fetchone()
                count, input_used, output_used, cost_used = map(int, summary)
                if count + 1 > max_paid_requests:
                    raise OrchestrationError("BUDGET_EXCEEDED", "Limit placených požadavků.")
                if input_used + input_limit > max_input_tokens:
                    raise OrchestrationError("BUDGET_EXCEEDED", "Limit vstupních tokenů.")
                if output_used + output_limit > max_output_tokens:
                    raise OrchestrationError("BUDGET_EXCEEDED", "Limit výstupních tokenů.")
                if (
                    max_cost_microusd is not None
                    and cost_microusd is not None
                    and cost_used + cost_microusd > max_cost_microusd
                ):
                    raise OrchestrationError("BUDGET_EXCEEDED", "Schválený cenový limit.")
                db.execute(
                    "INSERT INTO reservations(reservation_id,work_order_hash,state,cost_microusd,input_limit,output_limit,created_at) VALUES(?,?,?,?,?,?,?)",
                    (reservation_id, work_order_hash, "reserved", cost_microusd, input_limit, output_limit, _now()),
                )
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise


    def reserve_many(
        self,
        rows: list[dict[str, Any]],
        *,
        max_cost_microusd: int | None,
        max_input_tokens: int,
        max_output_tokens: int,
        max_paid_requests: int,
    ) -> None:
        if not rows:
            return
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                run_ids: set[str] = set()
                new_rows: list[dict[str, Any]] = []
                for row in rows:
                    existing = db.execute(
                        "SELECT cost_microusd,input_limit,output_limit FROM reservations WHERE reservation_id=?",
                        (row["reservation_id"],),
                    ).fetchone()
                    expected = (
                        row["cost_microusd"],
                        row["input_limit"],
                        row["output_limit"],
                    )
                    if existing:
                        if tuple(existing) != expected:
                            raise OrchestrationError(
                                "RESERVATION_CONFLICT", row["reservation_id"]
                            )
                        continue
                    run = db.execute(
                        "SELECT run_id FROM work_orders WHERE work_order_hash=?",
                        (row["work_order_hash"],),
                    ).fetchone()
                    if not run:
                        raise OrchestrationError(
                            "WORK_ORDER_UNKNOWN", row["work_order_hash"]
                        )
                    run_ids.add(str(run[0]))
                    new_rows.append(row)
                if len(run_ids) > 1:
                    raise OrchestrationError(
                        "BATCH_RUN_MISMATCH",
                        "Jedna rezervace dávky nesmí míchat různé runy.",
                    )
                if not new_rows:
                    db.commit()
                    return
                run_id = next(iter(run_ids))
                summary = db.execute(
                    """
                    SELECT COUNT(*),
                           COALESCE(SUM(r.input_limit),0),
                           COALESCE(SUM(r.output_limit),0),
                           COALESCE(SUM(CASE WHEN r.state='released' THEN 0 ELSE r.cost_microusd END),0)
                    FROM reservations r
                    JOIN work_orders w ON w.work_order_hash=r.work_order_hash
                    WHERE w.run_id=? AND r.state!='released'
                    """,
                    (run_id,),
                ).fetchone()
                count, input_used, output_used, cost_used = map(int, summary)
                proposed_input = sum(int(row["input_limit"]) for row in new_rows)
                proposed_output = sum(int(row["output_limit"]) for row in new_rows)
                proposed_known_cost = sum(
                    int(row["cost_microusd"])
                    for row in new_rows
                    if row["cost_microusd"] is not None
                )
                if count + len(new_rows) > max_paid_requests:
                    raise OrchestrationError("BUDGET_EXCEEDED", "Limit placených požadavků.")
                if input_used + proposed_input > max_input_tokens:
                    raise OrchestrationError("BUDGET_EXCEEDED", "Limit vstupních tokenů.")
                if output_used + proposed_output > max_output_tokens:
                    raise OrchestrationError("BUDGET_EXCEEDED", "Limit výstupních tokenů.")
                if (
                    max_cost_microusd is not None
                    and cost_used + proposed_known_cost > max_cost_microusd
                ):
                    raise OrchestrationError("BUDGET_EXCEEDED", "Schválený cenový limit.")
                for row in new_rows:
                    db.execute(
                        "INSERT INTO reservations(reservation_id,work_order_hash,state,cost_microusd,input_limit,output_limit,created_at) VALUES(?,?,?,?,?,?,?)",
                        (
                            row["reservation_id"],
                            row["work_order_hash"],
                            "reserved",
                            row["cost_microusd"],
                            row["input_limit"],
                            row["output_limit"],
                            _now(),
                        ),
                    )
                db.commit()
            except Exception:
                db.rollback()
                raise

    def release(self, reservation_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if not row or row[0] != "reserved":
                db.rollback()
                raise OrchestrationError("RELEASE_UNSAFE", reservation_id)
            db.execute(
                "UPDATE reservations SET state='released' WHERE reservation_id=?",
                (reservation_id,),
            )
            db.commit()

    def mark_submitted(self, reservation_id: str, provider_id: str | None, *, unknown: bool) -> None:
        state = "unknown" if unknown else "submitted"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,provider_id FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if not row or row[0] not in {"reserved", "submitted", "unknown"}:
                db.rollback()
                raise OrchestrationError("RESERVATION_STATE", reservation_id)
            if row[1] and provider_id and row[1] != provider_id:
                db.rollback()
                raise OrchestrationError("PROVIDER_ID_CONFLICT", reservation_id)
            db.execute(
                "UPDATE reservations SET state=?,provider_id=COALESCE(provider_id,?) WHERE reservation_id=?",
                (state, provider_id, reservation_id),
            )
            db.commit()

    def settle(
        self,
        reservation_id: str,
        *,
        provider: str,
        provider_item_id: str,
        usage: dict[str, Any],
        actual_cost_microusd: int | None,
        price_snapshot_hash: str | None,
    ) -> bool:
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                reservation = db.execute(
                    "SELECT work_order_hash,state FROM reservations WHERE reservation_id=?",
                    (reservation_id,),
                ).fetchone()
                if not reservation:
                    raise OrchestrationError("RESERVATION_UNKNOWN", reservation_id)
                work_order_hash, state = reservation
                if state == "released":
                    raise OrchestrationError("SETTLE_RELEASED", reservation_id)
                attempt_id = "ATTEMPT-" + work_order_hash[:24]
                db.execute(
                    "INSERT OR IGNORE INTO provider_operations(attempt_id,work_order_hash,endpoint,request_hash,state,provider_id) VALUES(?,?,?,?,?,?)",
                    (attempt_id, work_order_hash, "provider", work_order_hash, "completed", provider_item_id),
                )
                current = db.execute(
                    "SELECT usage_json,actual_cost_microusd FROM usage_records WHERE provider=? AND provider_item_id=?",
                    (provider, provider_item_id),
                ).fetchone()
                usage_json = json.dumps(usage or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if current:
                    if current != (usage_json, actual_cost_microusd):
                        raise OrchestrationError("USAGE_CONFLICT", provider_item_id)
                    db.commit()
                    return False
                db.execute(
                    "INSERT INTO usage_records(provider,provider_item_id,attempt_id,usage_json,price_snapshot_hash,actual_cost_microusd) VALUES(?,?,?,?,?,?)",
                    (provider, provider_item_id, attempt_id, usage_json, price_snapshot_hash, actual_cost_microusd),
                )
                db.execute(
                    "UPDATE reservations SET state='settled',provider_id=? WHERE reservation_id=?",
                    (provider_item_id, reservation_id),
                )
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def commit_transition(self, task_id: str, expected_revision: int, event: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT status,revision FROM runs WHERE run_id=?", (task_id,)).fetchone()
                if not row:
                    raise OrchestrationError("TASK_UNKNOWN", task_id)
                status, revision = row
                if revision != expected_revision:
                    raise OrchestrationError("REVISION_CONFLICT", task_id)
                event_id = str(event.get("event_id") or canonical_sha256(event))
                duplicate = db.execute("SELECT 1 FROM task_events WHERE event_id=?", (event_id,)).fetchone()
                if duplicate:
                    db.commit()
                    return {"task_id": task_id, "revision": revision, "status": status}
                new_status = str(event.get("status") or status)
                sequence = revision + 1
                db.execute(
                    "INSERT INTO task_events(event_id,run_id,sequence,type,payload_ref,created_at) VALUES(?,?,?,?,?,?)",
                    (event_id, task_id, sequence, str(event.get("type") or "transition"), canonical_sha256(event), _now()),
                )
                db.execute("UPDATE runs SET status=?,revision=? WHERE run_id=?", (new_status, sequence, task_id))
                db.commit()
                return {"task_id": task_id, "revision": sequence, "status": new_status}
            except Exception:
                db.rollback()
                raise


def repository_for_logger(logger) -> OrchestrationRepository:
    return OrchestrationRepository(Path(logger.paths.run_dir).parent / "orchestration.sqlite3")
