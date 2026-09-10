"""Výslovně spouštěné živé ověření API; vytváří placené požadavky."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core.contracts import extract_text_from_response, parse_json_strict
from kajovo.core.openai_client import OpenAIClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    args = parser.parse_args()
    if not args.live:
        return
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        env_file = Path(__file__).resolve().parents[1] / ".env.local"
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "OPENAI_API_KEY":
                key = value.strip().strip("\"'")
                break
    if not key:
        raise RuntimeError("Chybí OPENAI_API_KEY.")
    client = OpenAIClient(key)
    file_id = store_id = response_id = None

    def response(label, **extra):
        payload = {"model": "gpt-4.1-nano", "input": "Reply only OK.",
                   "max_output_tokens": 100, "store": False}
        payload.update(extra)
        result = client.create_response(payload)
        text = extract_text_from_response(result)
        if not text.strip():
            raise RuntimeError("Prázdný výsledek.")
        print(json.dumps({"check": label, "status": result.get("status"),
                          "model": result.get("model"), "usage": result.get("usage")}), flush=True)
        return result

    try:
        assert client.list_models()
        print('MODEL_LIST OK', flush=True)
        first = response("SDK_TEXT", store=True)
        response_id = first["id"]
        response("PREVIOUS_RESPONSE", previous_response_id=response_id)
        sdk = client._sdk
        client._sdk = None
        response("REST_TEXT", temperature=0)
        client._sdk = sdk
        structured = response("JSON_SCHEMA", input="Return ok=true.", text={"format": {
            "type": "json_schema", "name": "check", "strict": True,
            "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"], "additionalProperties": False}}})
        assert parse_json_strict(extract_text_from_response(structured)) == {"ok": True}
        with tempfile.TemporaryDirectory(prefix="kajovo-live-") as tmp:
            path = Path(tmp) / "check.txt"
            path.write_text("The verification code is KAJOVO-731.\n", encoding="utf-8")
            file_id = client.upload_file(str(path))["id"]
        assert client.retrieve_file(file_id)["id"] == file_id
        direct = response("INPUT_FILE", input=[{"role": "user", "content": [
            {"type": "input_text", "text": "What is the verification code in the file?"},
            {"type": "input_file", "file_id": file_id}]}])
        assert "KAJOVO-731" in extract_text_from_response(direct)
        store_id = client.create_vector_store("Kajovo verification", expires_after_days=1)["id"]
        client.add_file_to_vector_store(store_id, file_id, {"verification": True})
        for _ in range(30):
            status = client.retrieve_vector_store_file(store_id, file_id).get("status")
            if status == "completed":
                break
            if status in ("failed", "cancelled"):
                raise RuntimeError("Indexace selhala.")
            time.sleep(1)
        else:
            raise RuntimeError("Indexace nedokončena v časovém limitu.")
        updated = client.update_vector_store_file_attributes(store_id, file_id, {"verification": True, "revision": 2})
        print(json.dumps({"check": "UPDATE_ATTRIBUTES", "attributes": updated.get("attributes")}), flush=True)
        for _ in range(10):
            attributes = client.retrieve_vector_store_file(store_id, file_id).get("attributes", {})
            if attributes.get("revision") == 2:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Aktualizované atributy nejsou dostupné.")
        assert any(row["id"] == file_id for row in client.list_vector_store_files(store_id))
        found = response("FILE_SEARCH", input="Find the verification code in the attached store.",
                         tools=[{"type": "file_search", "vector_store_ids": [store_id]}],
                         tool_choice="required")
        assert "KAJOVO-731" in extract_text_from_response(found)
        print("LIVE_CHECKS_OK", flush=True)
    finally:
        failures = []
        for label, identifier, delete in (
            ("vector_store", store_id, client.delete_vector_store),
            ("file", file_id, client.delete_file),
            ("response", response_id, lambda rid: client._req("DELETE", f"/responses/{rid}")),
        ):
            if identifier:
                try:
                    delete(identifier)
                    print(f"CLEANUP {label} OK", flush=True)
                except Exception:
                    failures.append(f"{label}: {identifier}")
        client.session.close()
        if client._sdk is not None:
            client._sdk.close()
        if failures:
            raise RuntimeError("Nelze odstranit vlastní ověřovací prostředky: " + ", ".join(failures))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"LIVE_CHECK_FAILED {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
