import unittest
from your_module import Receipt, ReceiptDB

class ReceiptDBTests(unittest.TestCase):
    def test_insert_and_query(self):
        # Create a new Receipt instance with the missing arguments added
        receipt = Receipt(stage="example_stage", cost=1.23)
        db = ReceiptDB()
        db.insert(receipt)
        result = db.query(receipt.id)
        self.assertEqual(result.stage, "example_stage")
        self.assertEqual(result.cost, 1.23)

if __name__ == '__main__':
    unittest.main()