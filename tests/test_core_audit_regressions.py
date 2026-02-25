import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from kajovo.core.contracts import ContractError, parse_json_strict, validate_paths
from kajovo.core.model_capabilities import split_text as caps_split_text
from kajovo.core.openai_client import OpenAIClient, OpenAIError
from kajovo.core.pipeline import split_text as pipeline_split_text
from kajovo.core.pricing import PriceRow, compute_cost
from kajovo.core.receipt import Receipt, ReceiptDB


class SplitTextTests(unittest.TestCase):
    def test_split_text_pipeline(self):
        self.assertEqual(pipeline_split_text("abcdef", 2), ["ab", "cd", "ef"])

    def test_split_text_model_caps_empty(self):
        self.assertEqual(caps_split_text("", 10), [""])


class ContractsTests(unittest.TestCase):
    def test_parse_json_strict_extracts_embedded_object(self):
        out = parse_json_strict("header\n{\"a\":1}\nfooter")
        self.assertEqual(out, {"a": 1})

    def test_parse_json_strict_rejects_array(self):
        with self.assertRaises(ContractError):
            parse_json_strict("[1,2]")

    def test_validate_paths(self):
        validate_paths([{"path": "ok/file.txt"}])
        with self.assertRaises(ContractError):
            validate_paths([{"path": "../bad.txt"}])


class PricingTests(unittest.TestCase):
    def test_compute_cost_with_tools_and_storage(self):
        row = PriceRow(
            model="gpt-4o-mini",
            input_per_1k=1.0,
            output_per_1k=2.0,
            file_search_per_1k=0.5,
            storage_per_gb_day=0.25,
        )
        total, tool, storage = compute_cost(row, 2000, 1000, use_file_search=True, storage_gb_days=2)
        self.assertAlmostEqual(tool, 1.0)
        self.assertAlmostEqual(storage, 0.5)
        self.assertAlmostEqual(total, 5.5)


class ReceiptDBTests(unittest.TestCase):
    def test_insert_and_query(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = os.path.join(td, "kajovo.sqlite")
            db = ReceiptDB(db_path)
            rid = db.insert(
                Receipt(
                    run_id="RUN_010120251212_ABCD",
                    created_at=1.0,
                    project="P",
                    model="gpt-4o-mini",
                    mode="GENERATE",
                    flow_type="response",
                    response_id="resp_1",
                    batch_id=None,
                    input_tokens=10,
                    output_tokens=20,
                    tool_cost=0.0,
                    storage_cost=0.0,
                    total_cost=1.23,
                    pricing_verified=False,
                    notes="n",
                    log_paths={"run": "x"},
                    usage={"input_tokens": 10},
                )
            )
            self.assertGreater(rid, 0)
            rows = db.query()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["response_id"], "resp_1")


class OpenAIClientErrorMappingTests(unittest.TestCase):
    def test_raises_openai_error_for_non_retryable_http(self):
        client = OpenAIClient("k")
        client._sdk = None
        resp = Mock(status_code=400, headers={"content-type": "application/json"}, text="bad request")
        client.session.request = Mock(return_value=resp)
        with self.assertRaises(OpenAIError):
            client._req("GET", "/models")

    def test_retries_on_timeout_then_succeeds(self):
        client = OpenAIClient("k")
        client._sdk = None
        ok = Mock(status_code=200, headers={"content-type": "application/json"})
        ok.json.return_value = {"data": []}
        client.session.request = Mock(side_effect=[requests.Timeout("t"), ok])
        with patch("kajovo.core.openai_client.time.sleep", return_value=None):
            out = client._req("GET", "/models")
        self.assertEqual(out, {"data": []})


if __name__ == "__main__":
    unittest.main()
