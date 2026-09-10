"""Cenové potvrzení a účetní most mezi pracovníkem a rozhraním."""

import json
import threading
import time
from decimal import Decimal
from html import escape
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Signal, Slot, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser
from ..core.cost_accounting import CostLedger, BudgetExceeded, Rates, money, quote, fingerprint
from ..core.price_sources import fetch_fx
from ..core.progress import ProgressEvent
from ..core.pricing import price_response
from ..core.receipt import Receipt
from ..core.structured_output import prepare_payload, validate_output
from ..core.contracts import ContractError
from ..core.model_registry import model_spec
from .design import column, label, text, number, form, button, FitDialog
from .dialogs import DetailDialog


def amount(value, unit="USD"):
    if value is None:
        return "Nelze vyčíslit"
    value = Decimal(str(value))
    if 0 < abs(value) < Decimal("0.000001"):
        return "<0.000001 " + unit
    rendered = format(value, ".6f").rstrip("0").rstrip(".")
    return (rendered if "." in rendered else rendered + ".00") + " " + unit


def table(headers, rows):
    def cell(value, tag):
        return f'<{tag} align="left">{escape(str(value if value is not None else "Nedoloženo"))}</{tag}>'

    content = "<tr>" + "".join(cell(value, "th") for value in headers) + "</tr>"
    for values in rows:
        content += "<tr>" + "".join(cell(value, "td") for value in values) + "</tr>"
    return '<table width="100%" cellpadding="8" cellspacing="0" border="1">' + content + "</table>"


def estimate_summary(estimate):
    items = estimate.get("items", [estimate])
    values = [item.get("scenarios", []) for item in items]
    middles = [
        scenarios[min(1, len(scenarios) - 1)].get("usd") if scenarios else None
        for scenarios in values
    ]
    typical = (
        sum((money(value) for value in middles), Decimal(0))
        if all(value is not None for value in middles)
        else None
    )
    multiplier = 2 if estimate.get("includes_trial") else 1
    title = (
        "Pracovní volání a zkouška: přibližně " if multiplier == 2 else "Orientační cena: "
    ) + amount(typical * multiplier if typical is not None else None)
    content = (
        "<h2>"
        + escape(title)
        + "</h2><p>"
        + ("BATCH" if estimate.get("batch") else "LIVE")
        + " · "
        + escape(estimate.get("stage", "Požadavek"))
        + "</p>"
    )
    for item in items:
        content += "<h3>" + escape(item.get("model", "Model neuveden")) + "</h3>"
        content += (
            "<p>Vstup: "
            + str(item.get("input_tokens", "neznámý"))
            + " tokenů. Maximum výstupu: "
            + str(item.get("max_output_tokens") or "maximum modelu")
            + ".</p>"
        )
        content += table(
            ("Výstupní tokeny", "Odhad ceny"),
            [(s["output_tokens"], amount(s.get("usd"))) for s in item.get("scenarios", [])],
        )
        content += (
            "<p>"
            + (
                "P10 / medián / P90 z dokončených odpovědí."
                if item.get("empirical")
                else "Scénáře délky výstupu; nejde o statistický interval."
            )
            + "</p>"
        )
        rates = item.get("rates")
        if rates:
            content += (
                "<p>Sazby USD / milion tokenů: vstup "
                + amount(rates.get("input"))
                + ", výstup "
                + amount(rates.get("output"))
                + ", cache "
                + amount(rates.get("cached"))
                + ".</p>"
            )
            content += (
                "<p>Zdroj: "
                + escape(rates.get("source", ""))
                + " · ověřeno "
                + escape(rates.get("verified_at", ""))
                + ".</p>"
            )
        if item.get("reason"):
            content += "<p>" + escape(item["reason"]) + "</p>"
    content += (
        "<p>Maximum připravené operace: "
        + amount(estimate.get("maximum_usd"))
        + ". Dosud vyčísleno: "
        + amount(estimate.get("spent_usd", 0))
        + ".</p>"
    )
    if multiplier == 2:
        content += "<p>Zkouška používá stejné zadání a účtuje se samostatně. Scénáře v tabulce ukazují pracovní část; nadpis zahrnuje stejně dlouhou zkoušku. Převzatá dokončená zkouška BATCH se neopakuje.</p>"
    fx = estimate.get("fx")
    if fx and typical is not None:
        content += (
            "<p>Orientačně "
            + amount(typical * multiplier * money(fx["czk_per_usd"]), "CZK")
            + " · ČNB "
            + escape(fx["date"])
            + ".</p>"
        )
    content += "<p>Budoucí kroky nemají známý vstup. File search a další nástroje mohou přidat další tokeny a poplatky. Úložiště se účtuje průběžně mimo tento limit. Výsledná cena se počítá ze spotřeby API; nejde o fakturu OpenAI.</p>"
    if estimate.get("error"):
        content += "<p><b>" + escape(estimate["error"]) + "</b></p>"
    return title, content


class EstimateDialog(FitDialog):
    def __init__(self, estimate, limit, output_limit, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Potvrzení ceny")
        self.resize(760, 660)
        layout = column(self, 16)
        layout.setSpacing(8)
        layout.addWidget(label("Cena před odesláním", "Heading"))
        browser = QTextBrowser()
        browser.setHtml(estimate_summary(estimate)[1])
        layout.addWidget(browser, 1)
        technical = button("Technické podklady")
        technical.setCheckable(True)
        technical.toggled.connect(
            lambda checked: (
                browser.setPlainText(json.dumps(estimate, ensure_ascii=False, indent=2))
                if checked
                else browser.setHtml(estimate_summary(estimate)[1])
            )
        )
        layout.addWidget(technical)
        fields = form(layout)
        self.limit = text("" if limit is None else limit, "Bez pevného limitu")
        self.output = number(output_limit, maximum=1_000_000)
        fields.addRow("Limit celého běhu (USD)", self.limit)
        fields.addRow("Maximum výstupu (0 = maximum modelu)", self.output)
        layout.addWidget(
            label(
                "Změna výstupu vyžaduje přepočítání ceny. Nižší maximum může přerušit soubor.",
                "Hint",
            )
        )
        self.error = label()
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Potvrdit / přepočítat")
        buttons.button(QDialogButtonBox.Cancel).setText("Zrušit odeslání")
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
            self.error.show()
        else:
            self.accept()


class CostController(QObject):
    approval = Signal(object)
    progress_event = Signal(object)

    def __init__(self, db, table, scope, parent=None, stopped=lambda: False, project="", mode=""):
        super().__init__(parent)
        self.db = db
        self.table = table
        self.scope = scope
        self.stopped = stopped
        self.project = project
        self.mode = mode
        self.ledger = CostLedger(db.db_path)
        with self.ledger.connect() as connection:
            saved = connection.execute(
                "SELECT limit_usd FROM cost_scopes WHERE id=?", (scope,)
            ).fetchone()
        self.limit = saved[0] if saved else None
        self.output_limit = 0
        self.confirmed = self.cancelled = self.fx_checked = self.catalog_checked = False
        self.fx = None
        self.approval.connect(self._approve, Qt.QueuedConnection)

    @Slot(object)
    def _approve(self, request):
        event, estimate, answer = request
        try:
            if self.stopped() or self.cancelled:
                return
            dialog = EstimateDialog(estimate, self.limit, self.output_limit, self.parent())
            if dialog.exec() == QDialog.Accepted and not self.stopped():
                answer.update(
                    accepted=True,
                    limit=dialog.limit.text().strip() or None,
                    output=dialog.output.value(),
                )
        finally:
            event.set()

    def ask(self, estimate):
        self.progress_event.emit(
            ProgressEvent(
                estimate.get("stage", "Cena"), "approval", detail="Čekám na potvrzení ceny."
            )
        )
        event, answer = threading.Event(), {}
        self.approval.emit((event, estimate, answer))
        while not event.wait(0.1):
            self._check()
        if not answer.get("accepted"):
            self.cancelled = True
            raise RuntimeError("Odeslání zrušeno; připravené podklady zůstávají uložené.")
        changed = self.output_limit != answer["output"]
        self.output_limit = answer["output"]
        self.limit = answer["limit"]
        self.ledger.set_limit(self.scope, self.limit)
        self.progress_event.emit(ProgressEvent(estimate.get("stage", "Cena"), "active"))
        return changed

    def _check(self):
        if self.cancelled or self.stopped():
            raise RuntimeError("Běh zastaven před odesláním požadavku.")

    def prepare(self, client, payloads, batch=False, stage="RESPONSE", custom_ids=None):
        self._check()
        for payload in payloads:
            prepare_payload(payload)
            client.validate_access(payload, batch=batch)
        if not self.fx_checked:
            try:
                self.fx = fetch_fx()
            except Exception:
                self.fx = None
            self.fx_checked = True
        if not self.catalog_checked:
            if self.table.is_stale(72):
                self.table.refresh_from_url("https://developers.openai.com/api/docs/pricing.md")
            self.catalog_checked = True
        needs_confirmation = batch or not self.confirmed
        while True:
            items = []
            for index, payload in enumerate(payloads):
                self._check()
                if self.output_limit:
                    payload["max_output_tokens"] = self.output_limit
                client.validate_access(payload, batch=batch)
                row = self.table.get(payload["model"])
                maximum = row.output_token_limit if row else None
                if maximum and payload.get("max_output_tokens", 0) > maximum:
                    raise ValueError(
                        f"Model {payload['model']} povoluje nejvýše {maximum} výstupních tokenů."
                    )
                reason = ""
                try:
                    count = client.count_input_tokens(payload)
                except Exception as exc:
                    count = None
                    reason = f"Počet vstupních tokenů není dostupný: {exc}"
                endpoint = getattr(
                    client,
                    "base_url",
                    getattr(getattr(client, "client", None), "base_url", "https://api.openai.com"),
                )
                regional = (urlsplit(endpoint).hostname or "").endswith(".api.openai.com")
                rates = row.rates(batch, payload.get("service_tier"), regional) if row else None
                samples = []
                for receipt in self.db.query():
                    usage = json.loads(receipt["usage_json"] or "{}")
                    if (
                        usage.get("_requested_model", receipt["model"]) == payload["model"]
                        and receipt["flow_type"] == stage
                        and usage.get("_completed")
                        and usage.get("_reasoning") == payload.get("reasoning")
                    ):
                        samples.append(receipt["output_tokens"])
                item = quote(payload, count, rates, samples, model_output_limit=maximum)
                item.update(
                    regional=regional,
                    file_search_per_1k=str(row.file_search_per_1k)
                    if row and row.file_search_per_1k is not None
                    else None,
                )
                if reason:
                    item["reason"] = reason
                if custom_ids:
                    item["custom_id"] = custom_ids[index]
                if (
                    not row
                    or not row.source.startswith("https://developers.openai.com/")
                    or not row.verified_at
                    or not self.table.is_recent(payload["model"])
                    or self.table.is_stale(72)
                ):
                    item["maximum_usd"] = None
                items.append(item)
            maxima = [item["maximum_usd"] for item in items]
            estimate = dict(
                items=items,
                maximum_usd=str(sum((money(value) for value in maxima), Decimal(0)))
                if all(value is not None for value in maxima)
                else None,
                stage=stage,
                batch=batch,
                fx=self.fx,
                spent_usd=str(
                    sum(
                        (
                            money(op["actual_usd"])
                            for op in self.ledger.operations(self.scope)
                            if op["actual_usd"] is not None
                        ),
                        Decimal(0),
                    )
                ),
                includes_trial=not stage.startswith("PREFLIGHT")
                and getattr(client, "cost_control", None) is self,
            )
            if needs_confirmation:
                if self.ask(estimate):
                    continue
                self.confirmed = True
            self._check()
            if any(
                fingerprint(payload) != item["payload_hash"]
                for payload, item in zip(payloads, items, strict=True)
            ):
                raise ValueError("Požadavek se změnil od potvrzení ceny.")
            try:
                return self.ledger.reserve(self.scope, estimate), items
            except BudgetExceeded as exc:
                estimate["error"] = str(exc)
                self.ask(estimate)
                needs_confirmation = True

    def execute(self, client, payload, stage="RESPONSE"):
        operation, items = self.prepare(client, [payload], stage=stage)
        self.progress_event.emit(ProgressEvent(stage, "waiting", detail="Čekám na odpověď API."))
        try:
            response = client.create_response(payload)
        except ContractError as exc:
            response = getattr(exc, "response", None)
            if not isinstance(response, dict):
                self.ledger.mark(operation, "unknown")
                raise
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(
                exc.__cause__, "status_code", None
            )
            rejected = status in (400, 401, 403, 404, 422)
            self.ledger.settle(
                operation,
                None,
                usage={
                    "_outcome": "api_rejected" if rejected else "transport_unknown",
                    "_error_type": type(exc).__name__,
                    "_http_status": status,
                },
            )
            self.ledger.mark(
                operation,
                "released"
                if rejected or getattr(exc, "request_sent", None) is False
                else "unknown",
            )
            raise
        item = items[0]
        snapshot = item["rates"]
        rates = Rates(**snapshot) if snapshot else None
        actual_model = response.get("model") or payload["model"]
        row = self.table.get(actual_model)
        try:
            equivalent = (
                model_spec(actual_model)["canonical"] == model_spec(payload["model"])["canonical"]
            )
        except ValueError:
            equivalent = actual_model == payload["model"]
        tier = response.get("service_tier") or payload.get("service_tier")
        if not equivalent:
            row = rates = None
        elif (
            row
            and rates
            and (
                row.model != rates.model
                or tier not in (None, "auto", "default", rates.service_tier)
            )
        ):
            rates = row.rates(service_tier=tier, regional=item["regional"])
        usage = dict(response.get("usage") or {})
        usage.update(
            _requested_model=payload["model"],
            _reasoning=payload.get("reasoning"),
            _completed=response.get("status") == "completed",
            _request_id=response.get("_request_id"),
            _service_tier=tier,
            _regional=item["regional"],
            _file_search_calls=sum(
                part.get("type") == "file_search_call" for part in response.get("output", [])
            ),
            _unsupported_tools=[
                part["type"]
                for part in response.get("output", [])
                if part.get("type", "").endswith("_call")
                and part["type"] not in ("file_search_call", "function_call")
            ],
        )
        total, tool, rates, reason = price_response(
            row,
            dict(response, usage=usage, service_tier=tier),
            rates=rates,
            regional=item["regional"],
        )
        usage.update(
            _pricing_reason=reason,
            _outcome=response.get("status") or "unknown",
            _pricing_status="priced" if total is not None else "unknown_price",
        )
        pricing = rates.snapshot() if rates else None
        self.ledger.settle(operation, total, response.get("id"), usage, pricing)
        self.db.insert(
            Receipt(
                self.scope,
                time.time(),
                self.project,
                actual_model,
                self.mode,
                stage,
                response.get("id"),
                None,
                usage.get("input_tokens", 0),
                usage.get("output_tokens", 0),
                float(tool) if tool is not None else None,
                0.0,
                float(total) if total is not None else None,
                bool(rates and rates.verified_at),
                "Spotřeba API",
                {},
                usage,
                pricing_snapshot=pricing or {},
            )
        )
        validate_output(response, payload)
        return response


def show_final_receipt(parent, db, scope):
    operations = CostLedger(db.db_path).operations(scope)
    if not operations:
        return
    total = sum(
        (money(op["actual_usd"]) for op in operations if op["actual_usd"] is not None), Decimal(0)
    )
    pending = sum(op["status"] not in ("settled", "released") for op in operations)
    rows = []
    states = {
        "settled": "Vyčísleno",
        "released": "Neodesláno / odmítnuto",
        "pending": "Dávka čeká",
        "reserved": "Připraveno",
        "unknown": "Chybí podklady",
        "terminal_unknown": "Neúplný výsledek dávky",
    }
    for op in operations:
        estimate = json.loads(op["estimate_json"])
        rows.append(
            (
                "Zkouška"
                if estimate.get("stage", "").startswith("PREFLIGHT")
                else "Pracovní volání",
                ", ".join(sorted({item.get("model", "") for item in estimate.get("items", [])})),
                "BATCH" if estimate.get("batch") else "LIVE",
                states.get(op["status"], op["status"]),
                amount(op["actual_usd"])
                if op["status"] != "released"
                else "Neodesláno / odmítnuto",
            )
        )
    dialog = DetailDialog("Výsledná účtenka", "", parent, operations)
    html = "<h2>" + ("Vyčíslená část: " if pending else "Celková cena: ") + amount(total) + "</h2>"
    html += table(("Operace", "Model", "Režim", "Stav", "Cena"), rows)
    html += f"<p>Neuzavřené operace: {pending}. Neznámá cena není nula. Cena vychází ze skutečné spotřeby a uložených sazeb. Reasoning je již ve výstupních tokenech. Průběžné úložiště není zahrnuto. Nejde o fakturu OpenAI.</p>"
    for op in operations:
        fx = json.loads(op["estimate_json"]).get("fx")
        if fx:
            html += (
                "<p>Orientačně "
                + amount(total * money(fx["czk_per_usd"]), "CZK")
                + " · ČNB "
                + escape(fx["date"])
                + ".</p>"
            )
            break
    dialog.browser.setHtml(html)
    dialog.exec()
