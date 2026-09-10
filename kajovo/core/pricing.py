from __future__ import annotations

import os, json, time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import requests
import math
import re
from .cost_accounting import Rates, calculate, money, tokens
from .utils import atomic_write_text


@dataclass
class PriceRow:
    model: str
    input_per_1k: float
    output_per_1k: float
    batch_input_per_1k: Optional[float] = None
    batch_output_per_1k: Optional[float] = None
    file_search_per_1k: Optional[float] = None
    storage_per_gb_day: Optional[float] = None
    cached_input_per_1k: Optional[float] = None
    cache_write_per_1k: Optional[float] = None
    batch_cached_input_per_1k: Optional[float] = None
    batch_cache_write_per_1k: Optional[float] = None
    source: str = ""
    verified_at: str = ""
    context_threshold: Optional[int] = None
    long_input_multiplier: float = 1
    long_output_multiplier: float = 1
    output_token_limit: Optional[int] = None

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Model ceníku nesmí být prázdný.")
        for key, value in vars(self).items():
            if key not in ("model", "source", "verified_at") and value is not None:
                if key in ("context_threshold", "output_token_limit"):
                    tokens(value)
                else:
                    setattr(self, key, money(value))

    def rates(self, batch=False):
        inp = self.batch_input_per_1k if batch else self.input_per_1k
        out = self.batch_output_per_1k if batch else self.output_per_1k
        if inp is None or out is None:
            return None
        cached = self.batch_cached_input_per_1k if batch else self.cached_input_per_1k
        write = self.batch_cache_write_per_1k if batch else self.cache_write_per_1k
        return Rates(self.model, money(inp) * 1000, money(out) * 1000,
                     money(cached) * 1000 if cached is not None else None,
                     money(write) * 1000 if write is not None else None,
                     self.source or "neověřený ceník", self.verified_at, batch, self.context_threshold,
                     money(self.long_input_multiplier), money(self.long_output_multiplier))

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "PriceRow":
        if not isinstance(raw, dict) or not any(raw.get(key) is not None for key in ("input_per_1k", "input")) or not any(raw.get(key) is not None for key in ("output_per_1k", "output")):
            raise ValueError("Cenový řádek vyžaduje vstupní a výstupní sazbu.")
        def _get(keys: tuple[str, ...], default: float = 0.0) -> float:
            for k in keys:
                if k in raw and raw.get(k) is not None:
                    try:
                        return money(raw[k])
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"Neplatná sazba {k}.") from exc
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
            cached_input_per_1k=raw.get("cached_input_per_1k"),
            cache_write_per_1k=raw.get("cache_write_per_1k"),
            batch_cached_input_per_1k=raw.get("batch_cached_input_per_1k"),
            batch_cache_write_per_1k=raw.get("batch_cache_write_per_1k"),
            source=str(raw.get("source") or ""), verified_at=str(raw.get("verified_at") or ""),
            context_threshold=raw.get("context_threshold"),
            long_input_multiplier=raw.get("long_input_multiplier", 1),
            long_output_multiplier=raw.get("long_output_multiplier", 1),
            output_token_limit=raw.get("output_token_limit"),
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
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict) or raw.get("schema_version") != 2:
                return
        except (OSError, ValueError):
            return
        timestamp = raw.get("last_updated")
        self.last_updated = timestamp if isinstance(timestamp, (int, float)) and math.isfinite(timestamp) else None
        self.verified = bool(raw.get("verified"))
        self.last_fetch_source = raw.get("last_fetch_source", "") or ""
        self.rows = {}
        if not isinstance(raw.get("rows"), list):
            self.verified = False
            return
        for r in raw.get("rows", []):
            try:
                pr = PriceRow.from_dict(r)
                if pr.model:
                    self.rows[pr.model] = pr
            except Exception:
                continue

    def save_cache(self) -> None:
        if not self.cache_path or self.cache_path == ":memory:":
            return
        os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
        atomic_write_text(self.cache_path, json.dumps({
                "schema_version": 2,
                "last_updated": self.last_updated,
                "verified": self.verified,
                "last_fetch_source": self.last_fetch_source,
                "rows": [vars(r) for r in self.rows.values()],
            }, ensure_ascii=False, indent=2, default=str))

    def refresh_from_url(self, url: str, timeout_s: float = 20.0) -> Tuple[bool, str]:
        if not url or not str(url).strip():
            self.verified = False
            return False, "pricing URL is empty"
        try:
            from .price_sources import OPENAI_PRICING, parse_prices
            if url.rstrip("/") in ("https://openai.com/api/pricing", "https://developers.openai.com/api/docs/pricing", OPENAI_PRICING):
                response = requests.get(OPENAI_PRICING, timeout=timeout_s)
                response.raise_for_status()
                from concurrent.futures import ThreadPoolExecutor
                import re
                models = sorted(set(re.findall(r"^\|\s*((?:gpt-|o[134](?:-|\b))[A-Za-z0-9_.-]*)\s*(?:\([^|]*\))?\s*\|", response.text, re.M)))
                def fetch_model(model):
                    try:
                        r = requests.get("https://developers.openai.com/api/docs/models/" + model + ".md", timeout=timeout_s)
                        r.raise_for_status()
                        return model, r.text
                    except requests.RequestException:
                        return model, ""
                with ThreadPoolExecutor(max_workers=4) as pool:
                    docs = dict(pool.map(fetch_model, models))
                rows = parse_prices(response.text, docs)
                self.update_from_rows(rows, verified=True, source=OPENAI_PRICING)
                return True, "Oficiální sazby obnoveny; nedoložené kombinace zůstávají neznámé."
            r = requests.get(url, timeout=timeout_s)
            r.raise_for_status()
            ctype = (r.headers.get("content-type") or "").lower()
            rows: Dict[str, PriceRow] = {}

            if "json" in ctype:
                data = r.json()
                for row in data.get("rows", []):
                    try:
                        pr = PriceRow.from_dict(row)
                        pr.source, pr.verified_at = f"ruční JSON {url}", ""
                        if pr.model:
                            rows[pr.model] = pr
                    except Exception:
                        continue
                if rows:
                    self.update_from_rows(rows, verified=False, source=f"ruční JSON {url}")
                    return True, "OK"

            parsed_rows = self._parse_official_pricing_html(r.text)
            if parsed_rows:
                self.update_from_rows(parsed_rows, verified=False, source=f"official pricing page (unverified parse) {url}")
                return False, "Použit neověřený/odhadnutý parsing oficiální pricing stránky."

            return self._fallback_with_reason("Nepodařilo se parsovat pricing data z URL.")
        except Exception as e:
            reason = str(e).splitlines()[0] if str(e) else "neznámá chyba"
            return self._fallback_with_reason(f"URL ceníku nedostupná: {reason}")

    def _parse_official_pricing_html(self, html: str) -> Dict[str, PriceRow]:
        # HTML neobsahuje stabilní strojový kontrakt jednotek a kategorií cen.
        return {}

    def _fallback_with_reason(self, reason: str) -> Tuple[bool, str]:
        if not self.rows:
            self.rows = PriceTable.builtin_fallback().rows
            self.verified = False
        return False, f"{reason} Zachován poslední dostupný ceník."

    def get(self, model: str) -> Optional[PriceRow]:
        exact = self.rows.get(model)
        if exact is not None:
            return exact
        alias = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", model)
        return self.rows.get(alias) if alias != model else None

    def is_verified(self, model: str) -> bool:
        row = self.rows.get(model)
        return bool(row and ((row.source.startswith("https://developers.openai.com/") and row.verified_at) or self.verified))

    @staticmethod
    def builtin_fallback() -> 'PriceTable':
        pt = PriceTable(cache_path=":memory:")
        pt.verified = False
        pt.rows = {
            "gpt-4o-mini": PriceRow("gpt-4o-mini", 0.00015, 0.00060, 0.000075, 0.00030),
            "gpt-4o": PriceRow("gpt-4o", 0.00250, 0.01000, 0.00125, 0.00500),
        }
        return pt

    def _rows_equal(self, a: PriceRow, b: PriceRow) -> bool:
        return (
            a.model == b.model
            and float(a.input_per_1k) == float(b.input_per_1k)
            and float(a.output_per_1k) == float(b.output_per_1k)
            and a.batch_input_per_1k == b.batch_input_per_1k
            and a.batch_output_per_1k == b.batch_output_per_1k
            and a.file_search_per_1k == b.file_search_per_1k
            and a.storage_per_gb_day == b.storage_per_gb_day
            and vars(a) == vars(b)
        )

    def _merge_with_fallback(self, rows: Dict[str, PriceRow]) -> Dict[str, PriceRow]:
        merged = dict(self.rows)  # Zachování dosavadních sazeb.
        merged.update(rows or {})
        # Doplnění chybějících modelů z vestavěného ceníku.
        for model, pr in PriceTable.builtin_fallback().rows.items():
            merged.setdefault(model, pr)
        return merged

    def update_from_rows(self, rows: Dict[str, PriceRow], verified: bool, source: str = "ruční import") -> None:
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
            # Při stejných sazbách se zachovají řádky; čas obnovení se aktualizuje níže.
            self.rows = self._merge_with_fallback(self.rows)

        self.verified = bool(verified and set(merged) == set(rows))
        self.last_updated = time.time()
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
    file_search_calls: int = 0,
    usage: Optional[dict] = None,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Vrátí celkové náklady, náklady nástrojů a úložiště v USD."""
    tokens(input_tokens)
    tokens(output_tokens)
    tokens(file_search_calls)
    days = money(storage_gb_days)
    cost = calculate(row.rates(is_batch) if row else None,
                     usage if usage is not None else {"input_tokens": input_tokens, "output_tokens": output_tokens})
    tool = (money(row.file_search_per_1k) * file_search_calls / 1000
            if row and row.file_search_per_1k is not None else None) if file_search_calls else money(0)
    storage = (money(row.storage_per_gb_day) * days
               if row and row.storage_per_gb_day is not None else None) if days else money(0)
    total = cost.total + tool + storage if all(v is not None for v in (cost.total, tool, storage)) else None
    return tuple(float(v) if v is not None else None for v in (total, tool, storage))
