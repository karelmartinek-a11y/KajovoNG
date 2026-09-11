"""Společné kontrakty na drátu a ověření dokončených odpovědí."""
from __future__ import annotations

import copy
import json
import re

import jsonschema

from .contracts import ContractError, _load_json, extract_text_from_response


class OutputContractError(ContractError):
    def __init__(self, message, response):
        super().__init__(message)
        self.response = response


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


def response_format(name, schema):
    validate_schema(schema)
    return {"format": {"type": "json_schema", "name": name, "strict": True, "schema": copy.deepcopy(schema)}}


def text_format():
    return response_format("TEXT_RESPONSE", obj({"text": {"type": "string"}}))


def validate_schema(schema):
    """Konzervativní podmnožina strict; nepodporované konstrukce neodesíláme."""
    jsonschema.Draft202012Validator.check_schema(schema)
    if not isinstance(schema, dict) or schema.get("type") != "object" or "anyOf" in schema:
        raise ValueError("Kořen strict schématu musí být objekt.")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "description",
               "title", "$defs", "$ref", "anyOf", "minimum", "maximum", "exclusiveMinimum",
               "exclusiveMaximum", "multipleOf", "pattern", "format", "minItems", "maxItems"}
    properties_count = 0

    def visit(node, depth=0):
        nonlocal properties_count
        if not isinstance(node, dict) or depth > 10:
            raise ValueError("Neplatná nebo příliš hluboká definice strict schématu.")
        unknown = set(node) - allowed
        if unknown:
            raise ValueError(f"Nepodporované prvky strict schématu: {', '.join(sorted(unknown))}.")
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not (ref == "#" or ref.startswith("#/$defs/")):
                raise ValueError("Schéma smí odkazovat jen na své lokální definice.")
            target = schema
            try:
                for part in ref.split("/")[1:]:
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError):
                raise ValueError("Schéma obsahuje neexistující lokální odkaz.") from None
        kind = node.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(k not in (None, "object", "array", "string", "number", "integer", "boolean", "null") for k in kinds):
            raise ValueError("Neznámý datový typ schématu.")
        if kind is None and not any(k in node for k in ("$ref", "anyOf")):
            raise ValueError("Schéma musí určit datový typ.")
        if "object" in kinds:
            props = node.get("properties", {})
            if node.get("additionalProperties") is not False or set(node.get("required", [])) != set(props):
                raise ValueError("Strict objekt vyžaduje všechny vlastnosti a additionalProperties=false.")
            properties_count += len(props)
            for child in props.values():
                visit(child, depth + 1)
        if "array" in kinds:
            visit(node.get("items"), depth + 1)
        for child in node.get("anyOf", []):
            visit(child, depth + 1)
        for child in node.get("$defs", {}).values():
            visit(child, depth + 1)
        if "format" in node and node["format"] not in ("date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"):
            raise ValueError("Nepodporovaný formát řetězce ve strict schématu.")
        if len(node.get("enum", [])) > 1000:
            raise ValueError("Příliš rozsáhlý výčet ve schématu.")

    visit(schema)
    if properties_count > 5000 or len(json.dumps(schema, ensure_ascii=False)) > 120000:
        raise ValueError("Strict schéma překračuje lokální bezpečný limit velikosti.")


def compile_schema(schema):
    """Uzavře známé objekty; neurčitý tvar musí doplnit přípravný krok."""
    result = copy.deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            raise ValueError("Schéma musí určit strukturu dat.")
        node.pop("$schema", None)
        node.pop("default", None)
        if "const" in node:
            node["enum"] = [node.pop("const")]
        kinds = node.get("type", [])
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if "object" in kinds or "properties" in node:
            node.setdefault("type", "object")
            if "properties" not in node:
                raise ValueError("Objekt vyžaduje konkrétní vlastnosti.")
            required = set(node.get("required", []))
            for key, value in node["properties"].items():
                visit(value)
                if key not in required:
                    node["properties"][key] = {"anyOf": [value, {"type": "null"}]}
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False
        if "items" in node:
            visit(node["items"])
        for child in node.get("anyOf", []):
            visit(child)
        for child in node.get("$defs", {}).values():
            visit(child)

    visit(result)
    validate_schema(result)
    return result


def prepare_payload(payload):
    """Doplní textový kontrakt; existující kontrakt nikdy potichu nenahradí."""
    payload.setdefault("text", text_format())
    if not isinstance(payload["text"], dict) or set(payload["text"]) != {"format"}:
        raise ValueError("Program vyžaduje jednoznačný text.format bez dalších voleb.")
    fmt = payload["text"].get("format", {}) if isinstance(payload["text"], dict) else {}
    if not isinstance(fmt, dict) or set(fmt) - {"type", "name", "strict", "schema", "description"}:
        raise ValueError("Neznámé nastavení výstupního formátu.")
    if fmt.get("type") != "json_schema" or fmt.get("strict") is not True:
        raise ValueError("Každá odpověď vyžaduje JSON Schema se strict=true.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(fmt.get("name", ""))):
        raise ValueError("Neplatný název výstupního kontraktu.")
    validate_schema(fmt.get("schema"))
    return payload


def validate_output(response, payload):
    try:
        return _validate_output(response, payload)
    except ContractError as exc:
        exc.response = response
        raise


def _validate_output(response, payload):
    if not isinstance(response, dict):
        raise ContractError("Odpověď API musí být objekt.")
    if response.get("status") != "completed":
        raise ContractError(f"Odpověď není dokončená: {response.get('status', 'chybí stav')}; {response.get('incomplete_details') or response.get('error') or ''}")
    raw = extract_text_from_response(response)
    try:
        parsed = _load_json(raw)
        jsonschema.Draft202012Validator(payload["text"]["format"]["schema"], format_checker=jsonschema.FormatChecker()).validate(parsed)
    except (ValueError, ContractError, jsonschema.ValidationError) as exc:
        raise OutputContractError(f"Odpověď porušuje výstupní kontrakt: {exc}", response) from exc
    return parsed


def user_text(response, payload):
    value = validate_output(response, payload)
    return value["text"] if payload["text"]["format"]["name"] == "TEXT_RESPONSE" else extract_text_from_response(response)


def builtin_format(contract):
    text = {"type": "string"}
    strings = array(text)
    if contract == "B1_PLAN":
        schema = obj({"contract": {"type": "string", "enum": [contract]},
            "diagnosis": obj({"summary": text, "evidence": array(obj({"path": text, "reason": text})), "likely_root_causes": strings}),
            "change_plan": obj({"goals": strings, "files_to_modify": array(obj({"path": text, "intent": text})),
                "files_to_add": array(obj({"path": text, "intent": text})), "verification_steps": strings}), "missing_inputs": strings})
    elif contract == "C_FILES_ALL":
        schema = obj({"contract": {"type": "string", "enum": [contract]},
            "project": obj({k: text for k in ("name", "target_os", "runtime", "language")}), "root": text,
            "files": array(obj({k: text for k in ("path", "purpose", "content")})),
            "build_run": obj({k: strings for k in ("prerequisites", "commands", "verification")}), "notes": strings})
    else:
        raise ValueError(f"Neznámý kontrakt: {contract}")
    return response_format(contract, schema)


def resolve_schema(client, model, instructions, original=None, context=None):
    """Příprava neurčitého kontraktu s omezeným počtem oprav."""
    if original:
        try:
            return compile_schema(original)
        except ValueError:
            pass
    error = ""
    for _attempt in range(3):
        payload = {"model": model, "text": response_format("SCHEMA_PREPARATION", obj({"schema_json": {"type": "string"}})),
            "instructions": "Navrhni přesné JSON Schema výstupu pracovního kroku. Vrať je jako JSON text v schema_json. "
                "Kořen object; všechny objekty mají properties, required všech polí a additionalProperties=false. "
                "Každé pole má konkrétní typ, pole items. Používej jen type, properties, required, additionalProperties, "
                "items, enum, description, anyOf a lokální $defs/$ref. Zachovej požadované názvy a návaznosti.",
            "input": json.dumps({"instructions": instructions, "original_schema": original, "downstream": context, "validation_error": error}, ensure_ascii=False)}
        response = client.create_response(payload)
        try:
            proposal = validate_output(response, payload)
            schema = _load_json(proposal["schema_json"])
            validate_schema(schema)
            return schema
        except (ValueError, ContractError, jsonschema.SchemaError) as exc:
            error = str(exc)
    raise ContractError(f"Program nedokázal připravit platný kontrakt po {_attempt + 1} pokusech: {error}")


def restore_optional_fields(value, original):
    """Převede nepřítomná volitelná pole z nullable drátového tvaru."""
    if isinstance(value, dict) and isinstance(original, dict):
        required = original.get("required", [])
        props = original.get("properties", {})
        result = {}
        for key, child in value.items():
            definition = props.get(key, {})
            if child is None and key not in required and not jsonschema.Draft202012Validator(definition).is_valid(None):
                continue
            result[key] = restore_optional_fields(child, definition)
        return result
    if isinstance(value, list):
        return [restore_optional_fields(v, original.get("items", {})) for v in value]
    return value
