import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kajovo.core.cost_accounting import Rates, calculate, quote, CostLedger, BudgetExceeded, money
from kajovo.core.pricing import PriceRow, PriceTable, compute_cost
from kajovo.core.receipt import ReceiptDB, Receipt
from kajovo.core.price_sources import parse_fx, parse_prices


@pytest.mark.parametrize("value", [True, False, -1, "NaN", "Infinity", "x", None])
def test_money_rejects_invalid(value):
    with pytest.raises(ValueError):
        money(value)


def test_cache_write_replaces_input_and_reasoning_is_subset():
    rates = Rates("m", "10", "50", "1", "12.5")
    usage = {"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 300, "cache_write_tokens": 200},
             "output_tokens_details": {"reasoning_tokens": 80}}
    result = calculate(rates, usage)
    assert result.input == Decimal("0.0078")
    assert result.output == Decimal("0.005")
    assert result.total == Decimal("0.0128")


@pytest.mark.parametrize("usage", [{}, {"input_tokens": -1, "output_tokens": 0}, {"input_tokens": True, "output_tokens": 0},
                                  {"input_tokens": 1, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 2}},
                                  {"input_tokens": 1, "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 1}}])
def test_bad_usage_never_becomes_zero(usage):
    assert calculate(Rates("m", 1, 2), usage).total is None


def test_missing_prices_batch_and_tools_are_unknown():
    assert compute_cost(None, 1, 2)[0] is None
    assert compute_cost(PriceRow("m", 1, 2), 1, 2, is_batch=True)[0] is None
    assert compute_cost(PriceRow("m", 1, 2), 1, 2, file_search_calls=1)[0] is None
    assert calculate(Rates("m", 1, 2), {"input_tokens": 1, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 1}}).total is None


def test_context_threshold_and_conservative_cache_ceiling():
    rates = Rates("m", 10, 50, 1, "12.5", threshold=1000, long_input_multiplier=2, long_output_multiplier="1.5")
    assert calculate(rates, {"input_tokens": 1000, "output_tokens": 100}).total == Decimal(".015")
    assert calculate(rates, {"input_tokens": 1001, "output_tokens": 100}).total == Decimal(".02752")
    q = quote({"model": "m", "max_output_tokens": 100}, 1001, rates)
    assert Decimal(q["maximum_usd"]) == Decimal(".032525")
    assert quote({"model": "m"}, 1000, rates)["maximum_usd"] is None
    assert quote({"model": "m", "max_output_tokens": 100, "tools": [{"type": "file_search"}]}, 1000, rates)["maximum_usd"] is None


def test_scenarios_need_ten_samples_and_payload_binding():
    payload = {"model": "m", "max_output_tokens": 10000, "input": "a"}
    a = quote(payload, 10, Rates("m", 1, 2), range(9))
    assert not a["empirical"]
    assert [r["output_tokens"] for r in a["scenarios"]] == [2000, 8000, 10000]
    b = quote({**payload, "input": "b"}, 10, Rates("m", 1, 2), range(10))
    assert b["empirical"] and a["payload_hash"] != b["payload_hash"]


def test_concurrent_reservations_restart_and_unknown(tmp_path):
    path = str(tmp_path / "db.sqlite")
    ledger = CostLedger(path)
    ledger.set_limit("run", "1")
    def reserve(_):
        try:
            return CostLedger(path).reserve("run", {"maximum_usd": ".6"})
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(reserve, range(2)))
    assert sum(bool(v) for v in ids) == 1
    op = next(v for v in ids if v)
    ledger.mark(op, "unknown")
    with pytest.raises(BudgetExceeded):
        CostLedger(path).reserve("run", {"maximum_usd": ".5"})
    ledger.settle(op, ".1", "resp")
    CostLedger(path).reserve("run", {"maximum_usd": ".9"})


def receipt(i, rates=None):
    return Receipt("run", i, "project", "m", "QA", "RESPONSE", f"r{i}", None, 1000, 100,
                   0, 0, .0012, True, "", {}, {"input_tokens": 1000, "output_tokens": 100},
                   pricing_snapshot=rates.snapshot() if rates else {})


def test_receipt_snapshot_archive_revision_and_full_export(tmp_path):
    db = ReceiptDB(str(tmp_path / "db.sqlite"))
    rates = Rates("m", 1, 2)
    first = db.insert(receipt(1, rates))
    assert db.query()[0]["total_usd"] == "0.0012"
    db.update_row(first, receipt(1, Rates("m", 100, 200)))
    assert db.query()[0]["total_usd"] == "0.0012"
    db.delete_ids([first])
    assert not db.query()
    assert len(db.query(archived=True)) == 1
    assert db.insert(receipt(1)) == first
    for i in range(2, 1004):
        db.insert(receipt(i))
    assert len(db.export_rows(db.query())) == 1002
    assert len(db.query(limit=100, offset=100)) == 100


def test_fx_normalizes_unit():
    fx = parse_fx("09.09.2026 #174\nzemě|měna|množství|kód|kurz\nUSA|dolar|100|USD|2100,50\n")
    assert fx["czk_per_usd"] == "21.005"
    assert fx["date"] == "2026-09-09"


def test_failed_refresh_preserves_last_good(monkeypatch):
    table = PriceTable(":memory:")
    table.rows = {"gpt-4o": PriceRow("gpt-4o", 5, 6)}
    table.verified = True
    monkeypatch.setattr("kajovo.core.pricing.requests.get", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert not table.refresh_from_url("https://example.invalid")[0]
    assert table.rows["gpt-4o"].input_per_1k == 5
    assert table.verified


def test_count_endpoint_uses_actual_supported_parameters(monkeypatch):
    from kajovo.core.openai_client import OpenAIClient
    client = OpenAIClient("test")
    seen = []
    monkeypatch.setattr(client, "validate_access", lambda payload: None)
    monkeypatch.setattr(client, "_req", lambda *a, **k: (seen.append((a, k)), {"input_tokens": 42})[1])
    assert client.count_input_tokens({"model": "m", "input": "x", "previous_response_id": "r", "temperature": .2, "max_output_tokens": 100}) == 42
    assert seen[0][0] == ("POST", "/responses/input_tokens")
    assert seen[0][1]["json_body"] == {"model": "m", "input": "x", "previous_response_id": "r", "text": __import__("kajovo.core.structured_output", fromlist=["text_format"]).text_format()}


def test_batch_settles_without_output_import_and_notifies_once(tmp_path):
    from kajovo.core.batch_costs import finalize_batches, mark_seen
    path = str(tmp_path / "db.sqlite")
    ReceiptDB(path)
    ledger = CostLedger(path)
    item = quote({"model": "m", "max_output_tokens": 100}, 1000, Rates("m", 1, 2, batch=True))
    item["custom_id"] = "c1"
    op = ledger.reserve("run", {"items": [item], "maximum_usd": item["maximum_usd"], "stage": "A3_FILE"})
    ledger.mark(op, "pending", "b1")
    body = {"id": "r1", "model": "m", "usage": {"input_tokens": 1000, "output_tokens": 100}}
    client = SimpleNamespace(retrieve_batch=lambda _: {"id": "b1", "status": "completed", "output_file_id": "file"},
                             file_content=lambda _: json.dumps({"custom_id": "c1", "response": {"body": body}}).encode())
    notifications = finalize_batches(client, path)
    assert notifications == [{"scope": "run", "id": op}]
    assert ledger.operations("run")[0]["actual_usd"] == "0.0012"
    mark_seen(path, [op])
    assert not finalize_batches(client, path)
    assert len(ReceiptDB(path).query()) == 1


def test_official_parser_fails_on_changed_units():
    with pytest.raises(ValueError):
        parse_prices("Prices per 1000 tokens.")


def test_connections_release_windows_database_file(tmp_path):
    path = tmp_path / "db.sqlite"
    ledger = CostLedger(str(path))
    ledger.set_limit("run", "1")
    op = ledger.reserve("run", {"maximum_usd": "1"})
    ledger.settle(op, ".1")
    assert ledger.operations("run")
    path.unlink()
    assert not path.exists()


def test_legacy_migration_keeps_backup_and_marks_missing_provenance(tmp_path):
    import sqlite3
    from kajovo.core.receipt import SCHEMA_SQL
    path = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)
    con.execute("INSERT INTO receipts(run_id,created_at,total_cost) VALUES ('old',1,4.5)")
    con.commit()
    con.close()
    db = ReceiptDB(str(path))
    assert path.with_name(path.name + ".before-cost-v1.bak").is_file()
    row = db.query()[0]
    assert row["total_cost"] == 4.5 and row["total_usd"] is None
