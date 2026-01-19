from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import RetryPolicy
from .pricing import PriceTable, compute_cost
from .pricing_fetcher import PricingFetcher
from .receipt import Receipt, ReceiptDB
from .openai_client import OpenAIClient
from .retry import CircuitBreaker, with_retry


@dataclass
class AuditSummary:
    runs_scanned: int = 0
    responses_seen: int = 0
    inserted: int = 0
    updated: int = 0
    zero_usage: int = 0
    missing_runs: int = 0
    pricing_refresh: str = ""
    errors: List[str] = None  # type: ignore

    def as_dict(self) -> Dict[str, Any]:
        return {
            "runs_scanned": self.runs_scanned,
            "responses_seen": self.responses_seen,
            "inserted": self.inserted,
            "updated": self.updated,
            "zero_usage": self.zero_usage,
            "missing_runs": self.missing_runs,
            "pricing_refresh": self.pricing_refresh,
            "errors": self.errors or [],
        }


class PricingAuditor:
    """Deterministically scan LOG/* runs and ensure receipts/pricing coverage."""

    def __init__(self, settings, price_table: PriceTable, receipt_db: ReceiptDB, api_key: str = "", log_fn=None):
        self.s = settings
        self.pt = price_table
        self.db = receipt_db
        self.api_key = api_key or ""
        self.log = log_fn or (lambda *_args, **_kwargs: None)
        self.retry = getattr(settings, "retry", None) or RetryPolicy()
        self.breaker = CircuitBreaker(
            getattr(settings.retry, "circuit_breaker_failures", 3),
            getattr(settings.retry, "circuit_breaker_cooldown_s", 10.0),
        )

    def audit(self) -> Dict[str, Any]:
        summary = AuditSummary(errors=[])
        self._refresh_pricing_if_needed(summary)

        idx = self.db.existing_index()
        log_dir = self._abs_log_dir()
        if not os.path.isdir(log_dir):
            summary.errors.append(f"Log dir not found: {log_dir}")
            return summary.as_dict()

        for run_dir in self._iter_run_dirs(log_dir):
            summary.runs_scanned += 1
            run_state = self._load_run_state(run_dir)
            req_meta = self._load_request_meta(run_dir)
            res = self._audit_run(run_dir, run_state, req_meta, idx)
            summary.responses_seen += res["responses"]
            summary.inserted += res["inserted"]
            summary.updated += res["updated"]
            summary.zero_usage += res["zero_usage"]
            summary.missing_runs += res["missing"]
            if res["error"]:
                summary.errors.append(res["error"])
        return summary.as_dict()

    def _abs_log_dir(self) -> str:
        base = getattr(self.s, "log_dir", "LOG") or "LOG"
        if os.path.isabs(base):
            return base
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        return os.path.join(root_dir, base)

    def _refresh_pricing_if_needed(self, summary: AuditSummary) -> None:
        ttl_hours = getattr(getattr(self.s, "pricing", None), "cache_ttl_hours", 72)
        now = time.time()
        stale = (
            not self.pt.rows
            or not self.pt.last_updated
            or (ttl_hours and (now - float(self.pt.last_updated or 0)) > ttl_hours * 3600)
        )
        if not stale:
            return
        ok, msg = self.pt.refresh_from_url(getattr(self.s.pricing, "source_url", ""))
        if ok:
            summary.pricing_refresh = "url"
            self.log("Pricing refreshed from URL.")
            return
        if not self.api_key:
            summary.errors.append(f"Pricing refresh failed (no API key): {msg}")
            return
        try:
            client = OpenAIClient(self.api_key)
            resp = with_retry(lambda: client.create_response(PricingFetcher.payload()), self.retry, self.breaker)
            rows = PricingFetcher.parse_response(resp)
            if rows:
                self.pt.update_from_rows(rows, verified=False, source="GPT fallback")
                summary.pricing_refresh = "model"
                self.log(f"Pricing refreshed via model ({len(rows)} rows).")
            else:
                summary.errors.append("Pricing refresh via model returned empty rows.")
        except Exception as exc:
            summary.errors.append(f"Pricing refresh via model failed: {exc}")

    def _iter_run_dirs(self, log_dir: str) -> List[str]:
        runs = []
        for name in os.listdir(log_dir):
            path = os.path.join(log_dir, name)
            if os.path.isdir(path) and (name.startswith("RUN_") or name.startswith("TEST_")):
                runs.append(path)
        runs.sort()
        return runs

    def _load_run_state(self, run_dir: str) -> Dict[str, Any]:
        state_path = os.path.join(run_dir, "run_state.json")
        if os.path.exists(state_path):
            try:
                with open(state_path, "r", encoding="utf-8", errors="ignore") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _load_request_meta(self, run_dir: str) -> List[Tuple[str, bool, float]]:
        req_dir = os.path.join(run_dir, "requests")
        meta: List[Tuple[str, bool, float]] = []
        if not os.path.isdir(req_dir):
            return meta
        for fn in os.listdir(req_dir):
            if not fn.lower().endswith(".json") and not fn.lower().endswith(".jsonl"):
                continue
            path = os.path.join(req_dir, fn)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                payload = data.get("payload") or data.get("body") or data
                tools = payload.get("tools") or []
                use_fs = any(isinstance(t, dict) and t.get("type") == "file_search" for t in tools)
                label = self._infer_label(fn)
                meta.append((label, bool(use_fs), os.path.getmtime(path)))
            except Exception:
                continue
        meta.sort(key=lambda x: x[2])
        return meta

    def _audit_run(
        self,
        run_dir: str,
        run_state: Dict[str, Any],
        req_meta: List[Tuple[str, bool, float]],
        idx: Dict[str, Any],
    ) -> Dict[str, Any]:
        resp_dir = os.path.join(run_dir, "responses")
        results = {"responses": 0, "inserted": 0, "updated": 0, "zero_usage": 0, "missing": 0, "error": ""}
        if not os.path.isdir(resp_dir):
            # no responses at all
            results["missing"] += self._maybe_insert_fallback(run_dir, run_state, idx)
            return results

        resp_files = [os.path.join(resp_dir, f) for f in os.listdir(resp_dir) if f.lower().endswith(".json")]
        resp_files.sort(key=lambda p: os.path.getmtime(p))
        if not resp_files:
            results["missing"] += self._maybe_insert_fallback(run_dir, run_state, idx)
            return results

        for resp_path in resp_files:
            try:
                with open(resp_path, "r", encoding="utf-8", errors="ignore") as f:
                    resp = json.load(f)
            except Exception as exc:
                results["error"] = f"{run_dir}: failed to read {os.path.basename(resp_path)}: {exc}"
                continue

            info = self._build_receipt(run_dir, run_state, resp_path, resp, req_meta)
            if info is None:
                continue
            receipt, response_id, batch_id, zero_usage = info
            results["responses"] += 1
            if zero_usage:
                results["zero_usage"] += 1
            op = self._insert_or_update(receipt, response_id, batch_id, idx)
            if op == "inserted":
                results["inserted"] += 1
            elif op == "updated":
                results["updated"] += 1

        return results

    def _maybe_insert_fallback(self, run_dir: str, run_state: Dict[str, Any], idx: Dict[str, Any]) -> int:
        run_id = os.path.basename(run_dir)
        if run_id in idx.get("run_ids", set()):
            return 0
        project = run_state.get("project") or "UNKNOWN"
        status = run_state.get("status") or "unknown"
        receipt = Receipt(
            run_id=run_id,
            created_at=time.time(),
            project=project,
            model=run_state.get("model") or "",
            mode=run_state.get("mode") or "UNKNOWN",
            flow_type="FALLBACK",
            response_id=None,
            batch_id=None,
            input_tokens=0,
            output_tokens=0,
            tool_cost=0.0,
            storage_cost=0.0,
            total_cost=0.0,
            pricing_verified=False,
            notes=f"Audit fallback (no responses; status={status})",
            log_paths={"run_dir": run_dir},
            usage={"status": status},
        )
        row_id = self.db.insert(receipt)
        idx.get("run_ids", set()).add(run_id)
        idx.get("response", {})[f"fallback-{row_id}"] = {"id": row_id, "run_id": run_id, "total_cost": 0.0}
        return 1

    def _insert_or_update(
        self, receipt: Receipt, response_id: Optional[str], batch_id: Optional[str], idx: Dict[str, Any]
    ) -> str:
        if response_id:
            existing = idx.get("response", {}).get(response_id)
            if existing:
                if self._needs_update(existing.get("total_cost", 0.0), receipt.total_cost):
                    self.db.update_row(existing["id"], receipt)
                    existing["total_cost"] = receipt.total_cost
                    return "updated"
                return "skipped"
        if batch_id:
            existing = idx.get("batch", {}).get(batch_id)
            if existing:
                if self._needs_update(existing.get("total_cost", 0.0), receipt.total_cost):
                    self.db.update_row(existing["id"], receipt)
                    existing["total_cost"] = receipt.total_cost
                    return "updated"
                return "skipped"
        row_id = self.db.insert(receipt)
        if response_id:
            idx.get("response", {})[response_id] = {"id": row_id, "run_id": receipt.run_id, "total_cost": receipt.total_cost}
        if batch_id:
            idx.get("batch", {})[batch_id] = {"id": row_id, "run_id": receipt.run_id, "total_cost": receipt.total_cost}
        idx.get("run_ids", set()).add(receipt.run_id)
        return "inserted"

    @staticmethod
    def _needs_update(existing_total: float, new_total: float) -> bool:
        # Avoid zeros unless they are genuinely zero; update if delta is meaningful.
        if existing_total == 0.0 and new_total != 0.0:
            return True
        return abs(float(existing_total) - float(new_total)) > 1e-6

    def _build_receipt(
        self,
        run_dir: str,
        run_state: Dict[str, Any],
        resp_path: str,
        resp: Dict[str, Any],
        req_meta: List[Tuple[str, bool, float]],
    ) -> Optional[Tuple[Receipt, Optional[str], Optional[str], bool]]:
        run_id = os.path.basename(run_dir)
        fname = os.path.basename(resp_path)
        label = self._infer_label(fname)
        mode, flow = self._infer_mode_flow(label)
        response_id = self._extract(resp, ("id",))
        if not response_id:
            response_id = self._extract(resp.get("response", {}) if isinstance(resp, dict) else {}, ("id",))
        batch_id = resp.get("batch_id") or None
        model = (
            resp.get("model")
            or self._extract(resp, ("response.model",))
            or run_state.get("model")
            or ""
        )
        usage, inp, outp = self._extract_usage(resp)
        zero_usage = inp == 0 and outp == 0
        use_fs = self._match_request_tools(label, os.path.getmtime(resp_path), req_meta)
        row = self.pt.get(model) or PriceTable.builtin_fallback().get(model) or PriceTable.builtin_fallback().get("gpt-4o-mini")
        total, tool_cost, storage_cost = compute_cost(row, inp, outp, is_batch=mode == "C", use_file_search=use_fs)
        notes = f"{flow or 'UNKNOWN'}"
        if zero_usage and usage:
            notes += " (usage present but zero tokens)"
        elif zero_usage and not usage:
            notes += " (usage missing)"
        receipt = Receipt(
            run_id=run_id,
            created_at=os.path.getmtime(resp_path),
            project=run_state.get("project") or "UNKNOWN",
            model=model,
            mode=mode or run_state.get("mode") or "UNKNOWN",
            flow_type=flow or "UNKNOWN",
            response_id=response_id,
            batch_id=batch_id,
            input_tokens=inp,
            output_tokens=outp,
            tool_cost=float(tool_cost),
            storage_cost=float(storage_cost),
            total_cost=float(total),
            pricing_verified=bool(self.pt.verified and row is not None),
            notes=notes,
            log_paths={"run_dir": run_dir, "response_file": resp_path},
            usage=usage if isinstance(usage, dict) else {},
        )
        return receipt, response_id, batch_id, zero_usage

    @staticmethod
    def _extract_usage(resp: Dict[str, Any]) -> Tuple[Dict[str, Any], int, int]:
        usage = {}
        if isinstance(resp, dict):
            usage = resp.get("usage") or {}
            if not usage and isinstance(resp.get("response"), dict):
                usage = resp["response"].get("usage") or {}
            if not usage and isinstance(resp.get("body"), dict):
                usage = resp["body"].get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        outp = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        return usage, inp, outp

    @staticmethod
    def _extract(data: Dict[str, Any], dotted_keys: Tuple[str, ...]) -> Optional[str]:
        for key in dotted_keys:
            parts = key.split(".")
            cur = data
            try:
                for part in parts:
                    if isinstance(cur, dict):
                        cur = cur.get(part, {})
                    else:
                        cur = {}
                if isinstance(cur, str) and cur:
                    return cur
            except Exception:
                continue
        return None

    @staticmethod
    def _infer_label(name: str) -> str:
        upper = name.upper()
        for token in ("A3", "A2", "A1", "B3", "B2", "B1", "QA", "QFILE", "C_BATCH", "C"):
            if token in upper:
                return token
        if "BATCH" in upper:
            return "C"
        return "UNKNOWN"

    @staticmethod
    def _infer_mode_flow(label: str) -> Tuple[str, str]:
        mapping = {
            "A1": ("GENERATE", "A1"),
            "A2": ("GENERATE", "A2"),
            "A3": ("GENERATE", "A3"),
            "B1": ("MODIFY", "B1"),
            "B2": ("MODIFY", "B2"),
            "B3": ("MODIFY", "B3"),
            "QA": ("QA", "QA"),
            "QFILE": ("QFILE", "QFILE"),
            "C_BATCH": ("C", "C_BATCH"),
            "C": ("C", "C"),
        }
        return mapping.get(label, ("UNKNOWN", label or "UNKNOWN"))

    @staticmethod
    def _match_request_tools(label: str, resp_mtime: float, req_meta: List[Tuple[str, bool, float]]) -> bool:
        # Pick the latest request with the same label that occurred before the response.
        candidates = [m for m in req_meta if m[0] == label and m[2] <= resp_mtime + 1]
        if not candidates:
            return False
        return bool(candidates[-1][1])
