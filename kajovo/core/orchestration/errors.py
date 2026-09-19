"""Typovaná chyba lokální validace s původním strojovým kódem."""
from __future__ import annotations


class OrchestrationError(ValueError):
    """Chyba pravidla nesmí být zaměněna za úspěšně ověřený výsledek."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")
