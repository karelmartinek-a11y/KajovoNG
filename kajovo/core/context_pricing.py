"""Explicitně doložené základní USD sazby; neznámé modely se neodhadují."""

VERSION = "2026-09-13.1"
PRICES = {
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
