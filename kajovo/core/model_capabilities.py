"""Pohled UI na pevnou matici; schopnosti se nikdy nezjišťují placeným probe."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List

from .model_registry import model_spec, selectable, matrix_version


def split_text(text: str, max_chars: int) -> List[str]:
    if not text:
        return [""]
    if max_chars <= 0:
        return [text]
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        out.append(text[i : i + max_chars])
        i += max_chars
    return out


@dataclass
class ModelCapabilities:
    model: str
    tested_at: float
    ok_basic: bool
    supports_previous_response_id: bool
    supports_temperature: bool
    supports_tools: bool
    supports_file_search: bool
    supports_vector_store: bool = False
    supports_input_file: bool = False
    supports_image_input: bool = False
    vector_store_management: str = "runtime_check"
    supports_structured_outputs: bool = False
    notes: str = ""
    errors: dict | None = None

    def to_dict(self):
        return {**asdict(self), "errors": self.errors or {}}


class ModelCapabilitiesCache:
    """Kompatibilní čtecí rozhraní; historické cache už nejsou zdrojem pravidel."""
    def __init__(self, path):
        self.path = path

    def bind(self, api_key, base_url="https://api.openai.com/v1"):
        self.load()

    def load(self):
        return None

    def get(self, model):
        try:
            spec = model_spec(model)
        except ValueError:
            return None
        allowed = selectable(model)
        features = set(spec["features"])
        fs = allowed and "file_search" in features
        return ModelCapabilities(
            model=model,
            tested_at=0,
            ok_basic=allowed,
            supports_previous_response_id=allowed,
            supports_temperature=allowed and spec["sampling"] == "always",
            supports_tools=fs,
            supports_file_search=fs,
            supports_vector_store=fs,
            supports_input_file=allowed and "file_uploads" in features,
            supports_image_input=allowed and "image_input" in features,
            vector_store_management="runtime_check",
            supports_structured_outputs=allowed,
            notes=(
                f"Pevná matice {matrix_version()}; správu vector store a "
                "oprávnění účtu ověřuje runtime API."
            ),
        )

    def is_stale(self, model, ttl_hours):
        return False
