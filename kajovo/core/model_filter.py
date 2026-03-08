from __future__ import annotations

from typing import Any, List


def filter_models_for_generate(models: List[str], caps_cache: Any) -> List[str]:
    """Return models allowed for overrides in GENERATE; filters explicit prev_id rejection."""
    out = []
    for mid in models:
        caps = caps_cache.get(mid) if hasattr(caps_cache, "get") else None
        if caps and hasattr(caps, "supports_previous_response_id") and caps.supports_previous_response_id is False:
            continue
        out.append(mid)
    return out
