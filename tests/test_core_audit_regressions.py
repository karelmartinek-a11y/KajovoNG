import unittest
from tempfile import TemporaryDirectory

from kajovo.core.receipt import Receipt, ReceiptDB

class ReceiptDBTests(unittest.TestCase):
    def test_insert_and_query(self):
        with TemporaryDirectory() as tmp:
            db = ReceiptDB(f"{tmp}/kajovo.sqlite")
            receipt = Receipt(
                run_id="run-1",
                created_at=123.0,
                project="proj",
                model="gpt-4.1-mini",
                mode="GENERATE",
                flow_type="responses",
                stage="example_stage",
                response_id="resp-1",
                batch_id=None,
                input_tokens=10,
                output_tokens=20,
                tool_cost=0.1,
                storage_cost=0.0,
                total_cost=0.3,
                cost=0.3,
                pricing_verified=True,
                notes="note",
                log_paths={"run": "x"},
                usage={"input_tokens": 10, "output_tokens": 20},
            )
            db.insert(receipt)
            rows = db.query()

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["stage"], "example_stage")
            self.assertEqual(rows[0]["cost"], 0.3)

if __name__ == '__main__':
    unittest.main()
