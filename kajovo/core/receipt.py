from __future__ import annotations

import os, json, sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from dataclasses import field
from .cost_accounting import Rates, calculate, canonical, money, ClosingConnection

SCHEMA_SQL = '''
CREATE TABLE IF NOT EXISTS receipts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  project TEXT,
  model TEXT,
  mode TEXT,
  flow_type TEXT,
  response_id TEXT,
  batch_id TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  tool_cost REAL,
  storage_cost REAL,
  total_cost REAL,
  pricing_verified INTEGER,
  notes TEXT,
  log_paths_json TEXT,
  usage_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_receipts_created_at ON receipts(created_at);
CREATE INDEX IF NOT EXISTS idx_receipts_project ON receipts(project);
CREATE INDEX IF NOT EXISTS idx_receipts_run_id ON receipts(run_id);
CREATE INDEX IF NOT EXISTS idx_receipts_response_id ON receipts(response_id);
CREATE INDEX IF NOT EXISTS idx_receipts_batch_id ON receipts(batch_id);
CREATE TABLE IF NOT EXISTS receipt_notes(id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL, created_at REAL NOT NULL, note TEXT NOT NULL);
'''

@dataclass
class Receipt:
    run_id: str
    created_at: float
    project: str
    model: str
    mode: str
    flow_type: str
    response_id: Optional[str]
    batch_id: Optional[str]
    input_tokens: int
    output_tokens: int
    tool_cost: float
    storage_cost: float
    total_cost: float
    pricing_verified: bool
    notes: str
    log_paths: Dict[str, Any]
    usage: Dict[str, Any]
    pricing_snapshot: Dict[str, Any] = field(default_factory=dict)
    provider_profile: str = "openai:default"

class ReceiptDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0, factory=ClosingConnection)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _ensure(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        con = self._connect()
        try:
            version = con.execute("PRAGMA user_version").fetchone()[0]
            existed = con.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone()
            if existed and version < 1:
                backup = sqlite3.connect(self.db_path + ".before-cost-v1.bak")
                try:
                    con.backup(backup)
                finally:
                    backup.close()
            con.executescript(SCHEMA_SQL)
            if version < 1:
                con.execute("BEGIN IMMEDIATE")
                for column in ("total_usd TEXT", "pricing_snapshot_json TEXT", "provider_profile TEXT NOT NULL DEFAULT 'openai:default'", "archived INTEGER NOT NULL DEFAULT 0"):
                    con.execute("ALTER TABLE receipts ADD COLUMN " + column)
                con.execute("CREATE TABLE receipt_revisions(id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL, created_at REAL NOT NULL, data_json TEXT NOT NULL)")
                con.execute("PRAGMA user_version=1")
            con.commit()
        finally:
            con.close()

    def insert(self, r: Receipt) -> int:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            if r.response_id:
                existing = con.execute("SELECT id FROM receipts WHERE response_id = ? AND provider_profile=? LIMIT 1", (r.response_id, r.provider_profile)).fetchone()
                if existing:
                    return int(existing[0])
            cur = con.execute(
                '''INSERT INTO receipts
                   (run_id, created_at, project, model, mode, flow_type, response_id, batch_id,
                    input_tokens, output_tokens, tool_cost, storage_cost, total_cost,
                    pricing_verified, notes, log_paths_json, usage_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (r.run_id, r.created_at, r.project, r.model, r.mode, r.flow_type, r.response_id, r.batch_id,
                 int(r.input_tokens), int(r.output_tokens), r.tool_cost, r.storage_cost, r.total_cost,
                 1 if r.pricing_verified else 0, r.notes, json.dumps(r.log_paths, ensure_ascii=False), json.dumps(r.usage, ensure_ascii=False))
            )
            exact = None
            if r.pricing_snapshot:
                cost = calculate(Rates(**r.pricing_snapshot), r.usage)
                if cost.total is not None and r.tool_cost is not None and r.storage_cost is not None:
                    exact = str(cost.total + money(r.tool_cost) + money(r.storage_cost))
            con.execute("UPDATE receipts SET total_usd=?,pricing_snapshot_json=?,provider_profile=? WHERE id=?",
                        (exact, canonical(r.pricing_snapshot), r.provider_profile, cur.lastrowid))
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()

    def query(self, *, project=None, run_id=None, model=None, archived=False, limit=None, offset=0, since=None, until=None, status=None) -> List[sqlite3.Row]:
        con = self._connect()
        try:
            clauses, args = ["archived=?"], [int(archived)]
            for key, value in (("project", project), ("run_id", run_id), ("model", model)):
                if value:
                    clauses.append(key + "=?")
                    args.append(value)
            for key, value, operator in (("created_at", since, ">="), ("created_at", until, "<=")):
                if value is not None:
                    clauses.append(key + operator + "?")
                    args.append(value)
            if status in ("known", "unknown"):
                clauses.append("total_usd IS " + ("NOT NULL" if status == "known" else "NULL"))
            sql = "SELECT * FROM receipts WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC,id DESC"
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                args.extend([int(limit), int(offset)])
            cur = con.execute(sql, args)
            return list(cur.fetchall())
        finally:
            con.close()

    def existing_index(self) -> Dict[str, Any]:
        """Vrátí indexy response_id a batch_id a množinu run_id pro deduplikaci."""
        con = self._connect()
        try:
            cur = con.execute("SELECT id, run_id, response_id, batch_id, total_cost FROM receipts")
            resp_map: Dict[str, Dict[str, Any]] = {}
            batch_map: Dict[str, Dict[str, Any]] = {}
            run_ids: set[str] = set()
            for row_id, run_id, resp_id, batch_id, total_cost in cur.fetchall():
                run_ids.add(str(run_id))
                if resp_id:
                    resp_map[str(resp_id)] = {"id": int(row_id), "run_id": str(run_id), "total_cost": float(total_cost or 0.0)}
                if batch_id:
                    batch_map[str(batch_id)] = {"id": int(row_id), "run_id": str(run_id), "total_cost": float(total_cost or 0.0)}
            return {"response": resp_map, "batch": batch_map, "run_ids": run_ids}
        finally:
            con.close()

    def update_row(self, row_id: int, r: Receipt) -> None:
        """Oprava je nová revize; původní podklady zůstávají zachovány."""
        import time
        from dataclasses import asdict
        with self._connect() as con:
            con.execute("INSERT INTO receipt_revisions(receipt_id,created_at,data_json) VALUES (?,?,?)",
                        (row_id, time.time(), canonical(asdict(r))))
        return

    def delete_ids(self, ids: List[int]) -> None:
        if not ids:
            return
        con = self._connect()
        try:
            q = "UPDATE receipts SET archived=1 WHERE id IN (%s)" % ",".join("?" for _ in ids)
            con.execute(q, ids)
            con.commit()
        finally:
            con.close()

    def export_rows(self, rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for k in ("log_paths_json","usage_json", "pricing_snapshot_json"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else None
                except Exception:
                    pass
            out.append(d)
        return out

    def add_note(self, receipt_id, note):
        import time
        if not note.strip():
            return
        with self._connect() as con:
            con.execute("INSERT INTO receipt_notes(receipt_id,created_at,note) VALUES (?,?,?)", (receipt_id, time.time(), note))
