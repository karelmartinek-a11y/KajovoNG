import unittest

import pytest

from kajovo.core.pricing import PriceRow, PriceTable, compute_cost


def test_zero_batch_price_replaces_missing_price():
    table = PriceTable(":memory:")
    table.update_from_rows({"model": PriceRow("model", 1, 2)}, verified=False)
    table.update_from_rows({"model": PriceRow("model", 1, 2, 0, 0)}, verified=False)
    assert compute_cost(table.get("model"), 1000, 1000, is_batch=True)[0] == 0


def test_documented_snapshot_uses_verified_canonical_rate():
    table = PriceTable.builtin_fallback()
    table.verified = True
    assert table.get("gpt-4o-mini-2024-07-18") == table.get("gpt-4o-mini")
    assert table.is_verified("gpt-4o-mini-2024-07-18")
    assert table.get("gpt-4o-mini-2099-01-01") is None
    assert table.is_verified("gpt-4o-mini")
    assert table.get("gpt-4o-mini-unknown") is None


@pytest.mark.parametrize("price", [-1, float("inf"), float("nan")])
def test_invalid_prices_rejected(price):
    with pytest.raises(ValueError):
        PriceRow("model", price, 1)


def test_price_units_and_old_cache(tmp_path):
    row = PriceTable.builtin_fallback().get("gpt-4o-mini")
    assert compute_cost(row, 1_000_000, 1_000_000)[0] == pytest.approx(0.75)
    path = tmp_path / "prices.json"
    path.write_text('{"rows":[{"model":"gpt-4o-mini","input":150,"output":600}]}', encoding="utf-8")
    table = PriceTable(str(path))
    table.load_cache()
    assert not table.rows
    table.update_from_rows({row.model: row}, verified=False)
    reloaded = PriceTable(str(path))
    reloaded.load_cache()
    assert reloaded.get(row.model) == row


class PricingTests(unittest.TestCase):
    def test_compute_cost_with_tools_and_storage(self):
        row = PriceRow(
            model="gpt-4o-mini",
            input_per_1k=1.0,
            output_per_1k=2.0,
            file_search_per_1k=0.5,
            storage_per_gb_day=0.25,
        )
        total, tool, storage = compute_cost(row, 2000, 1000, use_file_search=True, storage_gb_days=2, file_search_calls=2)
        self.assertAlmostEqual(tool, 0.001)
        self.assertAlmostEqual(storage, 0.5)
        self.assertAlmostEqual(total, 4.501)
