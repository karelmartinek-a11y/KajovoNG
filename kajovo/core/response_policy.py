"""Neplacená validační politika pro pracovní požadavky OpenAI.

Tato vrstva záměrně NEODESÍLÁ zkušební Responses ani zkušební BATCH dávky.
Provádí pouze lokální validaci kontraktu, kontrolu dostupnosti modelu v katalogu
účtu a neplacené ověření již existujících vzdálených prostředků, které pracovní
požadavek skutečně používá.
"""
from __future__ import annotations

import hashlib
import json

from .model_registry import matrix_version, model_spec
from .request_rules import validate_response_payload
from .structured_output import prepare_payload


class PreflightPending(RuntimeError):
    """Legacy typ pouze pro čtení starých záznamů; nový kód jej nikdy nevytváří."""

    def __init__(self, message, batch_id=None, status=None):
        super().__init__(message)
        self.batches = [{"id": batch_id, "status": status}] if batch_id else []


class ResponsePolicy:
    """Centrální neplacená validace před skutečným pracovním požadavkem.

    Historické atributy ``proofs``/``save``/``key`` zůstávají jen kvůli
    kompatibilitě se staršími logy a volajícími. Nová validace nevytváří žádné
    vzdálené ověřovací Responses, soubory ani BATCH dávky a neukládá důkazy o
    zkušebních voláních.
    """

    def __init__(self, client, cache_dir="cache", log_dir="LOG"):
        self.client = client
        identity = client.base_url.rstrip("/") + "\0" + client.api_key + "\0" + matrix_version()
        self.identity = hashlib.sha256(identity.encode()).hexdigest()
        self.catalog = None
        # Kompatibilita s openai_client.create_batch() a staršími testovacími
        # double objekty. Aktuální runtime sem žádné preflight důkazy nezapisuje.
        self.proofs = {}

    def save(self):
        """Kompatibilní no-op; placené ověřovací důkazy se již nepersistují."""
        return None

    def invalidate(self, model=None):
        """Zahodí pouze cache katalogu modelů; neexistuje cache placených testů."""
        self.catalog = None

    def model_spec(self, model):
        return model_spec(model)

    def key(self, payload, batch=False):
        """Stabilní klíč ponechaný pro kompatibilitu starších volajících."""
        return hashlib.sha256(
            json.dumps(
                {"payload": payload, "batch": batch, "matrix": matrix_version()},
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()

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
        """Neplacená kontrola jednoho požadavku.

        ``preparing=True`` se používá při skládání požadavku a proto nekontroluje
        vzdálené reference. Bez ``preparing`` se navíc přes GET ověří pouze
        prostředky, které má skutečný pracovní požadavek použít. Nikdy se zde
        neposílá POST /responses ani POST /batches.
        """
        self.check_documented(payload, batch=batch)
        if preparing:
            return
        self.client.validate_resources(payload)

    def consume_live(self, payload):
        """Kompatibilní no-op; neexistuje jednorázový preflight důkaz ke spotřebě."""
        return None

    def ensure_batch(self, payloads):
        """Neplaceně ověří všechny řádky skutečné pracovní BATCH dávky.

        Kontroluje schema, jednotnou podporovanou matici a existující reference.
        Nevytváří pomocný JSONL, nenahrává pomocný soubor a nevolá /batches.
        """
        if not isinstance(payloads, list) or not payloads:
            raise ValueError("BATCH musí obsahovat alespoň jeden požadavek.")
        for payload in payloads:
            self.check_documented(payload, batch=True)
            self.client.validate_resources(payload)
