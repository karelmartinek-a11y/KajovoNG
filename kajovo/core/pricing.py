from __future__ import annotations

import os, json, time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import requests


@dataclass
class PriceRow:
    model: str
    input_per_1k: float
    output_per_1k: float
    batch_input_per_1k: Optional[float] = None
    batch_output_per_1k: Optional[float] = None
    file_search_per_1k: Optional[float] = None
    storage_per_gb_day: Optional[float] = None

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "PriceRow":
        def _get(keys: tuple[str, ...], default: float = 0.0) -> float:
            for k in keys:
                if k in raw and raw.get(k) is not None:
                    try:
                        return float(raw[k])
                    except Exception:
                        continue
            return default

        return PriceRow(
            model=str(raw.get("model") or ""),
            input_per_1k=_get(("input_per_1k", "input")),
            output_per_1k=_get(("output_per_1k", "output")),
            batch_input_per_1k=(
                _get(("batch_input_per_1k", "batch_input")) if raw.get("batch_input_per_1k") is not None or raw.get("batch_input") is not None else None
            ),
            batch_output_per_1k=(
                _get(("batch_output_per_1k", "batch_output")) if raw.get("batch_output_per_1k") is not None or raw.get("batch_output") is not None else None
            ),
            file_search_per_1k=(
                _get(("file_search_per_1k", "file_search")) if raw.get("file_search_per_1k") is not None or raw.get("file_search") is not None else None
            ),
            storage_per_gb_day=(
                _get(("storage_per_gb_day", "storage_gb_day")) if raw.get("storage_per_gb_day") is not None or raw.get("storage_gb_day") is not None else None
            ),
        )

class PriceTable:
    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self.rows: Dict[str, PriceRow] = {}
        self.last_updated: Optional[float] = None
        self.verified: bool = False
        self.last_fetch_source: str = ""

    def load_cache(self) -> None:
        if not os.path.exists(self.cache_path):
            return
        with open(self.cache_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.last_updated = raw.get("last_updated")
        self.verified = bool(raw.get("verified"))
        self.last_fetch_source = raw.get("last_fetch_source", "") or ""
        self.rows = {}
        for r in raw.get("rows", []):
            try:
                pr = PriceRow.from_dict(r)
                if pr.model:
                    self.rows[pr.model] = pr
            except Exception:
                continue

    def save_cache(self) -> None:
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump({
                "last_updated": self.last_updated,
                "verified": self.verified,
                "last_fetch_source": self.last_fetch_source,
                "rows": [vars(r) for r in self.rows.values()],
            }, f, ensure_ascii=False, indent=2)

    def refresh_from_url(self, url: str, timeout_s: float = 20.0) -> Tuple[bool, str]:
        if not url or not str(url).strip():
            self.verified = False
            return False, "pricing URL is empty"
        try:
            r = requests.get(url, timeout=timeout_s)
            r.raise_for_status()
            data = r.json()
            rows = {}
            for row in data.get("rows", []):
                try:
                    pr = PriceRow.from_dict(row)
                    if pr.model:
                        rows[pr.model] = pr
                except Exception:
                    continue
            if not rows:
                return False, "price_table: empty rows"
            self.update_from_rows(rows, verified=True, source=f"URL {url}")
            return True, "OK"
        except Exception as e:
            self.verified = False
            # fallback to builtin pricing so app remains usable
            fallback_rows = PriceTable.builtin_fallback().rows
            if fallback_rows:
                try:
                    self.update_from_rows(fallback_rows, verified=False, source="builtin fallback")
                except Exception:
                    pass
            reason = str(e).splitlines()[0] if str(e) else "neznámá chyba"
            return False, f"URL ceníku nedostupná (fallback): {reason}"

    def get(self, model: str) -> Optional[PriceRow]:
        return self.rows.get(model)

    @staticmethod
    def builtin_fallback() -> 'PriceTable':
        pt = PriceTable(cache_path=":memory:")
        pt.verified = False
        pt.rows = {
            "gpt-4o-mini": PriceRow("gpt-4o-mini", 0.15, 0.60),
            "gpt-4o": PriceRow("gpt-4o", 5.00, 15.00),
        }
        return pt

    def _rows_equal(self, a: PriceRow, b: PriceRow) -> bool:
        return (
            a.model == b.model
            and float(a.input_per_1k) == float(b.input_per_1k)
            and float(a.output_per_1k) == float(b.output_per_1k)
            and (a.batch_input_per_1k or 0.0) == (b.batch_input_per_1k or 0.0)
            and (a.batch_output_per_1k or 0.0) == (b.batch_output_per_1k or 0.0)
            and (a.file_search_per_1k or 0.0) == (b.file_search_per_1k or 0.0)
            and (a.storage_per_gb_day or 0.0) == (b.storage_per_gb_day or 0.0)
        )

    def _merge_with_fallback(self, rows: Dict[str, PriceRow]) -> Dict[str, PriceRow]:
        merged = dict(self.rows)  # keep existing prices so they stay available
        merged.update(rows or {})
        # ensure baseline GPT models are always present
        for model, pr in PriceTable.builtin_fallback().rows.items():
            merged.setdefault(model, pr)
        return merged

    def update_from_rows(self, rows: Dict[str, PriceRow], verified: bool, source: str = "GPT-4.1") -> None:
        if not rows:
            return
        merged = self._merge_with_fallback(rows)
        changed = False
        if set(self.rows.keys()) != set(merged.keys()):
            changed = True
        else:
            for mid, new_row in merged.items():
                old_row = self.rows.get(mid)
                if old_row is None or not self._rows_equal(old_row, new_row):
                    changed = True
                    break

        if changed:
            self.last_updated = time.time()
            self.rows = merged
        else:
            # keep existing rows and timestamp if nothing changed
            self.rows = self._merge_with_fallback(self.rows)

        self.verified = verified
        self.last_fetch_source = source
        if self.cache_path and self.cache_path != ":memory:":
            self.save_cache()

def compute_cost(
    row: Optional[PriceRow],
    input_tokens: int,
    output_tokens: int,
    is_batch: bool = False,
    use_file_search: bool = False,
    storage_gb_days: float = 0.0,
) -> tuple[float, float, float]:
    """Return total, tool_cost, storage_cost."""
    if row is None:
        return 0.0, 0.0, 0.0
    inp = row.batch_input_per_1k if is_batch and row.batch_input_per_1k is not None else row.input_per_1k
    outp = row.batch_output_per_1k if is_batch and row.batch_output_per_1k is not None else row.output_per_1k
    base = (input_tokens / 1000.0) * inp + (output_tokens / 1000.0) * outp
    tool_cost = 0.0
    if use_file_search and row.file_search_per_1k is not None:
        tool_cost += (input_tokens / 1000.0) * row.file_search_per_1k
    storage_cost = 0.0
    if storage_gb_days > 0 and row.storage_per_gb_day is not None:
        storage_cost = float(storage_gb_days) * row.storage_per_gb_day
    total = base + tool_cost + storage_cost
    return total, tool_cost, storage_cost
