"""Výslovně spuštěné živé ověření strict kontraktů s provozní evidencí."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core.config import load_settings
from kajovo.core.contracts import file_response_format, structure_response_format
from kajovo.core.generate_batch import plan_format, structure_format
from kajovo.core.openai_client import OpenAIClient
from kajovo.core.structured_output import builtin_format, text_format, validate_output
from kajovo.core.utils import atomic_write_text


def sample(schema):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = kind[0]
    if kind == "object":
        return {key: sample(value) for key, value in schema["properties"].items()}
    if kind == "array":
        return []
    if kind in ("number", "integer"):
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return False
    if kind == "null":
        return None
    return "ověřeno"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Výslovně povolit placená ověření.")
    parser.add_argument("--model", default="gpt-5.2")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Živá kontrola vyžaduje --execute.")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        parser.error("Chybí OPENAI_API_KEY.")
    settings = load_settings()
    client = OpenAIClient(key, timeout_s=settings.response_timeout_s)
    client.configure_validation(settings)
    client.preflight_response({"model": args.model, "input": "kontrola", "text": text_format(), "previous_response_id": "resp_preflight"})
    formats = [text_format(), plan_format(), structure_format(), builtin_format("B1_PLAN"),
               structure_response_format("B2_STRUCTURE"), file_response_format("A3_FILE", "probe.txt", 0),
               file_response_format("B3_FILE", "probe.txt", 0, "modify"), builtin_format("C_FILES_ALL")]
    for fmt in formats:
        expected = sample(fmt["format"]["schema"])
        if "chunking" in expected:
            expected["chunking"].update(has_more=False, next_chunk_index=None, chunk_count=1)
        payload = {"model": args.model, "text": fmt, "max_output_tokens": 2048,
            "instructions": "Vrať přesně dodaný příklad podle vynuceného schématu.",
            "input": json.dumps(expected, ensure_ascii=False)}
        client._policy.check_documented(payload)
        response = client._policy.trial_live(payload)
        decoded = validate_output(response, payload)
        if decoded != expected:
            raise ValueError(f"{fmt['format']['name']}: obsah neodpovídá ověřovacímu zadání.")
        if decoded.get("contract") in ("A3_FILE", "B3_FILE"):
            target = Path(settings.cache_dir) / "verified_downloads" / response["id"] / "probe.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(str(target), decoded["content"])
            if target.read_text(encoding="utf-8") != decoded["content"]:
                raise ValueError("Stažený obsah neodpovídá odpovědi.")
        print(json.dumps({"contract": fmt["format"]["name"], "status": response["status"],
            "response_id": response["id"], "usage": response.get("usage")}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
