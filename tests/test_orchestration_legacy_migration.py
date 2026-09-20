from __future__ import annotations

import json
import sqlite3

from kajovo.core.orchestration.repository import OrchestrationRepository


def test_legacy_economic_sqlite_is_migrated_to_provider_evidence(tmp_path):
    path = tmp_path / "orchestration.sqlite3"
    db = sqlite3.connect(path)
    try:
        db.executescript(
            """
            CREATE TABLE work_orders(
                work_order_hash TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                body_ref TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                route TEXT NOT NULL
            );
            CREATE TABLE reservations(
                reservation_id TEXT PRIMARY KEY,
                work_order_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                cost_microusd INTEGER,
                input_limit INTEGER,
                output_limit INTEGER,
                provider_id TEXT
            );
            CREATE TABLE provider_operations(
                attempt_id TEXT PRIMARY KEY,
                work_order_hash TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                state TEXT NOT NULL,
                provider_id TEXT,
                remote_input_file_id TEXT,
                raw_response_ref TEXT
            );
            CREATE TABLE usage_records(
                provider TEXT NOT NULL,
                provider_item_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                usage_json TEXT NOT NULL,
                price_snapshot_hash TEXT,
                actual_cost_microusd INTEGER,
                PRIMARY KEY(provider,provider_item_id)
            );
            INSERT INTO work_orders VALUES(
                'wo-1','RUN-1','TASK-1',1,'body','input',
                'schema','prompt','gpt-5.6-luna','responses_live'
            );
            INSERT INTO reservations VALUES(
                'RES-1','wo-1','settled',123,10,20,'resp-1'
            );
            INSERT INTO provider_operations VALUES(
                'OLD-ATTEMPT','wo-1','/v1/responses','request',
                'submitted','resp-1','file-1','response:resp-1'
            );
            INSERT INTO usage_records VALUES(
                'openai','resp-1','OLD-ATTEMPT',
                '{"input_tokens":10,"output_tokens":20}',
                'legacy-price-hash',123
            );
            """
        )
        db.commit()
    finally:
        db.close()

    repo = OrchestrationRepository(path)
    with repo.connect() as check:
        tables = {
            row[0]
            for row in check.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        provider = check.execute(
            """
            SELECT state,provider_id,remote_input_file_id,raw_response_ref
            FROM provider_operations
            """
        ).fetchone()
        usage = check.execute(
            "SELECT provider_item_id,usage_json FROM usage_records"
        ).fetchone()
        provider_columns = {
            row[1]
            for row in check.execute(
                "PRAGMA table_info(provider_operations)"
            )
        }
        usage_columns = {
            row[1]
            for row in check.execute("PRAGMA table_info(usage_records)")
        }

    assert "reservations" not in tables
    assert provider == (
        "completed",
        "resp-1",
        "file-1",
        "response:resp-1",
    )
    assert usage[0] == "resp-1"
    assert json.loads(usage[1]) == {
        "input_tokens": 10,
        "output_tokens": 20,
    }
    assert "cost_microusd" not in provider_columns
    assert "actual_cost_microusd" not in usage_columns
    assert "price_snapshot_hash" not in usage_columns
