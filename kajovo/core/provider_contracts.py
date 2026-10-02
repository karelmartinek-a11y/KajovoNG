"""Přesné nativní obálky management API odvozené z oficiálních SDK modelů.

Response obálka se složenými výstupy nástrojů zde zatím není pokryta.
Doménové Responses masky vlastní structured_output; vrstvy se nesměšují.
"""
from __future__ import annotations

import base64
import binascii
import copy
import math
import re
from functools import lru_cache

import jsonschema
from openai.types import Batch, FileObject, ImagesResponse, Model
from openai.types.responses.input_token_count_response import InputTokenCountResponse
from openai.types.vector_store import VectorStore
from openai.types.vector_stores import VectorStoreFile

MODELS = {
    "upload_file": FileObject, "retrieve_file": FileObject,
    "create_vector_store": VectorStore, "retrieve_vector_store": VectorStore,
    "attach_vector_store_file": VectorStoreFile, "retrieve_vector_store_file": VectorStoreFile,
    "update_vector_store_file": VectorStoreFile,
    "create_batch": Batch, "retrieve_batch": Batch, "cancel_batch": Batch,
    "create_image": ImagesResponse, "input_token_count": InputTokenCountResponse,
}
LISTS = {
    "list_models": Model, "list_files": FileObject,
    "list_vector_stores": VectorStore, "list_vector_store_files": VectorStoreFile,
    "list_batches": Batch,
}
DELETES = {"delete_file": "file.deleted", "delete_vector_store": "vector_store.deleted",
           "delete_vector_store_file": "vector_store.file.deleted"}
RESOURCE_ID = {"type": "string", "pattern": r"^[A-Za-z0-9_-]+$"}


def validate_native_schema(schema):
    """Kontrola všech definic včetně nepoužitých a rozřešení každého odkazu."""
    jsonschema.Draft202012Validator.check_schema(schema)

    def resolve(ref):
        if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
            raise ValueError("Nativní kontrakt vyžaduje lokální odkaz.")
        target = schema
        try:
            for part in ref.split("/")[1:]:
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise ValueError("Nativní kontrakt má nerozřešený odkaz.") from None
        if not isinstance(target, dict) or not target:
            raise ValueError("Nativní odkaz vede na neurčitou masku.")
        return target

    def concrete(node, ancestors=()):
        if not isinstance(node, dict) or not node:
            raise ValueError("Nativní kontrakt obsahuje obecnou masku.")
        if not any(key in node for key in ("type", "$ref", "anyOf")):
            raise ValueError("Nativní odkaz nevede na explicitní datový typ.")
        if id(node) in ancestors:
            raise ValueError("Nativní odkazy tvoří cyklus bez konkrétního typu.")
        ancestors = (*ancestors, id(node))
        if "$ref" in node:
            concrete(resolve(node["$ref"]), ancestors)
        for child in node.get("anyOf", []):
            concrete(child, ancestors)

    def visit(node, field=""):
        if not isinstance(node, dict) or not node:
            raise ValueError("Nativní kontrakt obsahuje obecnou masku.")
        supported = {"$defs", "$ref", "type", "properties", "required", "additionalProperties",
                     "items", "anyOf", "const", "enum", "default", "description", "title",
                     "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                     "pattern", "format", "minLength", "maxLength", "minItems", "maxItems",
                     "propertyNames", "minProperties", "maxProperties"}
        if set(node) - supported:
            raise ValueError("Nativní kontrakt obsahuje neauditovanou kompozici.")
        if not any(key in node for key in ("type", "$ref", "anyOf")):
            raise ValueError("Nativní kontrakt neurčuje datový typ.")
        if "type" in node and (not isinstance(node["type"], str) or node["type"] not in {"object", "array", "string", "integer", "number", "boolean", "null"}):
            raise ValueError("Nativní kontrakt používá neauditovaný typ.")
        concrete(node)
        if "$ref" in node:
            resolve(node["$ref"])
        if node.get("type") == "object":
            if "properties" in node:
                if node.get("additionalProperties") is not False:
                    raise ValueError("Pevný nativní objekt musí být uzavřený.")
                for key, child in node["properties"].items():
                    visit(child, key)
            else:
                if (field not in {"metadata", "attributes"}
                        or node.get("propertyNames") != {"type": "string", "maxLength": 64}
                        or node.get("maxProperties") != 16):
                    raise ValueError("Dynamické klíče nemají přesně omezený význam.")
                visit(node.get("additionalProperties"), field)
        if node.get("type") == "array":
            visit(node.get("items"), field)
        for child in node.get("anyOf", []):
            visit(child, field)
        for child in node.get("$defs", {}).values():
            visit(child)

    visit(schema)
    return schema


def _close_sdk_schema(schema, *, model_ids=False):
    """SDK třída určuje pevná pole. Pouze metadata/attributes jsou nativní mapy."""
    schema = copy.deepcopy(schema)

    def visit(node, field=""):
        if not isinstance(node, dict) or not node:
            raise ValueError("Nativní model obsahuje neurčitou masku.")
        if node.get("type") == "object":
            if "properties" in node:
                node["additionalProperties"] = False
                for key, child in node["properties"].items():
                    visit(child, key)
            else:
                value = node.get("additionalProperties")
                if field not in {"metadata", "attributes"} or not isinstance(value, dict):
                    raise ValueError("Nepopsaná dynamická mapa v nativním modelu.")
                # Provider ukládá uživatelské metadata a vyhledávací atributy.
                # Klíče lze při čtení cizích prostředků znát až z odpovědi.
                node["propertyNames"] = {"type": "string", "maxLength": 64}
                node["maxProperties"] = 16
                visit(value, field)
        if node.get("type") == "array":
            visit(node.get("items"), field)
        if node.get("type") == "string" and field in {"metadata", "attributes"}:
            node["maxLength"] = 512
        if node.get("type") in {"integer", "number"} and field in {
            "bytes", "usage_bytes", "created_at", "created", "total", "completed", "failed",
            "cancelled", "in_progress", "input_tokens", "output_tokens", "total_tokens",
        }:
            node["minimum"] = 0
        if field in {"id", "vector_store_id", "input_file_id", "output_file_id", "error_file_id"} and node.get("type") == "string" and not (model_ids and field == "id"):
            node.update(RESOURCE_ID)
        if model_ids and field == "id" and node.get("type") == "string":
            node["pattern"] = r"^\S+$"
        for child in node.get("anyOf", []):
            visit(child, field)
        for child in node.get("$defs", {}).values():
            visit(child)
        if not any(key in node for key in ("type", "$ref", "anyOf", "const", "enum")):
            raise ValueError("Nativní model neurčuje datový typ.")

    visit(schema)
    validate_native_schema(schema)
    return schema


@lru_cache(maxsize=None)
def _contract(operation):
    if operation in MODELS:
        return _close_sdk_schema(MODELS[operation].model_json_schema())
    if operation in LISTS:
        item = _close_sdk_schema(LISTS[operation].model_json_schema(), model_ids=operation == "list_models")
        definitions = item.pop("$defs", {})
        fields = {"object": {"type": "string", "const": "list"},
                  "data": {"type": "array", "items": item}}
        required = ["object", "data"]
        if operation != "list_models":
            fields.update(has_more={"type": "boolean"},
                          first_id={"anyOf": [RESOURCE_ID, {"type": "null"}]},
                          last_id={"anyOf": [RESOURCE_ID, {"type": "null"}]})
            required.append("has_more")
        return {"type": "object", "properties": fields, "required": required,
                "additionalProperties": False, "$defs": definitions}
    if operation in DELETES:
        return {"type": "object", "properties": {
            "id": RESOURCE_ID, "object": {"type": "string", "const": DELETES[operation]},
            "deleted": {"type": "boolean", "const": True},
        }, "required": ["id", "object", "deleted"], "additionalProperties": False}
    return None


def _attribute_contract(attributes):
    if not isinstance(attributes, dict) or len(attributes) > 16:
        raise ValueError("Atributy požadavku nemají přesnou provider mapu.")
    properties = {}
    for key, value in attributes.items():
        if not isinstance(key, str) or len(key) > 64:
            raise ValueError("Nepřípustný klíč atributu požadavku.")
        if type(value) is bool:
            kind = "boolean"
        elif type(value) is int:
            kind = "integer"
        elif type(value) is float and math.isfinite(value):
            kind = "number"
        elif isinstance(value, str) and len(value) <= 512:
            kind = "string"
        else:
            raise ValueError("Nepřípustná hodnota atributu požadavku.")
        properties[key] = {"type": kind, "const": value}
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def native_contract(operation, *, request=None, path=""):
    """Volající nedostává sdílenou mutable kopii masky."""
    schema = copy.deepcopy(_contract(operation))
    if schema is not None:
        clean = path.split("?", 1)[0]
        match = re.fullmatch(r"/(files|batches|vector_stores)/([A-Za-z0-9_-]+)(?:/(files)(?:/([A-Za-z0-9_-]+))?|/cancel)?", clean)
        if match and operation not in LISTS:
            identifier = match[4] if match[3] else match[2]
            if identifier is not None:
                schema["properties"]["id"]["const"] = identifier
        if match and match[3] and operation not in DELETES:
            target = schema["properties"]["data"]["items"] if operation in LISTS else schema
            target["properties"]["vector_store_id"]["const"] = match[2]
        if request is not None:
            if "metadata" in request and "metadata" in schema["properties"]:
                metadata = request["metadata"]
                if not isinstance(metadata, dict) or any(not isinstance(value, str) for value in metadata.values()):
                    raise ValueError("Metadata požadavku vyžadují textové hodnoty.")
                schema["properties"]["metadata"] = _attribute_contract(metadata)
                if "metadata" not in schema["required"]:
                    schema["required"].append("metadata")
            bound = {
                "create_batch": ("input_file_id", "endpoint", "completion_window"),
                "upload_file": ("purpose",), "create_vector_store": ("name",),
            }.get(operation, ())
            for field in bound:
                if field not in request:
                    raise ValueError(f"Požadavek postrádá {field} pro přesný response kontrakt.")
                if not jsonschema.Draft202012Validator(schema["properties"][field]).is_valid(request[field]):
                    raise ValueError(f"Požadavek má neplatný typ nebo hodnotu {field}.")
                schema["properties"][field]["const"] = request[field]
                if field not in schema["required"]:
                    schema["required"].append(field)
            if operation == "attach_vector_store_file":
                schema["properties"]["id"]["const"] = request["file_id"]
            if operation in {"attach_vector_store_file", "update_vector_store_file"} and "attributes" in request:
                schema["properties"]["attributes"] = _attribute_contract(request["attributes"])
                if "attributes" not in schema["required"]:
                    schema["required"].append("attributes")
            if operation == "create_image":
                count = request.get("n", 1)
                if type(count) is not int or count != 1:
                    raise ValueError("Program vyžaduje jediný obrazový výsledek.")
                schema["properties"]["data"].update(minItems=count, maxItems=count)
        validate_native_schema(schema)
    return schema


def validate_native_response(operation, value, *, path="", request=None):
    schema = native_contract(operation, request=request, path=path)
    if schema is None:
        raise ValueError(f"Operace {operation} nemá zde definovaný nativní kontrakt.")
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(value)
    clean = path.split("?", 1)[0]
    match = re.fullmatch(r"/(files|batches|vector_stores)/([A-Za-z0-9_-]+)(?:/(files)(?:/([A-Za-z0-9_-]+))?|/cancel)?", clean)
    if match and operation not in LISTS:
        identifier = match[4] if match[3] else match[2]
        if identifier is not None and "id" in value and value["id"] != identifier:
            raise ValueError("Provider vrátil jinou identitu prostředku.")
    if match and match[3] and operation not in DELETES:
        rows = value["data"] if operation in LISTS else [value]
        if any(row["vector_store_id"] != match[2] for row in rows):
            raise ValueError("Provider vrátil soubor jiného úložiště.")
    if operation == "attach_vector_store_file" and value["id"] != request["file_id"]:
        raise ValueError("Provider připojil jiný soubor.")
    if operation == "update_vector_store_file" and value.get("attributes") != request["attributes"]:
        raise ValueError("Provider nepotvrdil požadované atributy.")
    if operation == "create_batch" and any(value[key] != request[key] for key in ("input_file_id", "endpoint", "completion_window")):
        raise ValueError("Provider vytvořil dávku s jinou vstupní identitou.")
    if operation == "upload_file" and value["purpose"] != request["purpose"]:
        raise ValueError("Provider nepotvrdil účel souboru.")
    rows = value["data"] if operation in LISTS else [value]
    if operation in LISTS:
        identifiers = [row["id"] for row in rows]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Stránka obsahuje duplicitní identitu prostředku.")
        if value.get("has_more") and not rows:
            raise ValueError("Prázdná stránka nemůže mít pokračování.")
        for marker, expected in (("first_id", identifiers[0] if identifiers else None),
                                 ("last_id", identifiers[-1] if identifiers else None)):
            if value.get(marker) is not None and value[marker] != expected:
                raise ValueError("Cursor neodpovídá pořadí prostředků stránky.")
    if operation in {"create_vector_store", "retrieve_vector_store", "list_vector_stores"}:
        for row in rows:
            counts = row["file_counts"]
            if sum(counts[key] for key in ("completed", "failed", "in_progress", "cancelled")) != counts["total"]:
                raise ValueError("Počty souborů úložiště nemají konzistentní součet.")
    if operation in {"create_batch", "retrieve_batch", "cancel_batch", "list_batches"}:
        for row in rows:
            if row.get("request_counts") is not None:
                counts = row["request_counts"]
                if counts["completed"] + counts["failed"] > counts["total"]:
                    raise ValueError("Počty výsledků dávky překračují celkový počet.")
    if operation == "create_image":
        if not isinstance(value.get("data"), list) or len(value["data"]) != request.get("n", 1):
            raise ValueError("Počet obrazů neodpovídá pracovnímu požadavku.")
        if any(not isinstance(row.get("b64_json"), str) or not row["b64_json"] for row in value["data"]):
            raise ValueError("Obrazová odpověď neobsahuje požadované base64 obrazy.")
        try:
            for row in value["data"]:
                base64.b64decode(row["b64_json"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Obrazová odpověď obsahuje neplatné base64.") from exc
    return value
