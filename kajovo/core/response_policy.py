"""Neplacená validační politika pro pracovní požadavky OpenAI.

Tato vrstva záměrně NEODESÍLÁ zkušební Responses ani zkušební BATCH dávky.
Provádí pouze lokální validaci kontraktu, kontrolu dostupnosti modelu v katalogu
účtu a ne-generativní ověření již existujících vzdálených prostředků, které
pracovní požadavek skutečně používá.
"""
from __future__ import annotations

from .model_registry import model_spec
from .request_rules import validate_response_payload
from .structured_output import prepare_payload


class ResponsePolicy:
    """Centrální neplacená validace před skutečným pracovním požadavkem."""

    def __init__(self, client, cache_dir="cache", log_dir="LOG"):
        self.client = client
        self.catalog = None

    def invalidate(self, model=None):
        """Zahodí pouze cache katalogu modelů."""
        self.catalog = None

    def model_spec(self, model):
        return model_spec(model)

    def check_documented(self, payload, batch=False):
        """Ověří lokální kontrakt a dostupnost modelu bez generativního volání."""
        prepare_payload(payload)
        validate_response_payload(payload, batch=batch)
        if self.catalog is None:
            self.catalog = {row["id"] for row in self.client.list_models()}
        if payload["model"] not in self.catalog:
            raise ValueError(f"{payload['model']}: model není v aktuálním katalogu účtu.")
        spec = self.model_spec(payload["model"])
        if (
            spec["canonical"] == "gpt-6-astra"
            and "eu.api.openai.com" in self.client.base_url
            and payload.get("service_tier") == "priority"
        ):
            raise ValueError(
                "gpt-6-astra: service_tier=priority není podporován s EU data residency."
            )
        return spec

    def ensure(self, payload, batch=False, preparing=False):
        """Neplacená kontrola jednoho skutečného pracovního požadavku.

        ``preparing=True`` se používá při skládání payloadu a proto nekontroluje
        vzdálené reference. Bez něj se navíc přes GET ověří pouze prostředky,
        které má pracovní požadavek skutečně použít. Tato metoda nikdy nevytváří
        generativní Response ani BATCH.
        """
        self.check_documented(payload, batch=batch)
        if preparing:
            return
        self.client.validate_resources(payload)

    def ensure_batch(self, payloads):
        """Neplaceně ověří všechny řádky skutečné pracovní BATCH dávky."""
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("BATCH musí obsahovat alespoň jeden požadavek.")
        for payload in payloads:
            self.check_documented(payload, batch=True)
            self.client.validate_resources(payload)
