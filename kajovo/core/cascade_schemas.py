"""Explicitní předvolby a původní masky starších kaskád."""
from __future__ import annotations

import copy

import jsonschema

from .structured_output import compile_schema


PRESET_MANIFEST_SCHEMA = {
    "description": "Souborový manifest pro přímé uložení do OUT.",
    "type": "object",
    "required": ["files"],
    "additionalProperties": False,
    "properties": {
        "files": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["path", "content", "encoding"],
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "encoding": {"type": "string", "enum": ["utf-8", "base64"]},
                },
            },
        }
    },
}


PRESET_PROMPTS_SCHEMA = {
    "description": "Definice kaskády promptů.",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "version": {"type": "integer"},
        "name": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "model": {"type": "string"},
                    "instructions": {"type": "string"},
                    "input_text": {"type": "string"},
                },
                "required": ["title", "model", "instructions", "input_text"],
            },
        },
    },
    "required": ["version", "name", "steps"],
}


def legacy_schema_for_step(step):
    """Vrátí původní masku i strict tvar; neznámý JSON se připravuje zvlášť."""
    if step.output_type != "json":
        if step.expected_out_files:
            raise ValueError("Výstupní soubory vyžadují JSON manifest, nikoli textovou response.")
        return None, None
    if step.output_schema_kind == "manifest":
        original = copy.deepcopy(PRESET_MANIFEST_SCHEMA)
    elif step.output_schema_kind == "prompts":
        original = copy.deepcopy(PRESET_PROMPTS_SCHEMA)
    elif step.output_schema_kind == "custom" and step.output_schema_custom is not None:
        original = copy.deepcopy(step.output_schema_custom)
    elif step.expected_out_files:
        original = copy.deepcopy(PRESET_MANIFEST_SCHEMA)
    else:
        return None, None
    wire = compile_schema(original)
    if step.expected_out_files:
        _validate_manifest_handoff(original, step.expected_out_files)
    return original, wire


def _validate_manifest_handoff(schema, expected_paths):
    """Maska producenta musí mít typy, které souborový spotřebitel umí číst."""
    def branches(node):
        if "$ref" in node:
            target = schema
            for part in node["$ref"].split("/")[1:]:
                target = target[part.replace("~1", "/").replace("~0", "~")]
            yield from branches(target)
        elif "anyOf" in node:
            for child in node["anyOf"]:
                yield from branches(child)
        else:
            yield node

    files = schema["properties"].get("files", {})
    for collection in branches(files):
        if collection.get("type") != "array":
            raise ValueError("Souborový manifest vyžaduje files:array v každé response variantě.")
        for row in branches(collection["items"]):
            if row.get("type") != "object":
                raise ValueError("Položka souborového manifestu musí být objekt.")
            props = row["properties"]
            for field in ("path", "content"):
                if any(node.get("type") != "string" for node in branches(props.get(field, {}))):
                    raise ValueError(f"Souborový manifest vyžaduje {field}:string v každé response variantě.")
            for path in expected_paths:
                path_schema = {**props["path"], "$defs": schema.get("$defs", {})}
                if not jsonschema.Draft202012Validator(path_schema, format_checker=jsonschema.FormatChecker()).is_valid(path):
                    raise ValueError("Response maska nemůže předat předepsanou výstupní cestu.")
            if "encoding" in props:
                for encoding in branches(props["encoding"]):
                    options = encoding.get("enum")
                    if encoding.get("type") != "string" or not options or any(
                        not isinstance(value, str) or value.lower() not in {"utf-8", "base64"}
                        for value in options
                    ):
                        raise ValueError("Souborový manifest má nekompatibilní kódování.")
