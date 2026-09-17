"""Explicitně doložené základní USD sazby; neznámé modely se neodhadují."""

from typing import Any

VERSION = "2026-09-13.1"
PRICES: dict[str, dict[str, Any]] = {
    "gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02, "output": 1.20,
        "long_context_threshold": 272000, "long_input_multiplier": 2, "long_output_multiplier": 1.5,
        "source": "https://developers.openai.com/api/docs/models/gpt-5.6-luna"},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50,
        "long_context_threshold": None, "long_input_multiplier": 1, "long_output_multiplier": 1,
        "source": "https://developers.openai.com/api/docs/models/gpt-5.4-mini"},
}


def projected_cost(model, input_tokens, output_tokens, *, batch=False):
    price = PRICES.get(model)
    if price is None or output_tokens is None:
        return None
    long = price["long_context_threshold"] and input_tokens > price["long_context_threshold"]
    input_rate = price["input"] * (price["long_input_multiplier"] if long else 1)
    output_rate = price["output"] * (price["long_output_multiplier"] if long else 1)
    return {"usd": (input_tokens * input_rate + output_tokens * output_rate) / 1e6 * (0.5 if batch else 1),
            "pricing_version": VERSION, "source": price["source"],
            "assumptions": "Základní tokenová sazba, bez cache, nástrojů, úložiště a regionálních příplatků; výstup je rezervovaný strop.",
            "batch": batch}


def observed_image_cost(model, usage, *, batch=False):
    """Sazby Sunburst/Flare; neúplnou tokenovou skladbu nelze nacenit odhadem."""
    models = {"gpt-image-2.5-sunburst", "gpt-image-2.5-sunburst-2026-09-08",
              "gpt-image-2.5-flare", "gpt-image-2.5-flare-2026-09-08"}
    if model not in models or not isinstance(usage, dict):
        return None
    detail = usage.get("input_tokens_details") or {}
    values = [detail.get("text_tokens"), detail.get("image_tokens"), usage.get("output_tokens")]
    if any(type(value) is not int or value < 0 for value in values):
        return None
    cached = detail.get("cached_tokens", 0)
    if cached:
        # Bez členění cache na text a obraz by vznikla nesprávná cena.
        return None
    text, image, output = values
    return {"usd": (text * 5 + image * 8 + output * 30) / 1000000 * (.5 if batch else 1),
            "pricing_version": "image-2026-09-14", "batch": batch,
            "source": "https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst",
            "scope": "Obrazový požadavek; nezahrnuje textovou přípravu ani jiné služby."}
