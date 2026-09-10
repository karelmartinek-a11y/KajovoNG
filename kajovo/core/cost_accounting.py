"""Přesné USD výpočty, neměnné podklady a transakční rezervace nákladů."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import sqlite3
import time
import uuid
import statistics
import math


class ClosingConnection(sqlite3.Connection):
    """Kontext transakci dokončí a současně uvolní soubor databáze."""
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def money(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Cena nesmí být logická hodnota.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Neplatná cena.") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Cena musí být konečná a nezáporná.")
    return result


def tokens(value) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Počet tokenů musí být nezáporné celé číslo.")
    return value


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Rates:
    """Sazby v USD za milion tokenů; None znamená neznámou kategorii."""
    model: str
    input: Decimal
    output: Decimal
    cached: Decimal | None = None
    write: Decimal | None = None
    source: str = "ruční import"
    verified_at: str = ""
    batch: bool = False
    threshold: int | None = None
    long_input_multiplier: Decimal = Decimal(1)
    long_output_multiplier: Decimal = Decimal(1)

    def __post_init__(self):
        for key in ("input", "output", "cached", "write", "long_input_multiplier", "long_output_multiplier"):
            value = getattr(self, key)
            if value is not None:
                object.__setattr__(self, key, money(value))
        if self.threshold is not None:
            tokens(self.threshold)

    def snapshot(self):
        return json.loads(canonical(asdict(self)))


@dataclass(frozen=True)
class Cost:
    total: Decimal | None
    input: Decimal | None
    output: Decimal | None
    reason: str = ""


def calculate(rates: Rates | None, usage: dict) -> Cost:
    if rates is None:
        return Cost(None, None, None, "Chybí sazba modelu nebo režimu.")
    if not isinstance(usage, dict) or "input_tokens" not in usage or "output_tokens" not in usage:
        return Cost(None, None, None, "Chybí skutečná spotřeba.")
    try:
        inp, out = tokens(usage["input_tokens"]), tokens(usage["output_tokens"])
        details = usage.get("input_tokens_details") or {}
        cached, write = tokens(details.get("cached_tokens", 0)), tokens(details.get("cache_write_tokens", 0))
        reasoning = tokens((usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0))
        if cached + write > inp or reasoning > out:
            raise ValueError("Podkategorie tokenů překračují celkovou spotřebu.")
    except (ValueError, TypeError, AttributeError) as exc:
        return Cost(None, None, None, f"Neplatná spotřeba: {exc}")
    if (cached and rates.cached is None) or (write and rates.write is None):
        return Cost(None, None, None, "Chybí sazba cache.")
    long = rates.threshold is not None and inp > rates.threshold
    ic = ((inp - cached - write) * rates.input + cached * (rates.cached or 0)
          + write * (rates.write or 0)) / Decimal(1_000_000)
    oc = out * rates.output / Decimal(1_000_000)
    ic *= rates.long_input_multiplier if long else 1
    oc *= rates.long_output_multiplier if long else 1
    return Cost(ic + oc, ic, oc)


def quote(payload: dict, input_count: int | None, rates: Rates | None, samples=()) -> dict:
    maximum = payload.get("max_output_tokens")
    if maximum is not None:
        tokens(maximum)
        if maximum < 16:
            raise ValueError("max_output_tokens musí být alespoň 16.")
    samples = sorted(tokens(v) for v in samples)
    empirical = len(samples) >= 10
    if empirical:
        deciles = statistics.quantiles(samples, n=10, method="inclusive")
        outputs = [math.ceil(deciles[0]), math.ceil(statistics.median(samples)), math.ceil(deciles[-1])]
    else:
        outputs = [2000, 8000, 32000]
    outputs = [min(v, maximum) if maximum else v for v in outputs]
    scenarios = []
    for output in outputs:
        cost = calculate(rates, {"input_tokens": input_count, "output_tokens": output})
        scenarios.append({"output_tokens": output, "usd": str(cost.total) if cost.total is not None else None})
    ceiling = None
    # Dynamické nástroje mohou přidat další vstupy a vlastní poplatky.
    if rates and input_count is not None and maximum and not payload.get("tools"):
        tokens(input_count)
        upper = max(rates.input, rates.cached or 0, rates.write or 0)
        long = rates.threshold is not None and input_count > rates.threshold
        ceiling = (input_count * upper * (rates.long_input_multiplier if long else 1)
                   + maximum * rates.output * (rates.long_output_multiplier if long else 1)) / Decimal(1_000_000)
    result = {"model": payload.get("model"), "input_tokens": input_count, "max_output_tokens": maximum,
              "reasoning": payload.get("reasoning"),
              "scenarios": scenarios, "sample_count": len(samples), "empirical": empirical,
              "maximum_usd": str(ceiling) if ceiling is not None else None,
              "rates": rates.snapshot() if rates else None, "payload_hash": fingerprint(payload),
              "tools": bool(payload.get("tools")), "created_at": time.time()}
    result["hash"] = fingerprint(result)
    return result


class BudgetExceeded(ValueError):
    pass


class CostLedger:
    def __init__(self, path):
        self.path = path
        with self.connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS cost_scopes(id TEXT PRIMARY KEY, limit_usd TEXT);
                CREATE TABLE IF NOT EXISTS cost_operations(
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, created REAL NOT NULL,
                    estimate_json TEXT NOT NULL, reserved_usd TEXT, actual_usd TEXT,
                    status TEXT NOT NULL, response_id TEXT, batch_id TEXT,
                    usage_json TEXT, source_json TEXT, seen INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS cost_operations_scope ON cost_operations(scope);
            """)

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        con.row_factory = sqlite3.Row
        return con

    def set_limit(self, scope, limit):
        value = str(money(limit)) if limit is not None else None
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO cost_scopes VALUES (?,?) ON CONFLICT(id) DO UPDATE SET limit_usd=excluded.limit_usd", (scope, value))

    def reserve(self, scope, estimate):
        ceiling = estimate.get("maximum_usd")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT OR IGNORE INTO cost_scopes VALUES (?,NULL)", (scope,))
            limit = con.execute("SELECT limit_usd FROM cost_scopes WHERE id=?", (scope,)).fetchone()[0]
            rows = con.execute("SELECT * FROM cost_operations WHERE scope=?", (scope,)).fetchall()
            committed = Decimal(0)
            for row in rows:
                value = row["actual_usd"] if row["status"] == "settled" else row["reserved_usd"]
                if row["status"] == "released":
                    continue
                if value is None and limit is not None:
                    raise BudgetExceeded("Předchozí operace má neznámou cenu; limit nelze bezpečně ověřit.")
                committed += money(value) if value is not None else 0
            if limit is not None:
                if ceiling is None:
                    raise BudgetExceeded("Požadavek nemá doložitelnou maximální cenu. Nastavte limit výstupu a ověřené sazby; dynamické nástroje nelze ohraničit.")
                if committed + money(ceiling) > money(limit):
                    raise BudgetExceeded(f"Rozpočet {limit} USD nestačí: čerpání a rezervace {committed}, další maximum {ceiling} USD.")
            op = uuid.uuid4().hex
            con.execute("INSERT INTO cost_operations(id,scope,created,estimate_json,reserved_usd,status) VALUES (?,?,?,?,?,'reserved')",
                        (op, scope, time.time(), canonical(estimate), ceiling))
            return op

    def settle(self, operation, cost, response_id=None, usage=None, source=None):
        with self.connect() as con:
            con.execute("UPDATE cost_operations SET actual_usd=?,status=?,response_id=?,usage_json=?,source_json=? WHERE id=? AND status!='settled'",
                        (str(money(cost)) if cost is not None else None, "settled" if cost is not None else "unknown",
                         response_id, canonical(usage), canonical(source), operation))

    def mark(self, operation, status, batch_id=None):
        if status not in ("unknown", "released", "pending"):
            raise ValueError("Neplatný stav rezervace.")
        with self.connect() as con:
            con.execute("UPDATE cost_operations SET status=?,batch_id=? WHERE id=? AND status!='settled'", (status, batch_id, operation))

    def operations(self, scope):
        with self.connect() as con:
            return [dict(r) for r in con.execute("SELECT * FROM cost_operations WHERE scope=? ORDER BY created", (scope,))]
