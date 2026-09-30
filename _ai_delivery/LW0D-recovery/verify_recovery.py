"""Lokální integrity a byte-level kontrakty obnovy; žádná síť ani credentials."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import struct


ROOT = Path(__file__).resolve().parent


def audit_hash(version: int, previous: bytes, sequence: int, body: bytes) -> str:
    if version != 1 or len(previous) != 32 or not 1 <= sequence <= 2**63 - 1:
        raise ValueError("Neplatné parametry auditního hashe.")
    prefix = b"KCML-AUDIT\0" + struct.pack(">I32sQQ", version, previous, sequence, len(body))
    return hashlib.sha256(prefix + body).hexdigest()


def write_once(path: Path, value: object) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Existující artefakt se liší: {path.name}")
    else:
        with path.open("xb") as stream:
            stream.write(data)


def load(name: str) -> object:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def main() -> None:
    inventory = load("inventory.json")
    state = ROOT.parents[1] / "LOG" / inventory["source_run"] / "run_state.json"
    if hashlib.sha256(state.read_bytes()).hexdigest() != inventory["source_state_sha256"]:
        raise ValueError("Změnil se původní run_state.")
    req = load("source/A0.original.json")
    original = load("source/A1.original.json")
    normalized = load("A1.requirement-links.normalized.json")
    relocated = {r["architecture_id"]: r["source_references"] for r in load("A1.non-requirement-links.json")}
    known = {r["id"] for r in req["explicit_requirements"] + req["implicit_requirements"]}
    covered = set()
    assert len(original["architecture_items"]) == len(normalized["architecture_items"]) == 66
    for before, after in zip(original["architecture_items"], normalized["architecture_items"], strict=True):
        assert before["id"] == after["id"]
        assert before["responsibility"] == after["responsibility"]
        assert set(after["requirement_ids"]) <= known
        assert set(before["requirement_ids"]) == set(after["requirement_ids"]) | set(relocated.get(before["id"], []))
        covered.update(after["requirement_ids"])
    assert known == covered and len(known) == 200

    vectors = []
    for name, previous, sequence, body in [
        ("genesis-empty-object", bytes(32), 1, b"{}"),
        ("unicode-bytes", bytes(range(32)), 2, '{"x":"žluťoučký"}'.encode()),
        ("bigint-sequence", bytes.fromhex("ff" * 32), 2**63 - 1, b'{"sequence":"9223372036854775807"}'),
    ]:
        expected = audit_hash(1, previous, sequence, body)
        assert expected != audit_hash(1, previous, sequence, body + b"\n")
        vectors.append({"name": name, "previous_hash": previous.hex(), "sequence": str(sequence),
                        "canonical_hex": body.hex(), "expected_sha256": expected})
    for version, previous, sequence in [(2, bytes(32), 1), (1, bytes(31), 1), (1, bytes(32), 0), (1, bytes(32), 2**63)]:
        try:
            audit_hash(version, previous, sequence, b"{}")
        except ValueError:
            pass
        else:
            raise AssertionError("Audit přijal neplatný parametr.")
    write_once(ROOT / "audit-vectors.json", vectors)

    source = (ROOT / "source/SSOT.original.md").read_text(encoding="utf-8")
    api = source.split("## 26. API kontrakt", 1)[1].split("## 27.", 1)[0]
    routes = []
    section = "26"
    for line in api.splitlines():
        heading = re.match(r"^### (26\.[\d.]+) ", line)
        if heading:
            section = heading.group(1)
        match = re.match(r"^(GET|POST|PUT|PATCH|DELETE|WSS|WS)\s+(/\S+)", line)
        if match:
            routes.append({"method": match.group(1), "path": match.group(2), "source_section": section})
    assert len({(r["method"], r["path"]) for r in routes}) == len(routes)
    write_once(ROOT / "api-routes.inventory.json", routes)
    print(f"PASS: zdroj nezměněn; {len(known)} požadavků; 66 architektur; odkazy beze ztráty.")
    print(f"PASS: {len(vectors)} audit vectors; 4 invalidní hranice; {len(routes)} unikátních API cest.")
    print("NEOVĚŘENO: implementační struktura, úplnost operation/policy registrů a runtime chování.")


if __name__ == "__main__":
    main()
