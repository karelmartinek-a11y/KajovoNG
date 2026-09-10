"""Regrese od čistého startu až po vyčíslenou účtenku, bez placeného API."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication

from kajovo.core.pricing import PriceTable, PriceRow, price_response
from kajovo.core.cost_accounting import calculate, CostLedger, Rates, quote
from kajovo.core.receipt import Receipt, ReceiptDB
from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.pricing_audit import PricingAuditor
from kajovo.ui.cost_dialog import CostController
from kajovo.ui.pricing_panel import PricingPanel
from kajovo.ui.receipt_view import amount, estimate_summary


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def old_receipt(model="gpt-5.2", response="resp_old", usage=None):
    return Receipt("run", 1, "Projekt", model, "QA", "RESPONSE", response, None,
        1000, 100, 0, 0, None, False, "Původní podklady", {},
        usage if usage is not None else {"input_tokens": 1000, "output_tokens": 100})


def test_first_start_has_current_model_and_cache_prices_without_network(tmp_path):
    prices = PriceTable(str(tmp_path / "absent.json"))
    prices.bootstrap()
    row = prices.get("gpt-5.2-2025-12-11")
    assert row and prices.is_verified("gpt-5.2-2025-12-11")
    cost = calculate(row.rates(), {"input_tokens": 1000, "output_tokens": 100,
        "input_tokens_details": {"cached_tokens": 400}})
    assert cost.total == Decimal("0.00252")
    assert not prices.is_stale()
    prices.last_updated -= 73 * 3600
    assert prices.is_stale()
    before = prices.rows.copy()
    assert not prices.refresh_from_url("https://developers.openai.com/api/docs/pricing.md")[0]
    assert prices.rows == before


def test_bootstrap_repairs_empty_legacy_cache_but_preserves_manual_rates(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text('{"rows": []}')
    prices = PriceTable(str(path))
    prices.bootstrap()
    assert prices.get("gpt-5.2")
    prices.update_from_rows({"gpt-5.2": PriceRow("gpt-5.2", 1, 2, source="ruční import")}, False)
    again = PriceTable(str(path))
    again.bootstrap()
    assert again.get("gpt-5.2").input_per_1k == 1
    assert not again.is_verified("gpt-5.2")


def test_gpt54_batch_rounded_cache_and_explicit_long_context_rates():
    row = PriceTable.builtin_fallback().get("gpt-5.4")
    assert row.rates(True).cached == Decimal("0.13")
    assert row.rates(True).long_cached == Decimal("0.25")
    usage = {"input_tokens": 300000, "output_tokens": 1000, "input_tokens_details": {"cached_tokens": 100000}}
    assert calculate(row.rates(True), usage).total == Decimal("0.53625")


def test_service_tiers_are_independent_of_standard():
    row = PriceTable.builtin_fallback().get("gpt-5.2")
    usage = {"input_tokens": 1000, "output_tokens": 1000}
    assert calculate(row.rates(), usage).total == Decimal("0.01575")
    assert calculate(row.rates(True), usage).total == Decimal("0.007875")
    assert calculate(row.rates(service_tier="flex"), usage).total == Decimal("0.007875")
    assert calculate(row.rates(service_tier="priority"), usage).total == Decimal("0.0315")


def test_missing_long_batch_price_does_not_borrow_live_multiplier():
    row = PriceTable.builtin_fallback().get("gpt-5.5-pro")
    assert calculate(row.rates(True), {"input_tokens": 300000, "output_tokens": 10}).total is None
    assert calculate(row.rates(True), {"input_tokens": 1000, "output_tokens": 10}).total is not None


def test_region_requires_evidence_and_applies_uplift():
    row = PriceTable.builtin_fallback().get("gpt-5.5")
    usage = {"input_tokens": 1000, "output_tokens": 1000}
    assert calculate(row.rates(regional=True), usage).total == Decimal(".0385")
    assert PriceRow("manual", 1, 2).rates(regional=True) is None


def test_repair_legacy_receipts_preserves_original_and_is_idempotent(tmp_path):
    db = ReceiptDB(str(tmp_path / "db.sqlite"))
    rid = db.insert(old_receipt())
    assert db.query()[0]["total_usd"] is None
    prices = PriceTable.builtin_fallback()
    assert db.reprice_missing(prices) == 1
    assert Decimal(db.query()[0]["total_usd"]) == Decimal(".00315")
    assert db.reprice_missing(prices) == 0
    prices.rows["gpt-5.2"] = PriceRow("gpt-5.2", 100, 200)
    assert db.reprice_missing(prices) == 0
    assert Decimal(db.query()[0]["total_usd"]) == Decimal(".00315")
    with db._connect() as con:
        revisions = con.execute("SELECT * FROM receipt_revisions WHERE receipt_id=?", (rid,)).fetchall()
    assert len(revisions) == 1
    assert json.loads(revisions[0]["data_json"])["total_usd"] is None


def test_missing_usage_stays_unknown_instead_of_zero(tmp_path):
    db = ReceiptDB(str(tmp_path / "db.sqlite"))
    db.insert(old_receipt(usage={}))
    assert db.reprice_missing(PriceTable.builtin_fallback()) == 0
    assert db.query()[0]["total_usd"] is None
    assert amount(None) != amount(0)
    assert amount("0.0000001").startswith("<")


def test_log_audit_prices_new_and_existing_receipts(tmp_path):
    settings = AppSettings(log_dir=str(tmp_path / "LOG"), db_path=str(tmp_path / "db.sqlite"))
    settings.pricing.auto_refresh_on_start = False
    db = ReceiptDB(settings.db_path)
    db.insert(old_receipt(usage={}))
    folder = tmp_path / "LOG" / "RUN_example" / "responses"
    folder.mkdir(parents=True)
    body = {"id": "resp_old", "model": "gpt-5.2", "status": "completed", "usage": {"input_tokens": 1000, "output_tokens": 100}, "output": [{"type": "file_search_call"}]}
    (folder / "QA.json").write_text(json.dumps(body))
    summary = PricingAuditor(settings, PriceTable.builtin_fallback(), db).audit()
    assert summary["updated"] == 1
    assert Decimal(db.query()[0]["total_usd"]) == Decimal(".00565")
    assert len(db.query()) == 1
    assert PricingAuditor(settings, PriceTable.builtin_fallback(), db).audit()["updated"] == 0


def test_actual_live_and_preflight_have_two_priced_receipts(app, tmp_path, monkeypatch):
    db = ReceiptDB(str(tmp_path / "db.sqlite"))
    control = CostController(db, PriceTable.builtin_fallback(), "run")
    control.fx_checked = True
    approvals = []
    monkeypatch.setattr(control, "ask", lambda estimate: approvals.append(estimate) or False)
    client = OpenAIClient("test-key")
    client.configure_validation(AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG"), db_path=db.db_path), control)
    client._policy.catalog = {"gpt-5.2"}
    client.count_input_tokens = Mock(return_value=1000)
    client.validate_resources = Mock()
    usage = {"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 400}}
    client._send_response = Mock(side_effect=[{"id": id, "model": "gpt-5.2-2025-12-11", "status": "completed", "output_text": '{"text":"OK"}', "usage": usage} for id in ("resp_trial", "resp_work")])
    control.execute(client, {"model": "gpt-5.2", "input": "Test", "max_output_tokens": 100})
    assert client._send_response.call_count == 2
    assert len(db.query()) == 2
    assert sum(Decimal(r["total_usd"]) for r in db.query()) == Decimal(".00504")
    assert sum(Decimal(r["actual_usd"]) for r in control.ledger.operations("run")) == Decimal(".00504")
    assert approvals[0]["includes_trial"]


def test_batch_prices_actual_tool_calls_instead_of_blanket_unknown(tmp_path):
    from kajovo.core.batch_costs import finalize_batches
    db = ReceiptDB(str(tmp_path / "db.sqlite"))
    ledger = CostLedger(db.db_path)
    prices = PriceTable.builtin_fallback()
    item = quote({"model": "gpt-5.2", "tools": [{"type": "file_search"}]}, 1000, prices.get("gpt-5.2").rates(True))
    item.update(custom_id="a", file_search_per_1k="2.5")
    op = ledger.reserve("run", {"items": [item], "stage": "PREFLIGHT_BATCH"})
    ledger.mark(op, "pending", "batch_1")
    body = {"id": "resp_batch", "model": "gpt-5.2", "status": "completed", "usage": {"input_tokens": 1000, "output_tokens": 100}, "output": [{"type": "file_search_call"}]}
    client = SimpleNamespace(retrieve_batch=lambda _: {"id": "batch_1", "status": "completed", "output_file_id": "file_out"},
        file_content=lambda _: json.dumps({"custom_id": "a", "response": {"status_code": 200, "body": body}}).encode())
    finalize_batches(client, db.db_path)
    assert Decimal(ledger.operations("run")[0]["actual_usd"]) == Decimal(".004075")
    assert Decimal(db.query()[0]["total_usd"]) == Decimal(".004075")


def test_panel_shows_prices_before_any_run_and_recalculates(app, tmp_path):
    panel = PricingPanel(AppSettings(), PriceTable.builtin_fallback(), ReceiptDB(str(tmp_path / "db.sqlite")))
    panel.calc_model.setCurrentText("gpt-5.2")
    assert "0.01575 USD" in panel.calc_result.text()
    panel.calc_mode.setCurrentIndex(1)
    assert "0.007875 USD" in panel.calc_result.text()
    panel.calc_output.setValue(0)
    assert "0.000875 USD" in panel.calc_result.text()
    assert "milion" in panel.data_tabs.tabText(0)
    assert panel.tbl_prices.rowCount() > 50
    panel.close()


def test_estimate_has_readable_number_even_without_provable_ceiling():
    item = quote({"model": "m", "tools": [{"type": "file_search"}]}, 1000, Rates("m", 1, 2))
    title, html = estimate_summary({"items": [item], "maximum_usd": None})
    assert "0.017 USD" in title
    assert "File search" in html


def test_unknown_tool_never_becomes_a_zero_fee():
    total, fee, _, reason = price_response(PriceTable.builtin_fallback().get("gpt-5.2"),
        {"usage": {"input_tokens": 1, "output_tokens": 1}, "output": [{"type": "future_call"}]})
    assert total is None and fee is None and "future_call" in reason


def test_refresh_reads_one_table_page_for_documented_models(monkeypatch):
    from kajovo.core.price_sources import OPENAI_PRICING
    header = "| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
    source = "Prices per 1M tokens.\n### Standard pricing data\n\n" + header + "| gpt-5.4 (<272K context length) | $2.50 | $0.25 | - | $15.00 | $5.00 | $0.50 | - | $22.50 |\n"
    source += "\n### Batch pricing data\n\n" + header + "| gpt-5.4 (<272K context length) | $1.25 | $0.13 | - | $7.50 | $2.50 | $0.25 | - | $11.25 |\n"
    source += "\n| File search | Tool call | $2.50 / 1k calls |\n"
    fetch = Mock(return_value=SimpleNamespace(text=source, raise_for_status=lambda: None))
    monkeypatch.setattr("kajovo.core.pricing.requests.get", fetch)
    prices = PriceTable(":memory:")
    prices.bootstrap()
    ok, _ = prices.refresh_from_url(OPENAI_PRICING)
    assert ok and fetch.call_count == 1
    assert prices.get("gpt-5.4").rates(True).long_cached == Decimal(".25")
    assert prices.get("gpt-5.4").file_search_per_1k == Decimal("2.5")
    assert prices.get("gpt-5.2") is not None


def test_repair_incomplete_cached_rate_snapshot(tmp_path):
    db = ReceiptDB(str(tmp_path / "db.sqlite"))
    receipt = old_receipt(usage={"input_tokens": 1000, "output_tokens": 100, "input_tokens_details": {"cached_tokens": 400}})
    receipt.pricing_snapshot = Rates("gpt-5.2", "1.75", "14").snapshot()
    db.insert(receipt)
    assert db.query()[0]["total_usd"] is None
    assert db.reprice_missing(PriceTable.builtin_fallback()) == 1
    assert Decimal(db.query()[0]["total_usd"]) == Decimal(".00252")


def test_current_table_does_not_make_old_individual_rate_fresh():
    prices = PriceTable.builtin_fallback()
    prices.rows["gpt-5.2"].verified_at = "2020-01-01"
    assert not prices.is_stale()
    assert not prices.is_recent("gpt-5.2")


def test_api_fast_response_uses_priority_price():
    row = PriceTable.builtin_fallback().get("gpt-5.2")
    cost, _, rates, _ = price_response(row, {"service_tier": "fast", "usage": {"input_tokens": 1000, "output_tokens": 1000}})
    assert cost == Decimal(".0315")
    assert rates.service_tier == "priority"


@pytest.mark.parametrize("value", ["bad", {"default": 1}, {"default": {"input": 1}}, {"default": {"input": 1, "output": 2, "explicit_long_rates": "false"}}])
def test_invalid_extended_import_is_a_readable_validation_error(value):
    with pytest.raises(ValueError):
        PriceRow("m", 1, 2, mode_rates=value)


def test_budget_change_cannot_send_mismatched_paid_trial():
    from kajovo.core.response_policy import PreflightTransport
    client = SimpleNamespace(_send_response=Mock())
    adapter = PreflightTransport(client, {"model": "gpt-5.2", "max_output_tokens": 100})
    with pytest.raises(ValueError, match="Rozpočet změnil") as error:
        adapter.create_response({"model": "gpt-5.2", "max_output_tokens": 50})
    assert error.value.request_sent is False
    client._send_response.assert_not_called()
