"""Deterministické čtení oficiálního ceníku a kurzu ČNB."""
from datetime import datetime, timezone
import re
import requests
from .cost_accounting import money

OPENAI_PRICING = "https://developers.openai.com/api/docs/pricing.md"
CNB_RATE = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"


def parse_prices(text, model_docs=None):
    from .pricing import PriceRow
    if "Prices per 1M tokens." not in text:
        raise ValueError("Zdroj nedokládá jednotku milion tokenů.")
    rows = {}
    for mode in ("Standard", "Batch"):
        match = re.search(r"### " + mode + r" pricing data\s+((?:\|[^\n]+\n)+)", text)
        if not match:
            raise ValueError(f"Chybí tabulka {mode}.")
        lines = match[1].splitlines()
        expected = ["Model", "Short context input", "Short context cached input", "Short context cache writes", "Short context output", "Long context input", "Long context cached input", "Long context cache writes", "Long context output"]
        if [v.strip() for v in lines[0].strip("|").split("|")] != expected:
            raise ValueError("Změnila se struktura ceníku.")
        for line in lines[2:]:
            values = [v.strip() for v in line.strip("|").split("|")]
            if len(values) != 9:
                raise ValueError("Neúplný cenový řádek.")
            label, *values = values
            model = label.split(" (")[0]
            prices = [None if v == "-" else money(v.removeprefix("$")) for v in values]
            inp, cached, write, out, long_in, long_cached, long_write, long_out = prices
            if inp is None or out is None:
                continue
            # Práh nesmíme dovodit ze samotné existence dlouhého kontextu.
            doc = (model_docs or {}).get(model, "")
            threshold = 272000 if "272K" in label or re.search(r"(?:more than |>\s*)272K input tokens", doc) else None
            if long_in is not None and threshold is None:
                continue
            im = long_in / inp if long_in is not None else money(1)
            om = long_out / out if long_out is not None else money(1)
            if any(a is not None and b is not None and b != a * im for a, b in ((cached, long_cached), (write, long_write))):
                continue
            kw = dict(source=OPENAI_PRICING, verified_at=datetime.now(timezone.utc).isoformat(),
                      context_threshold=threshold, long_input_multiplier=float(im), long_output_multiplier=float(om),
                      file_search_per_1k=2.5 if "| File search | Tool call | $2.50 / 1k calls |" in text else None,
                      storage_per_gb_day=.1 if "| File search | Storage | $0.10 / GB per day" in text else None)
            if mode == "Standard":
                maximum = re.search(r"([\d,]+) max output tokens", doc)
                kw["output_token_limit"] = int(maximum[1].replace(",", "")) if maximum else None
                rows[model] = PriceRow(model, str(inp / 1000), str(out / 1000),
                                       cached_input_per_1k=str(cached / 1000) if cached is not None else None,
                                       cache_write_per_1k=str(write / 1000) if write is not None else None, **kw)
            elif model in rows:
                row = rows[model]
                if (row.long_input_multiplier, row.long_output_multiplier) != (float(im), float(om)):
                    continue
                row.batch_input_per_1k, row.batch_output_per_1k = str(inp / 1000), str(out / 1000)
                row.batch_cached_input_per_1k = str(cached / 1000) if cached is not None else None
                row.batch_cache_write_per_1k = str(write / 1000) if write is not None else None
    if not rows:
        raise ValueError("Ceník neobsahuje podporované doložitelné sazby.")
    from dataclasses import replace
    for model, doc in (model_docs or {}).items():
        if model in rows:
            for snapshot in re.findall(r"`(" + re.escape(model) + r"-\d{4}-\d{2}-\d{2})`", doc):
                rows.setdefault(snapshot, replace(rows[model], model=snapshot))
    return rows


def fetch_fx():
    response = requests.get(CNB_RATE, timeout=15)
    response.raise_for_status()
    return parse_fx(response.text)


def parse_fx(text):
    lines = text.splitlines()
    date = datetime.strptime(lines[0].split()[0], "%d.%m.%Y").date().isoformat()
    for line in lines[2:]:
        parts = line.split("|")
        if len(parts) == 5 and parts[3] == "USD":
            amount = money(parts[2])
            if not amount:
                raise ValueError("Nulová jednotka kurzu ČNB.")
            return {"date": date, "czk_per_usd": str(money(parts[4].replace(",", ".")) / amount), "source": CNB_RATE}
    raise ValueError("ČNB nevrátila kurz USD.")
