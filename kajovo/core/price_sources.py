"""Deterministické čtení oficiálního ceníku a kurzu ČNB."""
from datetime import datetime, timezone
import re
import requests
from .cost_accounting import money

OPENAI_PRICING = "https://developers.openai.com/api/docs/pricing.md"
CNB_RATE = "https://www.cnb.cz/cs/financni-trhy/devizovy-trh/kurzy-devizoveho-trhu/kurzy-devizoveho-trhu/denni_kurz.txt"


def parse_prices(text, model_docs=None, reference_rows=None):
    """Jednotky i každou sazbu přebírá explicitně, včetně zaokrouhlené Batch cache."""
    from .pricing import PriceRow
    from .model_registry import model_spec
    if "Prices per 1M tokens." not in text:
        raise ValueError("Zdroj nedokládá jednotku milion tokenů.")
    rows = {}
    verified = datetime.now(timezone.utc).isoformat()
    expected = ["Model", "Short context input", "Short context cached input", "Short context cache writes", "Short context output", "Long context input", "Long context cached input", "Long context cache writes", "Long context output"]
    keys = ("input", "cached", "write", "output", "long_input", "long_cached", "long_write", "long_output")
    def meta(model, doc, label=""):
        reference = (reference_rows or {}).get(model)
        threshold = 272000 if "272K" in label or re.search(r"(?:more than |>\s*)272K input tokens", doc) else None
        if threshold is None and reference is not None:
            threshold = reference.context_threshold
        maximum = re.search(r"([\d,]+) max output tokens", doc)
        # Datum vydání je převzato z doloženého snapshotu; obecné jméno není datum.
        try:
            spec = model_spec(model)
            maximum_value = spec["max_output_tokens"]
        except ValueError:
            maximum_value = None
        dates = re.findall(re.escape(model) + r"-(\d{4}-\d{2}-\d{2})", doc)
        return dict(source=OPENAI_PRICING, verified_at=verified, context_threshold=threshold,
            output_token_limit=int(maximum[1].replace(",", "")) if maximum else maximum_value,
            regional_uplift=(True if "10% uplift" in doc else min(dates) >= "2026-03-05" if dates else reference.regional_uplift if reference else None),
            file_search_per_1k="2.5" if "| File search | Tool call | $2.50 / 1k calls |" in text else None,
            storage_per_gb_day="0.1" if "| File search | Storage | $0.10 / GB per day" in text else None)
    for title, mode in (("Standard", "default"), ("Batch", "batch"), ("Flex", "flex"), ("Fast", "priority")):
        match = re.search(r"### " + title + r" pricing data\s+((?:\|[^\n]+\n)+)", text)
        if not match:
            if title in ("Standard", "Batch"):
                raise ValueError(f"Chybí tabulka {title}.")
            continue
        lines = match[1].splitlines()
        if [v.strip() for v in lines[0].strip("|").split("|")] != expected:
            raise ValueError("Změnila se struktura ceníku.")
        seen = set()
        for line in lines[2:]:
            cells = [v.strip() for v in line.strip("|").split("|")]
            if len(cells) != 9:
                raise ValueError("Neúplný cenový řádek.")
            label, *values = cells
            model = label.split(" (")[0]
            if model in seen:
                raise ValueError(f"Duplicitní sazba {model} / {title}.")
            seen.add(model)
            values = [None if v == "-" else str(money(v.removeprefix("$"))) for v in values]
            if values[0] is None or values[3] is None:
                continue
            doc = (model_docs or {}).get(model, "")
            kw = meta(model, doc, label)
            if any(v is not None for v in values[4:]) and kw["context_threshold"] is None:
                raise ValueError(f"Chybí doložený práh dlouhého kontextu: {model}.")
            rates = dict(zip(keys, values, strict=True), explicit_long_rates=True)
            if mode == "default":
                rows[model] = PriceRow(model, money(values[0])/1000, money(values[3])/1000,
                    cached_input_per_1k=money(values[1])/1000 if values[1] is not None else None,
                    cache_write_per_1k=money(values[2])/1000 if values[2] is not None else None,
                    mode_rates={mode: rates}, **kw)
            elif model in rows:
                row = rows[model]
                row.mode_rates[mode] = rates
                if mode == "batch":
                    row.batch_input_per_1k, row.batch_output_per_1k = money(values[0])/1000, money(values[3])/1000
                    row.batch_cached_input_per_1k = money(values[1])/1000 if values[1] is not None else None
                    row.batch_cache_write_per_1k = money(values[2])/1000 if values[2] is not None else None
    # Samostatná cenová sekce Codex/ChatGPT není součástí hlavní modelové tabulky.
    specialized = text.split("Specialized models", 1)[-1].split("Finetuning", 1)[0] if "Specialized models" in text else ""
    for index, block in enumerate(specialized.split("Fast mode")):
        mode = "default" if index == 0 else "priority"
        for _category, model, inp, cached, out in re.findall(
            r"^\| ([^|]+) \| ([A-Za-z0-9_.-]+) \| \$([\d.]+) \| (\$[\d.]+|-) \| \$([\d.]+) \|", block, re.M
        ):
            values = {"input": inp, "output": out, "cached": cached.removeprefix("$") if cached != "-" else None}
            if mode == "default":
                rows[model] = PriceRow(model, money(inp)/1000, money(out)/1000,
                    cached_input_per_1k=money(values["cached"])/1000 if values["cached"] is not None else None,
                    mode_rates={mode: values}, **meta(model, (model_docs or {}).get(model, "")))
            elif model in rows:
                rows[model].mode_rates[mode] = values
    # Modelové stránky obsahují i Codex varianty neuvedené v hlavní tabulce.
    for model, doc in (model_docs or {}).items():
        if model in rows:
            continue
        metrics = dict(re.findall(r"^\| (Input|Cached input|Output) \| \$([\d.]+) \| 1M tokens \|", doc, re.M))
        if "Input" not in metrics or "Output" not in metrics:
            continue
        kw = meta(model, doc)
        kw["source"] = "https://developers.openai.com/api/docs/models/" + model
        rows[model] = PriceRow(model, money(metrics["Input"])/1000, money(metrics["Output"])/1000,
            cached_input_per_1k=money(metrics["Cached input"])/1000 if "Cached input" in metrics else None, **kw)
    if not rows:
        raise ValueError("Ceník neobsahuje podporované doložitelné sazby.")
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
