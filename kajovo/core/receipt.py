from __future__ import annotations

import os, json, sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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

class ReceiptDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _ensure(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        con = self._connect()
        try:
            con.executescript(SCHEMA_SQL)
            con.commit()
        finally:
            con.close()

    def insert(self, r: Receipt) -> int:
        con = self._connect()
        try:
            cur = con.execute(
                '''INSERT INTO receipts
                   (run_id, created_at, project, model, mode, flow_type, response_id, batch_id,
                    input_tokens, output_tokens, tool_cost, storage_cost, total_cost,
                    pricing_verified, notes, log_paths_json, usage_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (r.run_id, r.created_at, r.project, r.model, r.mode, r.flow_type, r.response_id, r.batch_id,
                 int(r.input_tokens), int(r.output_tokens), float(r.tool_cost), float(r.storage_cost), float(r.total_cost),
                 1 if r.pricing_verified else 0, r.notes, json.dumps(r.log_paths, ensure_ascii=False), json.dumps(r.usage, ensure_ascii=False))
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            con.close()

    def query(self) -> List[sqlite3.Row]:
        con = self._connect()
        try:
            cur = con.execute("SELECT * FROM receipts ORDER BY created_at DESC LIMIT 1000")
            return list(cur.fetchall())
        finally:
            con.close()

    def existing_index(self) -> Dict[str, Any]:
        """Return maps for fast de-duplication: response_id -> row, batch_id -> row, and run_ids set."""
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
        con = self._connect()
        try:
            con.execute(
                """UPDATE receipts
                   SET run_id=?, created_at=?, project=?, model=?, mode=?, flow_type=?, response_id=?, batch_id=?,
                       input_tokens=?, output_tokens=?, tool_cost=?, storage_cost=?, total_cost=?,
                       pricing_verified=?, notes=?, log_paths_json=?, usage_json=?
                   WHERE id=?""",
                (
                    r.run_id,
                    r.created_at,
                    r.project,
                    r.model,
                    r.mode,
                    r.flow_type,
                    r.response_id,
                    r.batch_id,
                    int(r.input_tokens),
                    int(r.output_tokens),
                    float(r.tool_cost),
                    float(r.storage_cost),
                    float(r.total_cost),
                    1 if r.pricing_verified else 0,
                    r.notes,
                    json.dumps(r.log_paths, ensure_ascii=False),
                    json.dumps(r.usage, ensure_ascii=False),
                    int(row_id),
                ),
            )
            con.commit()
        finally:
            con.close()

    def delete_ids(self, ids: List[int]) -> None:
        if not ids:
            return
        con = self._connect()
        try:
            q = "DELETE FROM receipts WHERE id IN (%s)" % ",".join("?" for _ in ids)
            con.execute(q, ids)
            con.commit()
        finally:
            con.close()

    def export_rows(self, rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for k in ("log_paths_json","usage_json"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else None
                except Exception:
                    pass
            out.append(d)
        return out
