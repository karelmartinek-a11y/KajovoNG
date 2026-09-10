"""Potvrzení odhadů na vlákně UI; síť a rezervace běží ve workeru."""
import json
import threading
import time
from decimal import Decimal
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QSpinBox, QDialogButtonBox, QTextBrowser
from ..core.cost_accounting import CostLedger, BudgetExceeded, calculate, money, quote, fingerprint
from ..core.price_sources import fetch_fx


class EstimateDialog(QDialog):
    def __init__(self, estimate, limit, output_limit, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Odhad nákladů")
        self.resize(720, 550)
        layout = QVBoxLayout(self)
        summary = QLabel("Cena připraveného požadavku")
        summary.setWordWrap(True)
        summary.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(summary)
        info = QTextBrowser()
        from .receipt_view import detail_toggle, estimate_summary
        layout.addWidget(info)
        detail_toggle(layout, info, estimate_summary(estimate)[1], estimate)
        layout.addWidget(QLabel("Volitelný tvrdý limit běhu v USD (prázdné = bez limitu):"))
        self.limit = QLineEdit("" if limit is None else str(limit))
        layout.addWidget(self.limit)
        layout.addWidget(QLabel("Maximum výstupních tokenů na požadavek (0 = maximum modelu). Nižší limit může přerušit soubor."))
        self.output = QSpinBox()
        self.output.setRange(0, 1_000_000)
        self.output.setValue(output_limit)
        layout.addWidget(self.output)
        self.error = QLabel()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Spustit / přepočítat změny")
        buttons.button(QDialogButtonBox.Cancel).setText("Zrušit")
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate(self):
        try:
            if self.limit.text().strip():
                money(self.limit.text().strip())
            if 0 < self.output.value() < 16:
                raise ValueError("Maximum výstupu musí být 0 nebo alespoň 16.")
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()


def describe(estimate):
    lines = ["Odhad připravené operace; budoucí kroky a opravy dosud nemají známý vstup.",
             "Výsledná částka bude vypočtena ze spotřeby API. Nejde o fakturu poskytovatele."]
    if estimate.get("error"):
        lines.append(estimate["error"])
    for item in estimate.get("items", [estimate]):
        lines.extend([f"\nModel: {item.get('model')} | vstup: {item.get('input_tokens', 'neznámý')}",
                      f"Maximum výstupu: {item.get('max_output_tokens') or 'neurčeno'}",
                      f"Vzorky: {item.get('sample_count', 0)}; " + ("P10 / medián / P90" if item.get('empirical') else "scénáře, nikoli statistický interval")])
        rates = item.get("rates")
        if rates:
            lines.append(f"Sazby USD / milion tokenů: vstup {rates['input']}, výstup {rates['output']}, čtení cache {rates.get('cached') or 'nedoloženo'}, zápis cache {rates.get('write') or 'nedoloženo'}")
            lines.append(f"Zdroj: {rates['source']} | ověřeno: {rates.get('verified_at') or 'neověřeno'}")
        else:
            lines.append("Sazby nejsou doložené.")
        for scenario in item.get("scenarios", []):
            lines.append(f"  {scenario['output_tokens']} výstupních tokenů: {scenario['usd'] or 'neznámá cena'} USD")
    lines.append(f"\nMaximum připravené operace: {estimate.get('maximum_usd') or 'nelze doložit'} USD")
    lines.append(f"Dosud vyčísleno: {estimate.get('spent_usd', '0')} USD")
    if estimate.get("fx"):
        fx = estimate["fx"]
        lines.append(f"Orientační kurz ČNB {fx['date']}: 1 USD = {fx['czk_per_usd']} CZK")
        if estimate.get("maximum_usd") is not None:
            lines.append(f"Maximum orientačně {money(estimate['maximum_usd']) * money(fx['czk_per_usd']):.2f} CZK")
    else:
        lines.append("Kurz ČNB není dostupný; USD zůstává rozhodující.")
    lines.append("Úložiště je průběžná samostatná služba; není součástí limitu jednorázových požadavků.")
    return "\n".join(lines)


class CostController(QObject):
    approval = Signal(object)
    progress_event = Signal(object)

    def __init__(self, db, table, scope, parent=None, stopped=lambda: False, project="", mode=""):
        super().__init__(parent)
        self.db, self.table, self.scope = db, table, scope
        self.ledger = CostLedger(db.db_path)
        self.stopped = stopped
        self.limit = None
        with self.ledger.connect() as con:
            saved = con.execute("SELECT limit_usd FROM cost_scopes WHERE id=?", (scope,)).fetchone()
            if saved:
                self.limit = saved[0]
        self.project, self.mode = project, mode
        self.output_limit = 0
        self.confirmed = False
        self.cancelled = False
        self.fx = None
        self.fx_checked = False
        self.catalog_checked = False
        self.approval.connect(self._approve, Qt.QueuedConnection)

    @Slot(object)
    def _approve(self, request):
        event, estimate, result = request
        try:
            if self.stopped():
                return
            dialog = EstimateDialog(estimate, self.limit, self.output_limit, self.parent())
            if dialog.exec() == QDialog.Accepted and not self.stopped():
                result.update(accepted=True, limit=dialog.limit.text().strip() or None, output=dialog.output.value())
        finally:
            event.set()

    def ask(self, estimate):
        from ..core.progress import ProgressEvent
        stage = getattr(self, "_progress_stage", "Náklady")
        self.progress_event.emit(ProgressEvent(stage, "approval", detail="Čekám na potvrzení odhadu nákladů."))
        event, result = threading.Event(), {}
        self.approval.emit((event, estimate, result))
        while not event.wait(.1):
            if self.stopped() or self.cancelled:
                raise RuntimeError("Běh zastaven před odesláním požadavku.")
        if not result.get("accepted"):
            self.cancelled = True
            raise RuntimeError("Odeslání zrušeno v odhadu nákladů; připravené podklady zůstávají uložené.")
        changed = self.output_limit != result["output"]
        self.progress_event.emit(ProgressEvent(stage, "active", detail="Odhad potvrzen; připravuji odeslání."))
        self.limit, self.output_limit = result["limit"], result["output"]
        self.ledger.set_limit(self.scope, self.limit)
        return changed

    def prepare(self, client, payloads, batch=False, stage="RESPONSE", custom_ids=None):
        if self.cancelled or self.stopped():
            raise RuntimeError("Odesílání bylo zastaveno.")
        from ..core.structured_output import prepare_payload
        for payload in payloads:
            prepare_payload(payload)
            client.validate_access(payload, batch=batch)
        self._progress_stage = stage.split("_")[0]
        if self.cancelled or self.stopped():
            raise RuntimeError("Odesílání bylo zastaveno.")
        if not self.fx_checked:
            try:
                self.fx = fetch_fx()
            except Exception:
                pass
            self.fx_checked = True
        if not self.catalog_checked:
            if not self.table.rows or not self.table.last_updated or time.time() - self.table.last_updated > 72 * 3600:
                self.table.refresh_from_url("https://developers.openai.com/api/docs/pricing.md")
            self.catalog_checked = True
        confirm = batch or not self.confirmed
        while True:
            items = []
            for payload in payloads:
                if self.output_limit:
                    payload["max_output_tokens"] = self.output_limit
                count_error = ""
                try:
                    count = client.count_input_tokens(payload)
                except Exception as exc:
                    count = None
                    count_error = "Nepodařilo se spočítat vstupní tokeny: " + str(exc)
                row = self.table.get(payload["model"])
                if row and row.output_token_limit and payload.get("max_output_tokens", 0) > row.output_token_limit:
                    raise ValueError(f"Model {row.model} povoluje nejvýše {row.output_token_limit} výstupních tokenů.")
                from urllib.parse import urlsplit
                host = urlsplit(getattr(client, "base_url", getattr(getattr(client, "client", None), "base_url", "https://api.openai.com"))).hostname or ""
                regional = host.endswith(".api.openai.com")
                rates = row.rates(batch, payload.get("service_tier"), regional) if row else None
                samples = []
                for r in self.db.query():
                    usage = json.loads(r["usage_json"] or "{}")
                    if usage.get("_requested_model", r["model"]) == payload["model"] and r["flow_type"] == stage and usage.get("_reasoning") == payload.get("reasoning") and usage.get("_completed"):
                        samples.append(r["output_tokens"])
                item = quote(payload, count, rates, samples, model_output_limit=row.output_token_limit if row else None)
                item["regional"] = regional
                item["file_search_per_1k"] = str(row.file_search_per_1k) if row and row.file_search_per_1k is not None else None
                if count_error:
                    item["reason"] = count_error
                if row and row.output_token_limit:
                    for scenario in item["scenarios"]:
                        if scenario["output_tokens"] > row.output_token_limit:
                            scenario["output_tokens"] = row.output_token_limit
                            clipped = calculate(rates, {"input_tokens": count, "output_tokens": row.output_token_limit})
                            scenario["usd"] = str(clipped.total) if clipped.total is not None else None
                if custom_ids:
                    item["custom_id"] = custom_ids[len(items)]
                if not row or not row.verified_at or not row.source.startswith("https://developers.openai.com/") or not self.table.is_recent(payload["model"]) or not self.table.last_updated or time.time() - self.table.last_updated > 72 * 3600:
                    item["maximum_usd"] = None
                items.append(item)
            maxima = [item["maximum_usd"] for item in items]
            estimate = {"items": items, "maximum_usd": str(sum((money(v) for v in maxima), Decimal(0))) if all(v is not None for v in maxima) else None,
                        "fx": self.fx, "stage": stage, "batch": batch,
                        "spent_usd": str(sum((money(r["actual_usd"]) for r in self.ledger.operations(self.scope) if r["actual_usd"] is not None), Decimal(0)))}
            estimate["includes_trial"] = not stage.startswith("PREFLIGHT") and getattr(client, "cost_control", None) is self
            if confirm:
                if self.ask(estimate):
                    continue
                self.confirmed = True
            if any(fingerprint(p) != q["payload_hash"] for p, q in zip(payloads, items, strict=True)):
                raise ValueError("Požadavek se od potvrzeného odhadu změnil.")
            if self.stopped():
                raise RuntimeError("Běh zastaven před odesláním.")
            try:
                op = self.ledger.reserve(self.scope, estimate)
                return op, items
            except BudgetExceeded as exc:
                estimate["error"] = str(exc)
                self.ask(estimate)
                confirm = True

    def execute(self, client, payload, stage="RESPONSE"):
        from ..core.contracts import ContractError
        operation, items = self.prepare(client, [payload], stage=stage)
        from ..core.progress import ProgressEvent
        self.progress_event.emit(ProgressEvent(stage, "waiting", detail="Požadavek odesílám do API; čekám na odpověď."))
        try:
            response = client.create_response(payload)
        except ContractError as exc:
            response = getattr(exc, "response", None)
            if not isinstance(response, dict):
                self.ledger.mark(operation, "unknown")
                raise
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(exc.__cause__, "status_code", None)
            self.ledger.settle(operation, None, usage={"_outcome": "api_rejected" if status in (400, 401, 403, 404, 422) else "transport_unknown",
                "_error_type": type(exc.__cause__ or exc).__name__, "_http_status": status,
                "_request_id": getattr(exc, "request_id", None), "_elapsed_s": getattr(exc, "elapsed_s", None)})
            self.ledger.mark(operation, "released" if getattr(exc, "request_sent", None) is False or status in (400, 401, 403, 404, 422) else "unknown")
            raise
        from ..core.cost_accounting import Rates
        snapshot = items[0]["rates"]
        rates = Rates(**snapshot) if snapshot else None
        actual_model = response.get("model") or payload["model"]
        actual = self.table.get(actual_model)
        from ..core.model_registry import model_spec
        try:
            same_model = model_spec(actual_model)["canonical"] == model_spec(payload["model"])["canonical"]
        except ValueError:
            same_model = actual_model == payload["model"]
        if not same_model:
            rates = None
            actual = None
        if same_model and actual and snapshot and actual.model != snapshot["model"]:
            rates = actual.rates(service_tier=response.get("service_tier") or payload.get("service_tier"), regional=items[0].get("regional", False))
        actual_tier = response.get("service_tier") or payload.get("service_tier")
        if actual_tier not in (None, "auto", "default") and (not rates or rates.service_tier != actual_tier):
            rates = actual.rates(service_tier=actual_tier, regional=items[0].get("regional", False)) if actual else None
        from ..core.pricing import price_response
        usage = {**(response.get("usage") or {}), "_request_id": response.get("_request_id"),
            "_requested_model": payload["model"], "_reasoning": payload.get("reasoning"),
            "_completed": response.get("status") == "completed", "_service_tier": actual_tier,
            "_regional": items[0].get("regional", False),
            "_file_search_calls": sum(o.get("type") == "file_search_call" for o in response.get("output", [])),
            "_unsupported_tools": [o.get("type") for o in response.get("output", []) if o.get("type", "").endswith("_call") and o["type"] not in ("file_search_call", "function_call")]}
        total, tool_cost, rates, reason = price_response(actual, {**response, "usage": usage, "service_tier": actual_tier}, rates=rates,
            regional=items[0].get("regional", False))
        snapshot = rates.snapshot() if rates else None
        usage["_pricing_reason"] = reason
        usage["_outcome"] = response.get("status") or "unknown"
        usage["_pricing_status"] = "priced" if total is not None else "unknown_price"
        self.ledger.settle(operation, total, response.get("id"), usage, snapshot)
        from ..core.receipt import Receipt
        self.db.insert(Receipt(self.scope, time.time(), self.project, response.get("model") or payload["model"], self.mode,
                               stage, response.get("id"), None, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                               float(tool_cost) if tool_cost is not None else None,
                               0.0, float(total) if total is not None else None, bool(rates and rates.verified_at),
                               "Spotřeba API", {}, usage, pricing_snapshot=snapshot if rates else {}))
        from ..core.structured_output import validate_output
        validate_output(response, payload)
        return response


def show_final_receipt(parent, db, scope):
    operations = CostLedger(db.db_path).operations(scope)
    if not operations:
        return
    total = sum((money(r["actual_usd"]) for r in operations if r["actual_usd"] is not None), Decimal(0))
    unresolved = sum(r["status"] not in ("settled", "released") for r in operations)
    dialog = QDialog(parent)
    dialog.setWindowTitle("Výsledná účtenka")
    dialog.resize(740, 500)
    layout = QVBoxLayout(dialog)
    from .receipt_view import amount, detail_toggle, table
    summary = QLabel(("Vyčíslená část: " if unresolved else "Celková cena: ") + amount(total) + f" · Neuzavřené operace: {unresolved}")
    summary.setWordWrap(True)
    summary.setStyleSheet("font-size: 16px; font-weight: 700;")
    layout.addWidget(summary)
    text = QTextBrowser()
    layout.addWidget(text)
    rows, details = [], []
    states = {"settled": "Vyčísleno", "released": "Neodesláno / odmítnuto", "pending": "Batch čeká", "reserved": "Připraveno", "unknown": "Chybí podklady", "terminal_unknown": "Neúplný výsledek Batch"}
    for op in operations:
        estimate = json.loads(op["estimate_json"])
        usage = json.loads(op["usage_json"] or "null")
        stage = estimate.get("stage", "Požadavek")
        kind = "Zkouška" if stage.startswith("PREFLIGHT") else "Pracovní volání"
        item_models = sorted({i.get("model", "") for i in estimate.get("items", [])})
        cost_text = "Bez účtovaného požadavku" if op["status"] == "released" else amount(op["actual_usd"])
        rows.append((kind + " · " + ("BATCH" if estimate.get("batch") else "LIVE"), ", ".join(item_models), states.get(op["status"], op["status"]), cost_text))
        details.append({**op, "estimate": estimate, "usage": usage})
    html = "<h2>" + ("Vyčíslená část: " if unresolved else "Celková cena: ") + amount(total) + "</h2>"
    html += table(("Operace", "Model", "Stav", "Cena"), rows)
    if unresolved:
        html += "<p>Součet je neúplný. Čekající nebo nevyčíslené operace nejsou započteny jako nula.</p>"
    html += "<p>Cena = skutečné tokeny API × uložené sazby + skutečná volání nástrojů. Reasoning tokeny jsou již součástí výstupu. Úložiště se účtuje průběžně mimo tento součet.</p>"
    fx = next((d["estimate"].get("fx") for d in details if d["estimate"].get("fx")), None)
    if fx:
        html += "<p>Orientačně " + amount(total * money(fx["czk_per_usd"]), "CZK") + " · ČNB " + fx["date"] + ".</p>"
    html += "<p>Nejde o fakturu OpenAI. Podklady ke každé operaci jsou dostupné tlačítkem níže.</p>"
    detail_toggle(layout, text, html, details)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
