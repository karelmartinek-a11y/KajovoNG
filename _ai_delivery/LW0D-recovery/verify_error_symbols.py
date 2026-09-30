"""Kontrola rozlišení chybových symbolů bez přepisování původního SSOT."""

import hashlib
import json
from pathlib import Path
import re

from verify_recovery import write_once


ROOT = Path(__file__).resolve().parent
SYMBOL = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
ERROR_LIKE = re.compile(
    "INVALID|CONFLICT|STALE|FAILED|EXCEEDED|UNKNOWN|REJECTED|UNSUPPORTED|REQUIRED|"
    "UNRESOLVED|EXPIRED|INCOMPLETE|ALREADY|MISSING|DENIED|NOT_FOUND|CANCELLED|"
    "UNAVAILABLE|EXHAUSTED|TIMEOUT|ERROR|BLOCKED|IN_PROGRESS|NOT_READY|VIOLATION"
)


def verify(source, definitions):
    catalog = source.split("## 32. Chybové stavy a obnova", 1)[1].split("## 33.", 1)[0]
    known = set(SYMBOL.findall(catalog))
    additions, aliases, others = (definitions[key] for key in ["canonical_additions", "aliases", "non_error_symbols"])
    if set(additions) & (set(aliases) | set(others)) or set(aliases) & set(others):
        raise ValueError("Symbol má více neslučitelných rolí.")
    if not set(aliases.values()) <= known | set(additions):
        raise ValueError("Alias nemá canonical target.")
    candidates = {name for name in SYMBOL.findall(source) if ERROR_LIKE.search(name)} - known
    unresolved = candidates - set(additions) - set(aliases) - set(others)
    if unresolved:
        raise ValueError("Nerozlišené error-like symboly: " + ", ".join(sorted(unresolved)))
    records = []
    for name in sorted(candidates):
        occurrences = [i for i, line in enumerate(source.splitlines(), 1) if name in SYMBOL.findall(line)]
        kind = "canonical_addition" if name in additions else "alias" if name in aliases else "non_error"
        records.append({"symbol": name, "kind": kind, "source_lines": occurrences,
                        "resolution": additions.get(name) or aliases.get(name) or others.get(name)})
    return records


def main():
    raw = (ROOT / "source/SSOT.original.md").read_bytes()
    definitions = json.loads((ROOT / "error-symbols.json").read_text(encoding="utf-8"))
    records = verify(raw.decode("utf-8"), definitions)
    write_once(ROOT / "error-symbols.verified.json", {
        "source_sha256": hashlib.sha256(raw).hexdigest(), "records": records,
        "scope": "Všechny error-like uppercase symboly celého původního SSOT mimo §32; nejde o test implementovaných error handlers."
    })
    print(f"PASS: {len(records)} error-like symbolů má explicitní roli; žádný unresolved alias.")


if __name__ == "__main__":
    main()
