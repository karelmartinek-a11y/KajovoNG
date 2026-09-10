import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QTextBrowser
import pytest
import time

from kajovo.desktop.finance import CostController, EstimateDialog, show_final_receipt
from kajovo.core.pricing import PriceRow, PriceTable
from kajovo.core.receipt import ReceiptDB


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def controller(tmp_path, monkeypatch):
    table = PriceTable(":memory:")
    table.last_updated = time.time()
    table.rows["gpt-4.1"] = PriceRow("gpt-4.1", ".001", ".002", source="https://developers.openai.com/api/docs/pricing", verified_at="2026-09-09")
    result = CostController(ReceiptDB(str(tmp_path / "db.sqlite")), table, "run")
    monkeypatch.setattr("kajovo.desktop.finance.fetch_fx", lambda: {"date": "2026-09-09", "czk_per_usd": "21"})
    return result


def test_dialog_close_never_accepts(app):
    dialog = EstimateDialog({"items": [], "maximum_usd": None}, None, 0)
    dialog.close()
    assert dialog.result() == QDialog.Rejected


def test_controller_requotes_output_and_uses_same_snapshot(app, tmp_path, monkeypatch):
    control = controller(tmp_path, monkeypatch)
    approvals, calls = [], []
    def ask(estimate):
        approvals.append(estimate)
        if len(approvals) == 1:
            control.output_limit = 100
            control.limit = "1"
            control.ledger.set_limit("run", "1")
            return True
        return False
    monkeypatch.setattr(control, "ask", ask)
    class Client:
        def validate_access(self, payload, batch=False):
            from kajovo.core.request_rules import validate_response_payload
            validate_response_payload(payload)
        def count_input_tokens(self, payload):
            return 1000
        def create_response(self, payload):
            calls.append(dict(payload))
            return {"id": "r", "model": "gpt-4.1", "status": "completed", "output_text": '{"text":"OK"}', "usage": {"input_tokens": 1000, "output_tokens": 10}}
    control.execute(Client(), {"model": "gpt-4.1", "input": "x"})
    assert len(approvals) == 2 and len(calls) == 1
    assert calls[0]["max_output_tokens"] == 100
    assert control.ledger.operations("run")[0]["actual_usd"] == "0.00102"
    assert control.db.query()[0]["total_usd"] == "0.00102"
    texts = []
    monkeypatch.setattr(QDialog, "exec", lambda dialog: texts.append(dialog.findChild(QTextBrowser).toPlainText()))
    show_final_receipt(None, control.db, "run")
    assert "0.00102 USD" in texts[0] and "CZK" in texts[0]


def test_timeout_keeps_reservation_and_does_not_retry(app, tmp_path, monkeypatch):
    control = controller(tmp_path, monkeypatch)
    monkeypatch.setattr(control, "ask", lambda _: False)
    calls = []
    class Client:
        def validate_access(self, payload, batch=False):
            from kajovo.core.request_rules import validate_response_payload
            validate_response_payload(payload)
        def count_input_tokens(self, payload):
            return 1000
        def create_response(self, payload):
            calls.append(payload)
            raise TimeoutError("timeout")
    with pytest.raises(TimeoutError):
        control.execute(Client(), {"model": "gpt-4.1", "max_output_tokens": 100})
    assert len(calls) == 1
    operation = control.ledger.operations("run")[0]
    assert operation["status"] == "unknown" and operation["reserved_usd"] == "0.0012"


def test_explicit_snapshot_keeps_verified_cost(app, tmp_path, monkeypatch):
    from dataclasses import replace
    control = controller(tmp_path, monkeypatch)
    control.table.rows["gpt-4.1-2025-04-14"] = replace(control.table.rows["gpt-4.1"], model="gpt-4.1-2025-04-14")
    monkeypatch.setattr(control, "ask", lambda _: False)
    class Client:
        def validate_access(self, payload, batch=False):
            from kajovo.core.request_rules import validate_response_payload
            validate_response_payload(payload)
        def count_input_tokens(self, payload):
            return 1000
        def create_response(self, payload):
            return {"id": "r", "model": "gpt-4.1-2025-04-14", "status": "completed", "output_text": '{"text":"OK"}', "usage": {"input_tokens": 1000, "output_tokens": 100}}
    control.execute(Client(), {"model": "gpt-4.1", "max_output_tokens": 100})
    assert control.ledger.operations("run")[0]["actual_usd"] == "0.0012"


def test_cancelled_controller_never_sends(app, tmp_path, monkeypatch):
    control = controller(tmp_path, monkeypatch)
    control.cancelled = True
    with pytest.raises(RuntimeError):
        control.execute(object(), {"model": "gpt-4.1"})
    assert not control.ledger.operations("run")


def test_batch_has_second_confirmation_and_one_aggregate_reservation(app, tmp_path, monkeypatch):
    control = controller(tmp_path, monkeypatch)
    control.table.rows["gpt-4.1"].batch_input_per_1k = ".0005"
    control.table.rows["gpt-4.1"].batch_output_per_1k = ".001"
    approvals = []
    monkeypatch.setattr(control, "ask", lambda estimate: (approvals.append(estimate), False)[1])
    class Client:
        def validate_access(self, payload, batch=False):
            from kajovo.core.request_rules import validate_response_payload
            validate_response_payload(payload)
        def count_input_tokens(self, payload):
            return 1000
        def create_response(self, payload):
            return {"id": "r", "model": "gpt-4.1", "status": "completed", "output_text": '{"text":"OK"}', "usage": {"input_tokens": 1000, "output_tokens": 100}}
    client = Client()
    payload = {"model": "gpt-4.1", "max_output_tokens": 100}
    control.execute(client, dict(payload))
    op, items = control.prepare(client, [dict(payload), dict(payload)], batch=True, custom_ids=["a", "b"])
    assert len(approvals) == 2
    assert approvals[1]["spent_usd"] == "0.0012"
    assert approvals[1]["maximum_usd"] == "0.0012"
    assert [item["custom_id"] for item in items] == ["a", "b"]
    assert control.ledger.operations("run")[-1]["id"] == op
    assert len(control.ledger.operations("run")) == 2
