import os
import tempfile
import unittest

from kajovo.core.receipt import Receipt, ReceiptDB


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
