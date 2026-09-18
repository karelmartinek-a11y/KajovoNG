"""Per-account cache dostupnosti modelů; schopnosti vždy pocházejí z pevné matice."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .model_registry import matrix_version, model_spec, model_usages
from .utils import atomic_write_text


SCHEMA_VERSION = 1
_SAFE_CATALOG_FIELDS = ("id", "object", "created", "owned_by")


def _account_scope(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else ""


def _safe_catalog_record(record: dict) -> dict:
    return {key: record[key] for key in _SAFE_CATALOG_FIELDS if key in record}


def _hydrate(record: dict) -> dict:
    model = str(record.get("id") or "").strip()
    if not model:
        raise ValueError("Katalog modelů obsahuje záznam bez id.")
    try:
        capabilities = model_spec(model)
        usages = model_usages(model)
    except ValueError:
        capabilities = None
        usages = []
    return {
        "id": model,
        "catalog": _safe_catalog_record(record),
        "matrix_version": matrix_version(),
        "capabilities": capabilities,
        "usages": usages,
    }


class ModelCatalogCache:
    """Ukládá bezpečná metadata GET /models odděleně pro účet bez uložení API klíče."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, api_key: str) -> dict:
        empty = {"schema_version": SCHEMA_VERSION, "fetched_at": 0.0, "models": {}}
        if not api_key or not self.path.is_file():
            return empty
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return empty
        if (
            not isinstance(raw, dict)
            or raw.get("schema_version") != SCHEMA_VERSION
            or raw.get("account_scope") != _account_scope(api_key)
            or not isinstance(raw.get("models"), dict)
        ):
            return empty
        models = {}
        for model, stored in raw["models"].items():
            catalog = stored.get("catalog") if isinstance(stored, dict) else None
            record = dict(catalog) if isinstance(catalog, dict) else {"id": model}
            record["id"] = model
            try:
                models[model] = _hydrate(record)
            except ValueError:
                continue
        fetched_at = raw.get("fetched_at")
        return {
            "schema_version": SCHEMA_VERSION,
            "fetched_at": float(fetched_at) if isinstance(fetched_at, (int, float)) else 0.0,
            "models": models,
        }

    def save(self, api_key: str, records: list[dict]) -> dict:
        if not api_key:
            raise ValueError("Katalog modelů nelze uložit bez identity účtu.")
        models = {}
        for record in records:
            if not isinstance(record, dict) or not str(record.get("id") or "").strip():
                continue
            hydrated = _hydrate(record)
            models[hydrated["id"]] = hydrated
        fetched_at = time.time()
        payload = {
            "schema_version": SCHEMA_VERSION,
            "account_scope": _account_scope(api_key),
            "fetched_at": fetched_at,
            "matrix_version": matrix_version(),
            "models": models,
        }
        atomic_write_text(str(self.path), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return {"schema_version": SCHEMA_VERSION, "fetched_at": fetched_at, "models": models}
