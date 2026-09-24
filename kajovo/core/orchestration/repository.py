"""SQLite orchestration repository for immutable work and provider-operation identity."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256
from .errors import OrchestrationError
from .work_order import attempt_identity

_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version(
    version INTEGER PRIMARY KEY,
    installed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs(
    run_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    config_json TEXT NOT NULL CHECK(json_valid(config_json)),
    status TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    approval_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS work_orders(
    work_order_hash TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    task_id TEXT NOT NULL,
    attempt_no INTEGER NOT NULL CHECK(attempt_no >= 1),
    body_ref TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    route TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    provider_endpoint TEXT NOT NULL,
    work_order_json TEXT NOT NULL CHECK(json_valid(work_order_json)),
    UNIQUE(run_id,task_id,attempt_no)
);
CREATE TABLE IF NOT EXISTS provider_operations(
    attempt_id TEXT PRIMARY KEY,
    work_order_hash TEXT NOT NULL REFERENCES work_orders(work_order_hash),
    endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    physical_request_hash TEXT,
    state TEXT NOT NULL CHECK(
        state IN ('prepared','submission_unknown','submitted','completed','not_submitted')
    ),
    provider_id TEXT,
    remote_input_file_id TEXT,
    raw_response_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_records(
    provider TEXT NOT NULL,
    provider_item_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL REFERENCES provider_operations(attempt_id),
    usage_json TEXT NOT NULL CHECK(json_valid(usage_json)),
    PRIMARY KEY(provider,provider_item_id,attempt_id)
);
CREATE TABLE IF NOT EXISTS task_events(
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    task_id TEXT,
    sequence INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id,sequence)
);
CREATE TABLE IF NOT EXISTS artifacts(
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    kind TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    envelope_ref TEXT NOT NULL,
    validation_ref TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_dependencies(
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    dependency_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    dependency_hash TEXT NOT NULL,
    PRIMARY KEY(artifact_id,dependency_id)
);
CREATE TABLE IF NOT EXISTS verification_reports(
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    target_hash TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    result TEXT NOT NULL,
    report_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS domain_outbox(
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    domain TEXT NOT NULL,
    target_id TEXT NOT NULL,
    expected_revision INTEGER NOT NULL,
    payload_ref TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','applied','conflict'))
);
CREATE INDEX IF NOT EXISTS idx_provider_state ON provider_operations(state);
CREATE INDEX IF NOT EXISTS idx_provider_id ON provider_operations(provider_id);
CREATE INDEX IF NOT EXISTS idx_runs_lineage ON runs(lineage_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id,sequence);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _columns(db: sqlite3.Connection, name: str) -> set[str]:
    if not _table_exists(db, name):
        return set()
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({name})")}


def _route_endpoint(route: str) -> str:
    if route in {"responses_batch", "image_batch"}:
        return "/v1/batches"
    if route == "responses_live":
        return "/v1/responses"
    if route == "image_live":
        return "/v1/images/edits"
    raise OrchestrationError(
        "WORK_ORDER_ROUTE_UNKNOWN",
        f"Legacy WorkOrder má neznámou route: {route}",
    )


def _migrate_work_order_contract(db: sqlite3.Connection) -> None:
    """Additive V3 persistence for exact canonical WorkOrder identity."""
    if not _table_exists(db, "work_orders"):
        return
    columns = _columns(db, "work_orders")
    additions = {
        "attempt_id": "TEXT",
        "provider_endpoint": "TEXT",
        "work_order_json": "TEXT",
    }
    for name, declaration in additions.items():
        if name not in columns:
            db.execute(f"ALTER TABLE work_orders ADD COLUMN {name} {declaration}")
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_work_orders_attempt_id
        ON work_orders(attempt_id) WHERE attempt_id IS NOT NULL
        """
    )


def _migrate_provider_operation_contract(db: sqlite3.Connection) -> None:
    if not _table_exists(db, "provider_operations"):
        return
    columns = _columns(db, "provider_operations")
    if "physical_request_hash" not in columns:
        db.execute(
            "ALTER TABLE provider_operations ADD COLUMN physical_request_hash TEXT"
        )
    db.execute(
        """
        UPDATE provider_operations
        SET physical_request_hash=request_hash
        WHERE physical_request_hash IS NULL AND endpoint != '/v1/batches'
        """
    )


def _migrate_legacy_economic_schema(db: sqlite3.Connection) -> None:
    """LEGACY-DATA-READER: převede starou SQLite evidenci na nefinanční V2 schema."""
    legacy_reservations = _table_exists(db, "reservations")
    legacy_usage = {
        "price_snapshot_hash",
        "actual_cost_microusd",
    } & _columns(db, "usage_records")
    provider_columns = _columns(db, "provider_operations")
    legacy_provider = bool(provider_columns) and "created_at" not in provider_columns
    if not (legacy_reservations or legacy_usage or legacy_provider):
        return

    old_provider_rows: list[sqlite3.Row] = []
    old_usage_rows: list[sqlite3.Row] = []
    old_reservation_rows: list[sqlite3.Row] = []
    db.row_factory = sqlite3.Row
    if _table_exists(db, "provider_operations"):
        old_provider_rows = list(db.execute("SELECT * FROM provider_operations"))
    if _table_exists(db, "usage_records"):
        old_usage_rows = list(db.execute("SELECT * FROM usage_records"))
    if legacy_reservations:
        old_reservation_rows = list(db.execute("SELECT * FROM reservations"))

    work_rows = {
        str(row["work_order_hash"]): row
        for row in db.execute(
            "SELECT work_order_hash,run_id,task_id,attempt_no,body_ref,route FROM work_orders"
        )
    }
    old_attempt_to_work = {
        str(row["attempt_id"]): str(row["work_order_hash"])
        for row in old_provider_rows
        if row["attempt_id"] and row["work_order_hash"]
    }
    provider_by_work = {
        str(row["work_order_hash"]): row
        for row in old_provider_rows
        if row["work_order_hash"]
    }
    reservation_by_work = {
        str(row["work_order_hash"]): row
        for row in old_reservation_rows
        if row["work_order_hash"]
    }

    db.executescript(
        """
        DROP TABLE IF EXISTS provider_operations_v2_migration;
        DROP TABLE IF EXISTS usage_records_v2_migration;
        CREATE TABLE provider_operations_v2_migration(
            attempt_id TEXT PRIMARY KEY,
            work_order_hash TEXT NOT NULL REFERENCES work_orders(work_order_hash),
            endpoint TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            state TEXT NOT NULL CHECK(
                state IN ('prepared','submission_unknown','submitted','completed','not_submitted')
            ),
            provider_id TEXT,
            remote_input_file_id TEXT,
            raw_response_ref TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE usage_records_v2_migration(
            provider TEXT NOT NULL,
            provider_item_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL REFERENCES provider_operations_v2_migration(attempt_id),
            usage_json TEXT NOT NULL CHECK(json_valid(usage_json)),
            PRIMARY KEY(provider,provider_item_id,attempt_id)
        );
        """
    )

    migrated_attempts: dict[str, str] = {}
    for work_hash, work in work_rows.items():
        provider = provider_by_work.get(work_hash)
        legacy = reservation_by_work.get(work_hash)
        if provider is None and legacy is None:
            continue
        new_attempt = attempt_identity(
            str(work["run_id"]),
            str(work["task_id"]),
            int(work["attempt_no"]),
        )
        old_state = str(provider["state"]) if provider is not None else str(legacy["state"])
        state_map = {
            "reserved": "prepared",
            "unknown": "submission_unknown",
            "released": "not_submitted",
            "settled": "completed",
        }
        state = state_map.get(old_state, old_state)
        if state not in {
            "prepared", "submission_unknown", "submitted", "completed", "not_submitted"
        }:
            state = "submission_unknown"
        provider_id = (
            provider["provider_id"]
            if provider is not None and "provider_id" in provider.keys()
            else (legacy["provider_id"] if legacy is not None else None)
        )
        endpoint = (
            str(provider["endpoint"])
            if provider is not None and provider["endpoint"]
            else _route_endpoint(str(work["route"]))
        )
        request_hash = (
            str(provider["request_hash"])
            if provider is not None and provider["request_hash"]
            else str(work["body_ref"])
        )
        remote_input = (
            provider["remote_input_file_id"]
            if provider is not None and "remote_input_file_id" in provider.keys()
            else None
        )
        raw_ref = (
            provider["raw_response_ref"]
            if provider is not None and "raw_response_ref" in provider.keys()
            else None
        )
        now = _now()
        db.execute(
            """
            INSERT OR REPLACE INTO provider_operations_v2_migration(
                attempt_id,work_order_hash,endpoint,request_hash,state,provider_id,
                remote_input_file_id,raw_response_ref,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                new_attempt,
                work_hash,
                endpoint,
                request_hash,
                state,
                provider_id,
                remote_input,
                raw_ref,
                now,
                now,
            ),
        )
        migrated_attempts[work_hash] = new_attempt

    for row in old_usage_rows:
        work_hash = old_attempt_to_work.get(str(row["attempt_id"]))
        new_attempt = migrated_attempts.get(work_hash or "")
        if not new_attempt:
            continue
        db.execute(
            """
            INSERT OR REPLACE INTO usage_records_v2_migration(
                provider,provider_item_id,attempt_id,usage_json
            ) VALUES(?,?,?,?)
            """,
            (
                row["provider"],
                row["provider_item_id"],
                new_attempt,
                row["usage_json"],
            ),
        )
        db.execute(
            """
            UPDATE provider_operations_v2_migration
            SET state='completed',updated_at=?
            WHERE attempt_id=?
            """,
            (_now(), new_attempt),
        )

    if _table_exists(db, "usage_records"):
        db.execute("DROP TABLE usage_records")
    if _table_exists(db, "provider_operations"):
        db.execute("DROP TABLE provider_operations")
    if legacy_reservations:
        db.execute("DROP TABLE reservations")
    db.execute(
        "ALTER TABLE provider_operations_v2_migration RENAME TO provider_operations"
    )
    db.execute("ALTER TABLE usage_records_v2_migration RENAME TO usage_records")
    db.row_factory = None


def _migrate_attempt_ordinals(db):
    """Ruční pokusy mají vlastní pořadí; automatický limit určuje WorkOrder."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='work_orders'").fetchone()[0]
    if "attempt_no BETWEEN 1 AND 3" not in sql:
        return
    db.commit()
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("BEGIN IMMEDIATE")
    try:
        definition = sql.replace("work_orders", "work_orders_attempt_migration", 1).replace("attempt_no BETWEEN 1 AND 3", "attempt_no >= 1")
        db.execute(definition)
        columns = ",".join('"' + row[1] + '"' for row in db.execute("PRAGMA table_info(work_orders)"))
        db.execute(f"INSERT INTO work_orders_attempt_migration ({columns}) SELECT {columns} FROM work_orders")
        db.execute("DROP TABLE work_orders")
        db.execute("ALTER TABLE work_orders_attempt_migration RENAME TO work_orders")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Migrace pořadí pokusů porušila reference evidence.")
        db.commit()
    except Exception:
        db.rollback()
        raise


class OrchestrationRepository:
    def __init__(self, path: str | Path):
        self.path = str(Path(path))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA foreign_keys=OFF")
            _migrate_legacy_economic_schema(db)
            db.executescript(_SCHEMA)
            _migrate_work_order_contract(db)
            _migrate_provider_operation_contract(db)
            _migrate_attempt_ordinals(db)
            db.executescript(_SCHEMA)
            db.execute("INSERT OR IGNORE INTO schema_version(version,installed_at) VALUES(4,?)", (_now(),))
            db.execute(
                "INSERT OR IGNORE INTO schema_version(version,installed_at) VALUES(2,?)",
                (_now(),),
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_version(version,installed_at) VALUES(3,?)",
                (_now(),),
            )
            db.commit()
        finally:
            db.close()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            with db:
                db.execute("PRAGMA foreign_keys=ON")
                yield db
        finally:
            db.close()

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
        payload = json.dumps(
            config, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT lineage_id,scope_hash,policy_hash,config_json,approval_id
                FROM runs WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            expected = (lineage_id, scope_hash, policy_hash, payload, approval_id)
            if row and tuple(row) != expected:
                db.rollback()
                raise OrchestrationError(
                    "RUN_ID_CONFLICT", "Run ID má jinou zmrazenou konfiguraci."
                )
            if not row:
                db.execute(
                    """
                    INSERT INTO runs(
                        run_id,lineage_id,scope_hash,policy_hash,config_json,status,approval_id
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        lineage_id,
                        scope_hash,
                        policy_hash,
                        payload,
                        status,
                        approval_id,
                    ),
                )
            db.commit()

    def register_work_order(self, order, *, body_ref: str, input_hash: str) -> str:
        value = order.to_dict() if hasattr(order, "to_dict") else dict(order)
        order_hash = getattr(order, "order_hash", None) or canonical_sha256(value)
        for label, digest in (("body_ref", body_ref), ("input_hash", input_hash)):
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(ch not in "0123456789abcdef" for ch in digest)
            ):
                raise OrchestrationError(
                    "WORK_ORDER_DIGEST_INVALID",
                    f"{label} musí být kanonický SHA-256.",
                )
        if input_hash != value["input_projection_hash"]:
            raise OrchestrationError(
                "WORK_ORDER_INPUT_HASH_MISMATCH",
                str(value.get("attempt_id") or order_hash),
            )
        work_order_json = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if canonical_sha256(json.loads(work_order_json)) != order_hash:
            raise OrchestrationError("WORK_ORDER_HASH_MISMATCH", order_hash)
        expected = (
            value["run_id"],
            value["task_id"],
            value["attempt_no"],
            body_ref,
            input_hash,
            value["schema_hash"],
            value["prompt_hash"],
            value["model"],
            value["route"],
            value["attempt_id"],
            value["provider_endpoint"],
            work_order_json,
        )
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT run_id,task_id,attempt_no,body_ref,input_hash,schema_hash,
                       prompt_hash,model,route,attempt_id,provider_endpoint,
                       work_order_json
                FROM work_orders WHERE work_order_hash=?
                """,
                (order_hash,),
            ).fetchone()
            if row:
                if tuple(row) != expected:
                    db.rollback()
                    raise OrchestrationError("WORK_ORDER_CONFLICT", order_hash)
                db.commit()
                return order_hash

            legacy = db.execute(
                """
                SELECT work_order_hash,body_ref,input_hash,schema_hash,prompt_hash,
                       model,route,attempt_id,provider_endpoint,work_order_json
                FROM work_orders WHERE run_id=? AND task_id=? AND attempt_no=?
                """,
                (value["run_id"], value["task_id"], value["attempt_no"]),
            ).fetchone()
            if legacy:
                # Historické V2 řádky neměly úplný kanonický JSON. Ty lze
                # pouze konzervativně znovu otevřít; novější řádek musí být
                # shodný bitově ve všech uložených kontraktech.
                if legacy[9] is not None:
                    current = (
                        value["run_id"],
                        value["task_id"],
                        value["attempt_no"],
                        legacy[1],
                        legacy[2],
                        legacy[3],
                        legacy[4],
                        legacy[5],
                        legacy[6],
                        legacy[7],
                        legacy[8],
                        legacy[9],
                    )
                    if current != expected:
                        db.rollback()
                        raise OrchestrationError("WORK_ORDER_CONFLICT", str(legacy[0]))
                elif tuple(legacy[2:7]) != (
                    input_hash,
                    value["schema_hash"],
                    value["prompt_hash"],
                    value["model"],
                    value["route"],
                ) or legacy[1] != body_ref:
                    db.rollback()
                    raise OrchestrationError("WORK_ORDER_CONFLICT", str(legacy[0]))
                db.commit()
                return str(legacy[0])

            db.execute(
                """
                INSERT INTO work_orders(
                    work_order_hash,run_id,task_id,attempt_no,body_ref,input_hash,
                    schema_hash,prompt_hash,model,route,attempt_id,
                    provider_endpoint,work_order_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    order_hash,
                    value["run_id"],
                    value["task_id"],
                    value["attempt_no"],
                    body_ref,
                    input_hash,
                    value["schema_hash"],
                    value["prompt_hash"],
                    value["model"],
                    value["route"],
                    value["attempt_id"],
                    value["provider_endpoint"],
                    work_order_json,
                ),
            )
            db.commit()
            return order_hash

    def prepare_provider_operation(
        self,
        *,
        attempt_id: str,
        work_order_hash: str,
        endpoint: str,
        request_hash: str,
        allow_existing: bool = False,
    ) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            work = db.execute(
                """
                SELECT body_ref,attempt_id,provider_endpoint,work_order_json
                FROM work_orders WHERE work_order_hash=?
                """,
                (work_order_hash,),
            ).fetchone()
            if not work:
                db.rollback()
                raise OrchestrationError("WORK_ORDER_UNKNOWN", work_order_hash)
            if work[0] != request_hash:
                db.rollback()
                raise OrchestrationError(
                    "PROVIDER_REQUEST_HASH_MISMATCH",
                    attempt_id,
                )
            if work[1] is not None and work[1] != attempt_id:
                db.rollback()
                raise OrchestrationError(
                    "PROVIDER_ATTEMPT_MISMATCH",
                    attempt_id,
                )
            if work[2] is not None and work[2] != endpoint:
                db.rollback()
                raise OrchestrationError(
                    "PROVIDER_ENDPOINT_MISMATCH",
                    attempt_id,
                )
            row = db.execute(
                """
                SELECT work_order_hash,endpoint,request_hash,state,physical_request_hash
                FROM provider_operations WHERE attempt_id=?
                """,
                (attempt_id,),
            ).fetchone()
            expected = (work_order_hash, endpoint, request_hash)
            if row:
                if tuple(row[:3]) != expected:
                    db.rollback()
                    raise OrchestrationError("PROVIDER_OPERATION_CONFLICT", attempt_id)
                if row[3] in {"submitted", "submission_unknown", "completed"}:
                    if allow_existing:
                        db.commit()
                        return False
                    db.rollback()
                    raise OrchestrationError(
                        "DUPLICATE_SUBMIT_BLOCKED",
                        f"{attempt_id}: provider operace už mohla být odeslána.",
                    )
                db.execute(
                    "UPDATE provider_operations SET state='prepared',updated_at=?,"
                    "physical_request_hash=CASE WHEN state='not_submitted' AND endpoint='/v1/batches' THEN NULL ELSE physical_request_hash END,"
                    "remote_input_file_id=CASE WHEN state='not_submitted' AND endpoint='/v1/batches' THEN NULL ELSE remote_input_file_id END "
                    "WHERE attempt_id=?",
                    (_now(), attempt_id),
                )
                db.commit()
                return False
            db.execute(
                """
                INSERT INTO provider_operations(
                    attempt_id,work_order_hash,endpoint,request_hash,
                    physical_request_hash,state,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    attempt_id,
                    work_order_hash,
                    endpoint,
                    request_hash,
                    request_hash if endpoint != "/v1/batches" else None,
                    "prepared",
                    _now(),
                    _now(),
                ),
            )
            db.commit()
            return True

    def bind_physical_request(
        self,
        attempt_id: str,
        *,
        physical_request_hash: str,
        remote_input_file_id: str | None = None,
    ) -> None:
        if (
            not isinstance(physical_request_hash, str)
            or len(physical_request_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in physical_request_hash)
        ):
            raise OrchestrationError("PHYSICAL_REQUEST_HASH_INVALID", attempt_id)
        if remote_input_file_id is not None and (
            not isinstance(remote_input_file_id, str)
            or not remote_input_file_id.strip()
        ):
            raise OrchestrationError("REMOTE_INPUT_FILE_INVALID", attempt_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT endpoint,state,physical_request_hash,remote_input_file_id
                FROM provider_operations WHERE attempt_id=?
                """,
                (attempt_id,),
            ).fetchone()
            if not row:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_UNKNOWN", attempt_id)
            endpoint, state, current_hash, current_file = row
            if state not in {"prepared", "not_submitted", "submission_unknown", "submitted"}:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_STATE", attempt_id)
            if current_hash and current_hash != physical_request_hash:
                db.rollback()
                raise OrchestrationError("PHYSICAL_REQUEST_HASH_CONFLICT", attempt_id)
            if current_file and remote_input_file_id and current_file != remote_input_file_id:
                db.rollback()
                raise OrchestrationError("REMOTE_INPUT_FILE_CONFLICT", attempt_id)
            if endpoint == "/v1/batches" and not (remote_input_file_id or current_file):
                db.rollback()
                raise OrchestrationError("REMOTE_INPUT_FILE_REQUIRED", attempt_id)
            db.execute(
                """
                UPDATE provider_operations
                SET physical_request_hash=?,
                    remote_input_file_id=COALESCE(remote_input_file_id,?),
                    updated_at=?
                WHERE attempt_id=?
                """,
                (physical_request_hash, remote_input_file_id, _now(), attempt_id),
            )
            db.commit()

    def mark_submission_started(self, attempt_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT state,endpoint,physical_request_hash,remote_input_file_id
                FROM provider_operations WHERE attempt_id=?
                """,
                (attempt_id,),
            ).fetchone()
            if not row:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_UNKNOWN", attempt_id)
            if row[0] == "completed":
                db.commit()
                return
            if not row[2] or (row[1] == "/v1/batches" and not row[3]):
                db.rollback()
                raise OrchestrationError(
                    "PHYSICAL_REQUEST_NOT_BOUND",
                    attempt_id,
                )
            if row[0] not in {"prepared", "not_submitted"}:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_STATE", attempt_id)
            db.execute(
                """
                UPDATE provider_operations
                SET state='submission_unknown',updated_at=?
                WHERE attempt_id=?
                """,
                (_now(), attempt_id),
            )
            db.commit()

    def mark_submitted(
        self,
        attempt_id: str,
        provider_id: str | None,
        *,
        unknown: bool,
    ) -> None:
        state = "submission_unknown" if unknown else "submitted"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,provider_id FROM provider_operations WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if not row:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_UNKNOWN", attempt_id)
            current_state, current_provider = row
            if current_provider and provider_id and current_provider != provider_id:
                db.rollback()
                raise OrchestrationError("PROVIDER_ID_CONFLICT", attempt_id)
            if current_state == "completed":
                db.commit()
                return
            if current_state not in {
                "prepared", "not_submitted", "submission_unknown", "submitted"
            }:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_STATE", attempt_id)
            db.execute(
                """
                UPDATE provider_operations
                SET state=?,provider_id=COALESCE(provider_id,?),updated_at=?
                WHERE attempt_id=?
                """,
                (state, provider_id, _now(), attempt_id),
            )
            db.commit()

    def mark_not_submitted(self, attempt_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM provider_operations WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if not row:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_UNKNOWN", attempt_id)
            if row[0] not in {"prepared", "submission_unknown", "not_submitted"}:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_STATE", attempt_id)
            db.execute(
                """
                UPDATE provider_operations
                SET state='not_submitted',provider_id=NULL,updated_at=?
                WHERE attempt_id=?
                """,
                (_now(), attempt_id),
            )
            db.commit()

    def set_remote_input_file(self, attempt_id: str, file_id: str) -> None:
        if not isinstance(file_id, str) or not file_id.strip():
            raise OrchestrationError("REMOTE_INPUT_FILE_INVALID", attempt_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT remote_input_file_id,state
                FROM provider_operations WHERE attempt_id=?
                """,
                (attempt_id,),
            ).fetchone()
            if not row:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_UNKNOWN", attempt_id)
            current_file, state = row
            if current_file and current_file != file_id:
                db.rollback()
                raise OrchestrationError("REMOTE_INPUT_FILE_CONFLICT", attempt_id)
            if state == "completed" and current_file != file_id:
                db.rollback()
                raise OrchestrationError("PROVIDER_OPERATION_STATE", attempt_id)
            db.execute(
                """
                UPDATE provider_operations
                SET remote_input_file_id=?,updated_at=?
                WHERE attempt_id=?
                """,
                (file_id, _now(), attempt_id),
            )
            db.commit()

    def latest_target_attempts(self, run_id):
        with self.connect() as db:
            return dict(db.execute(
                "SELECT json_extract(work_order_json,'$.target_path'),MAX(attempt_no) "
                "FROM work_orders WHERE run_id=? "
                "AND json_extract(work_order_json,'$.target_path') IS NOT NULL "
                "GROUP BY json_extract(work_order_json,'$.target_path')", (run_id,),
            ))

    def mark_batch_dispatch(self, attempt_ids, *, rejected=False):
        """Jedna fyzická dávka mění všechny pracovní pokusy v jedné transakci."""
        attempt_ids = list(attempt_ids)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for identifier in attempt_ids:
                row = db.execute("SELECT state,physical_request_hash,remote_input_file_id FROM provider_operations WHERE attempt_id=?",
                                 (identifier,)).fetchone()
                allowed = {"prepared", "not_submitted", "submission_unknown"} if rejected else {"prepared", "not_submitted"}
                if not row or row[0] not in allowed or (not rejected and (not row[1] or not row[2])):
                    raise OrchestrationError("PROVIDER_OPERATION_STATE", identifier)
            for identifier in attempt_ids:
                db.execute("UPDATE provider_operations SET state=?,updated_at=? WHERE attempt_id=?",
                           ("not_submitted" if rejected else "submission_unknown", _now(), identifier))
            db.commit()

    def recover_batch_identity(self, orders, *, batch_id: str, input_file_id: str) -> None:
        """Obnoví všechny vazby jedné doložené dávky v jediné transakci."""
        if not batch_id or not input_file_id:
            raise OrchestrationError("BATCH_IDENTITY_MISSING", "Chybí identita dávky nebo vstupu.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for order in orders:
                row = db.execute(
                    "SELECT work_order_hash,endpoint,provider_id,remote_input_file_id FROM provider_operations WHERE attempt_id=?",
                    (order.attempt_id,),
                ).fetchone()
                if not row or row[0] != order.order_hash or row[1] != "/v1/batches":
                    raise OrchestrationError("PROVIDER_OPERATION_CONFLICT", order.attempt_id)
                if row[2] not in {None, batch_id} or row[3] not in {None, input_file_id}:
                    raise OrchestrationError("PROVIDER_ID_CONFLICT", order.attempt_id)
            for order in orders:
                db.execute(
                    "UPDATE provider_operations SET state=CASE WHEN state='completed' THEN state ELSE 'submitted' END,"
                    "provider_id=?,remote_input_file_id=?,updated_at=? WHERE attempt_id=?",
                    (batch_id, input_file_id, _now(), order.attempt_id),
                )
            db.commit()

    def mark_terminal(self, attempt_id: str, provider_id: str) -> None:
        """Uzavře doložený vzdálený pokus, i když poskytovatel nevrátil usage."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,provider_id FROM provider_operations WHERE attempt_id=?", (attempt_id,)).fetchone()
            if not row or row[0] not in {"submitted", "completed"} or row[1] != provider_id or not provider_id:
                raise OrchestrationError("PROVIDER_OPERATION_NOT_CONFIRMED", attempt_id)
            db.execute("UPDATE provider_operations SET state='completed',updated_at=? WHERE attempt_id=?", (_now(), attempt_id))
            db.commit()

    def record_usage(
        self,
        attempt_id: str,
        *,
        provider: str,
        provider_item_id: str,
        usage: dict[str, Any],
        raw_response_ref: str | None = None,
    ) -> bool:
        usage_json = json.dumps(
            usage or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                operation = db.execute(
                    """
                    SELECT state,provider_id FROM provider_operations WHERE attempt_id=?
                    """,
                    (attempt_id,),
                ).fetchone()
                if not operation:
                    raise OrchestrationError("PROVIDER_OPERATION_UNKNOWN", attempt_id)
                state, provider_id = operation
                if state not in {"submitted", "completed"} or not provider_id:
                    raise OrchestrationError(
                        "PROVIDER_OPERATION_NOT_CONFIRMED",
                        attempt_id,
                    )
                current = db.execute(
                    """
                    SELECT usage_json FROM usage_records
                    WHERE provider=? AND provider_item_id=? AND attempt_id=?
                    """,
                    (provider, provider_item_id, attempt_id),
                ).fetchone()
                if current:
                    if current[0] != usage_json:
                        raise OrchestrationError("USAGE_CONFLICT", provider_item_id)
                    db.commit()
                    return False
                db.execute(
                    """
                    INSERT INTO usage_records(
                        provider,provider_item_id,attempt_id,usage_json
                    ) VALUES(?,?,?,?)
                    """,
                    (provider, provider_item_id, attempt_id, usage_json),
                )
                db.execute(
                    """
                    UPDATE provider_operations
                    SET state='completed',
                        provider_id=COALESCE(provider_id,?),
                        raw_response_ref=COALESCE(?,raw_response_ref),
                        updated_at=?
                    WHERE attempt_id=?
                    """,
                    (provider_item_id, raw_response_ref, _now(), attempt_id),
                )
                db.commit()
                return True
            except Exception:
                db.rollback()
                raise

    def commit_transition(
        self,
        task_id: str,
        expected_revision: int,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT status,revision FROM runs WHERE run_id=?",
                    (task_id,),
                ).fetchone()
                if not row:
                    raise OrchestrationError("TASK_UNKNOWN", task_id)
                status, revision = row
                if revision != expected_revision:
                    raise OrchestrationError("REVISION_CONFLICT", task_id)
                event_id = str(event.get("event_id") or canonical_sha256(event))
                duplicate = db.execute(
                    "SELECT 1 FROM task_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if duplicate:
                    db.commit()
                    return {
                        "task_id": task_id,
                        "revision": revision,
                        "status": status,
                    }
                new_status = str(event.get("status") or status)
                sequence = revision + 1
                db.execute(
                    """
                    INSERT INTO task_events(
                        event_id,run_id,sequence,type,payload_ref,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        event_id,
                        task_id,
                        sequence,
                        str(event.get("type") or "transition"),
                        canonical_sha256(event),
                        _now(),
                    ),
                )
                db.execute(
                    "UPDATE runs SET status=?,revision=? WHERE run_id=?",
                    (new_status, sequence, task_id),
                )
                db.commit()
                return {
                    "task_id": task_id,
                    "revision": sequence,
                    "status": new_status,
                }
            except Exception:
                db.rollback()
                raise


def repository_for_logger(logger) -> OrchestrationRepository:
    return OrchestrationRepository(
        Path(logger.paths.run_dir).parent / "orchestration.sqlite3"
    )
