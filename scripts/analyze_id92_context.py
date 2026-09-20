"""Offline ověření ID92 a měření kandidátních kontextů bez API a tajných údajů."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core.context_limits import measure  # noqa: E402
from kajovo.core.context_compiler import content_hash, dependency_groups, global_obligations  # noqa: E402


def percentile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = int(index)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (index - low)


def analyze(archive, manifest_path):
    manifest = json.loads(manifest_path.read_text("utf-8"))
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    if archive_hash != manifest["sha256"]:
        raise ValueError("Archiv neodpovídá SHA-256 manifestu.")
    with tempfile.TemporaryDirectory(prefix="kajovong_id92_analysis_") as directory:
        root = Path(directory)
        with tarfile.open(archive) as stream:
            # Nový prázdný adresář; žádné přepsání provozních dat.
            if any(not member.isfile() and not member.isdir() for member in stream.getmembers()):
                raise ValueError("Důkazní archiv nesmí obsahovat odkazy nebo speciální soubory.")
            stream.extractall(root, filter="data")
        observed = Counter((p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest())
                           for p in root.rglob("*") if p.is_file())
        expected = Counter((f["bytes"], f["sha256"]) for f in manifest["files"])
        if observed != expected:
            raise ValueError("Obsah archivu neodpovídá manifestu.")
        manual = root / "LOG/manual_ID92"

        def read(name):
            return json.loads((manual / name).read_text("utf-8"))

        snapshot = read("final_preparation_snapshot.json")
        measurements = {r["custom_id"]: r for r in read("batch_budget.json")["rows"]}
        counts = read("batch_token_counts.json")
        verified_requests = set()
        with (manual / "batch_requests.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                request = json.loads(line)
                cid = request["custom_id"]
                if cid in verified_requests or counts.get(content_hash(request["body"])) != measurements[cid]["input_tokens"]:
                    raise ValueError("Archivní token-count evidence nemá shodné hashové přiřazení requestu.")
                verified_requests.add(cid)
        if verified_requests != set(measurements):
            raise ValueError("Archivní token-count evidence a pracovní JSONL mají odlišná ID.")
        targets = read("batch_expected.json")
        if not isinstance(targets, dict):
            raise ValueError("Neznámý tvar archivního mapování.")
        files = {f["path"]: f for f in snapshot["structure"]["files"]}
        requirements = {r["id"]: r for key, values in snapshot["requirements"].items()
                        if key.endswith("requirements") for r in values}
        architecture = {a["id"]: a for a in snapshot["plan"]["architecture_items"]}
        interfaces = {i["id"]: i for i in snapshot["structure"]["interfaces"]}
        rows = []
        for cid, path in targets.items():
            file = files[path]
            symbols = set(file["provides"] + file["requires"])
            candidate = {
                "target_file": file,
                "relevant_requirements": [requirements[r] for r in file["requirement_ids"]],
                "architecture_contracts": [architecture[a] for a in file["architecture_item_ids"]],
                "dependency_contracts": [{"path": p, "provides": files[p]["provides"]}
                                         for p in file["dependencies"]],
                "shared_type_contracts": [interfaces[i] for i in sorted(symbols)],
                # Bez rozhodnutí působnosti nelze globální povinnosti odfiltrovat.
                "unscoped_global_obligations": global_obligations(snapshot),
            }
            size = measure(candidate)
            rows.append({"path": path, "custom_id": cid, "model": measurements[cid]["model"],
                         "legacy_input_tokens": measurements[cid]["input_tokens"],
                         "candidate_estimated_tokens": size["estimated_tokens"],
                         "candidate_utf8_bytes": size["utf8_bytes"],
                         "candidate_hash": content_hash(candidate),
                         "status": "blocked_missing_implementation_contract",
                         "blockers": ["Chybí přesné verzované symboly, error/lifecycle kontrakty a působnost globálních povinností.",
                                      "Kandidát není platný FileContextV1 a nesmí se odeslat do API."]})
        statuses, usage = {}, Counter()
        for name in ("gpt-5.4-mini_error_file_id.jsonl", "gpt-5.6-luna_output_file_id.jsonl"):
            counts = Counter()
            for line in (manual / name).read_text("utf-8").splitlines():
                row = json.loads(line)
                body = (row.get("response") or {}).get("body") or {}
                error = body.get("error") or row.get("error") or {}
                counts[error.get("code") or (body.get("incomplete_details") or {}).get("reason") or body.get("status", "unknown")] += 1
                u = body.get("usage") or {}
                usage.update({k: v for k, v in u.items() if type(v) is int})
                usage["reasoning_tokens"] += (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
            statuses[name] = dict(counts)
        sizes = [r["candidate_estimated_tokens"] for r in rows]
        legacy_total = sum(r["legacy_input_tokens"] for r in rows)
        candidate_total = sum(sizes)
        return {"version": 1, "archive_sha256": archive_hash, "verified_archive_files": sum(observed.values()),
                "prompt_characters": len((manual / "original_prompt.txt").read_text("utf-8")),
                "file_count": len(rows), "structure_file_count": len(files),
                "verified_request_token_counts": len(verified_requests),
                "excluded_local_file": "SSOT_CURRENT.md", "snapshot_hash": content_hash(snapshot),
                "responses": statuses, "actual_batch_usage": dict(usage),
                "legacy_total_input_tokens": legacy_total, "candidate_total_estimated_tokens": candidate_total,
                "candidate_reduction_percent": 100 * (1 - candidate_total / legacy_total),
                "verified_equivalent_savings_tokens": None,
                "blocked_files": len(rows), "safe_file_contexts": 0,
                "candidate_distribution": {"max": max(sizes), "mean": candidate_total / len(rows),
                    **{f"p{p}": percentile(sizes, p / 100) for p in (50, 90, 95, 99)},
                    **{f"over_{limit}": sum(n > limit for n in sizes) for limit in (100000, 150000, 200000)}},
                "measurement": "Legacy: archivní API input_tokens včetně přílohy. Kandidát: lokální odhad UTF-8 bajty / 3; bez API přílohy, instrukcí a schema. Nejde o ekvivalentní přesná měření.",
                "dependency_graph": dependency_groups(snapshot), "files": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("LOG/ID92_complete/ID92_complete.tar.xz"))
    parser.add_argument("--output", type=Path, default=Path("docs/ID92_CONTEXT_OPTIMIZATION_REPORT.json"))
    args = parser.parse_args()
    report = analyze(args.archive, args.archive.with_name("manifest.json"))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    dist = report["candidate_distribution"]
    text = f"""# Offline regrese kontextu ID92

Archiv SHA-256 `{report['archive_sha256']}`: ověřeno všech {report['verified_archive_files']} souborů.
Zadání má {report['prompt_characters']:,} znaků. Struktura má 300 souborů; přesný lokálně dodaný SSOT se negeneruje. Vyhodnoceno všech {report['file_count']} archivních souborových úloh.

**Bezpečně připravené FileContextV1: 0. Blokující nedostatečnost přípravy: {report['blocked_files']} souborů.**
Finální A2Q neobsahuje přesné verzované implementační kontrakty ani rozhodnutí působnosti globálních povinností. Jejich domyšlení by nebylo deterministické zachování kvality. Archiv se neopravuje a celý SSOT se nepoužívá jako fallback.

| Veličina | Hodnota |
|---|---:|
| Archivní součet input tokenů 299 úloh | {report['legacy_total_input_tokens']:,} |
| Součet lokálně odhadnutých kandidátů | {report['candidate_total_estimated_tokens']:,} |
| Orientační redukce reprezentace | {report['candidate_reduction_percent']:.2f} % |
| Největší kandidát | {dist['max']:,} |
| Průměr | {dist['mean']:.1f} |
| Medián | {dist['p50']:.1f} |
| p90 / p95 / p99 | {dist['p90']:.1f} / {dist['p95']:.1f} / {dist['p99']:.1f} |
| Nad 100k / 150k / 200k | {dist['over_100000']} / {dist['over_150000']} / {dist['over_200000']} |

{report['measurement']}

**Tato čísla nejsou prokázanou úsporou při stejné kvalitě.** Kandidáti obsahují všechny nezacílené globální povinnosti; žádná z nich nebyla kvůli velikosti odstraněna. Individuální blokace a měření jsou ve [strojovém reportu]({args.output.name}). Kvalitativně ekvivalentní úspora zůstává neprokázaná, dokud příprava nedodá chybějící kontrakty a implementace neprojde integrací.

Přímé počítání výsledků potvrzuje 228 chyb mini `context_length_exceeded`, 38 dokončených odpovědí Luny a 33 `incomplete/max_output_tokens`. Hotová Response ani Batch nedokládá integraci projektu.

Reprodukce: `.venv/Scripts/python.exe scripts/analyze_id92_context.py`. Skript používá nový dočasný adresář, ověřuje SHA-256 archivu i obsahů, neposílá API volání a nevkládá původní zadání ani odpovědi do dokumentace.
"""
    args.output.with_suffix(".md").write_text(text, encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("file_count", "blocked_files", "legacy_total_input_tokens", "candidate_total_estimated_tokens", "candidate_reduction_percent")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
