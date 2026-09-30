"""Sestaví lokální přezkoumatelný návrh bez změny historického běhu."""

import hashlib
import json
from pathlib import Path

from verify_recovery import write_once


ROOT = Path(__file__).resolve().parent
PARTS = ["source/SSOT.original.md", "TECHNICKY_DODATEK.md", "RESENI_KONKRETNI.md",
         "AUDIT_FORMAT.md", "KONFIGURACE.md", "OPERATION_MAPPING.md", "operation-map.expanded.json",
         "canonical-vectors.json", "audit-vectors.json"]


def main():
    parts = []
    sources = []
    for name in PARTS:
        data = (ROOT / name).read_bytes()
        text = data.decode("utf-8")
        assert not text.startswith("\ufeff")
        sources.append({"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        parts.append(f"\n\n<!-- SOURCE: {name}; SHA256: {sources[-1]['sha256']} -->\n\n" + text)
    header = (
        "# KájovoCML — konsolidovaný návrh obnovy LW0D\n\n"
        "STAV: REVIEW_ONLY — nejsou uzavřeny všechny kontrakty, není připraveno k Batch submit.\n\n"
        "Původní text následuje beze ztráty obsahu. Konkrétní opravy R01–R18 v přílohách mají "
        "na základě oprávnění OWNERa přednost pouze v uvedených ustanoveních, i před původním "
        "pravidlem zákazu řešení konfliktu. Ostatní produktové povinnosti zůstávají. "
        "DECIDED v resolution-status.json není důkaz implementace ani runtime acceptance.\n"
    )
    data = (header + "".join(parts)).encode("utf-8")
    destination = ROOT / "SSOT.recovery-review.md"
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError("Návrh již existuje s jiným obsahem; vytvořte explicitně novou revizi.")
    else:
        with destination.open("xb") as stream:
            stream.write(data)
    status = json.loads((ROOT / "resolution-status.json").read_text(encoding="utf-8"))
    manifest = {"status": "REVIEW_ONLY", "sources": sources,
                "bundle": destination.name, "bundle_sha256": hashlib.sha256(data).hexdigest(),
                "unresolved_findings": [item["id"] for item in status["findings"] if item["status"] != "DECIDED"],
                "submitted_batch_id": None}
    write_once(ROOT / "review-bundle.manifest.json", manifest)
    print(f"Lokální přezkumný návrh: {len(data)} bytes; neuzavřeno {manifest['unresolved_findings']}.")


if __name__ == "__main__":
    main()
