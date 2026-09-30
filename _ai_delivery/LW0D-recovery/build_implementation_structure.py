"""Offline kompilátor návrhu A2; nikdy nezapisuje výstup ani nevolá síť.

Návrh není schválení: úplnost podkladů, působností a důkazů se ověřuje
odděleně od schématu. Finální JSON smí opustit CLI pouze po všech kontrolách.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
REPOSITORY = ROOT.parents[1]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

SOURCE_FILES = ("source/A0.original.json", "A1.requirement-links.normalized.json",
                "source/SSOT.original.md")
FACETS = {"types", "serialization", "storage", "api", "events", "errors", "retry",
          "timeouts", "idempotency", "transactions", "concurrency", "locking",
          "persistence", "auth", "security", "lifecycle", "invariants", "framework", "versions"}


class BuildError(ValueError):
    """Neplatné podklady se nesmějí opravovat odhadem."""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def strict_json(data: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise BuildError(f"Duplicitní JSON klíč: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise BuildError(f"Neplatná JSON konstanta: {value}")

    if data.startswith(b"\xef\xbb\xbf"):
        raise BuildError("JSON nesmí obsahovat BOM.")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"Neplatný UTF-8 JSON: {exc}") from exc


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise BuildError(f"Neplatná POSIX cesta: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"", ".", ".."} for p in value.split("/")):
        raise BuildError(f"Únik nebo nekanonická cesta: {value}")
    return value


def local_bytes(root: Path, name: str) -> bytes:
    relative_path(name)
    base = root.resolve()
    target = (base / name).resolve()
    if not target.is_relative_to(base):
        raise BuildError(f"Symlink/junction opouští pracovní adresář: {name}")
    return target.read_bytes()


def pointer(document: Any, reference: str) -> Any:
    if reference == "":
        return document
    if not reference.startswith("/"):
        raise BuildError(f"Neplatný JSON pointer: {reference}")
    current = document
    try:
        for part in reference[1:].split("/"):
            if re.search(r"~(?![01])", part):
                raise BuildError(f"Neplatný JSON pointer: {reference}")
            key = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise BuildError(f"Neplatný index: {reference}")
                current = current[int(key)]
            else:
                current = current[key]
        return current
    except (KeyError, IndexError, TypeError) as exc:
        raise BuildError(f"Chybí JSON pointer: {reference}") from exc


@dataclass(frozen=True)
class Section:
    id: str
    heading: str
    start: int
    end: int


class SourceIndex:
    """Přesné disjunktní úseky UTF-8; nadpisy v code fence nejsou kapitoly.

    Offsety slouží pouze lokálnímu dohledání výňatku, nikoli KCML-REQ identitě.
    Nečíslované podnadpisy patří do poslední číslované sekce.
    """

    def __init__(self, data: bytes):
        self.data = data
        self.digest = sha(data)
        data.decode("utf-8", errors="strict")
        headings = []
        offset = 0
        fence = None
        for raw in data.splitlines(keepends=True):
            line = raw.decode("utf-8").rstrip("\r\n")
            fence_match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if fence_match:
                marker, tail = fence_match.groups()
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence) and not tail.strip():
                    fence = None
            elif fence is None:
                match = re.match(r"^#{1,6} +(\d+(?:\.\d+)*)(?:\. +| +)(.+)$", line)
                if match:
                    headings.append((match[1], line, offset))
            offset += len(raw)
        if fence is not None:
            raise BuildError("Neuzavřený Markdown code fence ve zdroji.")
        if not headings:
            raise BuildError("SSOT neobsahuje číslované sekce.")
        self.sections = {}
        for i, (ident, title, start) in enumerate(headings):
            if ident in self.sections:
                raise BuildError(f"Duplicitní číslo sekce {ident}.")
            end = headings[i + 1][2] if i + 1 < len(headings) else len(data)
            self.sections[ident] = Section(ident, title, start, end)
        self.preamble = data[:headings[0][2]]

    def select(self, selector: dict) -> list[dict]:
        ident = selector["section"]
        if ident not in self.sections:
            raise BuildError(f"Neznámá sekce §{ident}.")
        selected = [s for s in self.sections.values() if s.id == ident or
                    (selector.get("children", False) and s.id.startswith(ident + "."))]
        result = []
        for section in selected:
            content = self.data[section.start:section.end]
            result.append({"section": section.id, "source_sha256": self.digest,
                           "byte_start": section.start, "byte_end": section.end,
                           "sha256": sha(content), "text": content.decode("utf-8")})
        return result


def unique(items: list[str], label: str) -> None:
    if any(not isinstance(v, str) or not v.strip() for v in items) or len(set(items)) != len(items):
        raise BuildError(f"{label}: prázdné nebo duplicitní hodnoty.")


def decision_excerpt(root: Path, spec: dict) -> dict:
    raw = local_bytes(root, spec["path"])
    if sha(raw) != spec["sha256"]:
        raise BuildError(f"Změněný opravný dodatek: {spec['path']}")
    lines = raw.splitlines(keepends=True)
    positions, offset = [], 0
    for line in lines:
        text = line.decode("utf-8").rstrip("\r\n")
        if text.startswith(spec["heading_prefix"]):
            positions.append((offset, len(text) - len(text.lstrip("#"))))
        offset += len(line)
    if len(positions) != 1:
        raise BuildError(f"Nejednoznačná opravná sekce {spec['heading_prefix']}.")
    start, level = positions[0]
    offset, end = 0, len(raw)
    for line in lines:
        text = line.decode("utf-8")
        match = re.match(r"^(#{1,6}) ", text)
        if offset > start and match and len(match[1]) <= level:
            end = offset
            break
        offset += len(line)
    content = raw[start:end]
    return {"kind": "approved_recovery_amendment", "path": spec["path"],
            "document_sha256": sha(raw), "byte_start": start, "byte_end": end,
            "sha256": sha(content), "text": content.decode("utf-8"),
            "precedence": "Technické rozhodnutí nahrazuje pouze výslovně opravované věty původního SSOT."}


def architecture_id(prefix: str, plan: dict) -> str:
    matches = [a["id"] for a in plan["architecture_items"] if a["id"] == prefix or
               a["id"].startswith(prefix + "-")]
    if len(matches) != 1:
        raise BuildError(f"Nejednoznačná architektura {prefix}: {matches}")
    return matches[0]


def artifact_inputs(root: Path, layout: dict, source_hash: str) -> tuple[dict, list[str]]:
    """Důkaz musí vázat přesné bytes katalogu, SSOT i validační program.

    Výstup producenta není automaticky proof. Formát dat je určen JSON pointery
    v layoutu; tento modul nevykonává cizí skripty ani nevymýšlí jejich schémata.
    """
    artifacts, blockers = {}, []
    for spec in layout["artifact_inputs"]:
        name = spec["path"]
        try:
            raw = local_bytes(root, name)
            value = strict_json(raw)
        except FileNotFoundError:
            blockers.append(f"MISSING_ARTIFACT:{name}")
            continue
        records = pointer(value, spec["records_pointer"])
        if not isinstance(records, list) or not records:
            raise BuildError(f"{name}: katalog musí obsahovat neprázdný seznam záznamů.")
        index = {}
        for record in records:
            ident = pointer(record, spec["id_pointer"])
            if not isinstance(ident, str) or not ident or ident in index:
                raise BuildError(f"{name}: konfliktní/prázdná record identity {ident!r}.")
            index[ident] = record
        artifacts[name] = {"sha256": sha(raw), "records": index, "value": value}
        try:
            proof = strict_json(local_bytes(root, spec["proof_path"]))
        except FileNotFoundError:
            blockers.append(f"MISSING_VALIDATION_PROOF:{spec['proof_path']}")
            continue
        if (proof.get("status") != "PASS" or proof.get("artifact_sha256") != sha(raw)
                or proof.get("source_sha256") != source_hash or proof.get("blockers") != []):
            blockers.append(f"INVALID_VALIDATION_PROOF:{name}")
            continue
        validator = proof.get("validator", {})
        try:
            validator_hash = sha(local_bytes(root, validator.get("path", "")))
        except (BuildError, FileNotFoundError):
            blockers.append(f"MISSING_VALIDATOR:{name}")
            continue
        if validator.get("sha256") != validator_hash:
            blockers.append(f"STALE_VALIDATOR:{name}")
        checks = proof.get("checks", [])
        unique([c["id"] for c in checks], f"Proof {name}")
        passed = {c["id"] for c in checks if c.get("status") == "PASS"}
        if not set(spec["required_checks"]) <= passed or any(c.get("status") != "PASS" for c in checks):
            blockers.append(f"INCOMPLETE_VALIDATION_PROOF:{name}")
        if set(proof.get("record_ids", [])) != set(index):
            blockers.append(f"INCOMPLETE_RECORD_PROOF:{name}")
    return artifacts, blockers


def _expand_files(layout: dict) -> list[dict]:
    files = deepcopy(layout["files"])
    for item in list(files):
        test = item.pop("test", None)
        if test is None:
            continue
        # Testovací soubor sdílí konkrétní kontrakt SUT, nikoli nový domain writer.
        files.append({**deepcopy(item), "path": test["path"], "language": test["language"],
                      "purpose": test["purpose"], "behavior": test["behavior"],
                      "provides": [], "requires": list(item["provides"]),
                      "dependencies": [item["path"]], "expected_output_tokens": 4500,
                      "acceptance": test["acceptance"], "scenarios": test["scenarios"],
                      "role": "test", "test_of": item["path"]})
    return files


def build(root: Path = ROOT, layout: dict | None = None, *, check_host: bool = True) -> dict:
    from kajovo.core.context_compiler import global_obligations

    layout = deepcopy(layout) if layout is not None else strict_json(local_bytes(root, "implementation-layout.json"))
    if layout.get("version") != 1:
        raise BuildError("Nepodporovaná verze layoutu.")
    inputs = {name: local_bytes(root, name) for name in SOURCE_FILES}
    source = SourceIndex(inputs[SOURCE_FILES[2]])
    requirements = strict_json(inputs[SOURCE_FILES[0]])
    plan = strict_json(inputs[SOURCE_FILES[1]])
    blockers = []
    hashes = {name: sha(raw) for name, raw in inputs.items()}
    for name, expected in layout["source_sha256"].items():
        if hashes.get(name) != expected:
            raise BuildError(f"Zdroj se změnil: {name}")
    if set(layout["source_sha256"]) != set(SOURCE_FILES):
        raise BuildError("Layout musí pinovat všechny tři zdroje.")
    # Normalizované A1 je historický důkaz, ne vyřešení jeho blokujících rozhodnutí.
    for key, name in (("requirements", "A0.effective.json"), ("plan", "A1.effective.json")):
        try:
            data = local_bytes(root, name)
        except FileNotFoundError:
            blockers.append(f"MISSING_EFFECTIVE_INPUT:{name}")
            continue
        expected = layout.get("effective_sha256", {}).get(name)
        if expected != sha(data):
            blockers.append(f"UNREVIEWED_EFFECTIVE_INPUT:{name}")
            continue
        hashes[name] = sha(data)
        if key == "requirements":
            requirements = strict_json(data)
        else:
            plan = strict_json(data)
    artifacts, artifact_blockers = artifact_inputs(root, layout, source.digest)
    blockers.extend(artifact_blockers)
    hashes.update({name: value["sha256"] for name, value in artifacts.items()})
    requirements_by_id = {r["id"]: r for key in ("explicit_requirements", "implicit_requirements")
                          for r in requirements[key]}
    files = _expand_files(layout)
    unique([f["path"] for f in files], "Cesty souborů")
    casefold_paths = [f["path"].casefold() for f in files]
    if len(set(casefold_paths)) != len(files):
        raise BuildError("Kolize cest na case-insensitive filesystemu.")
    interfaces = deepcopy(layout["interfaces"])
    unique([i["id"] for i in interfaces], "Rozhraní")
    interface_by_id = {i["id"]: i for i in interfaces}
    providers = {}
    paths = {relative_path(f["path"]) for f in files}
    for file in files:
        for symbol in file["provides"]:
            if symbol in providers or symbol not in interface_by_id:
                raise BuildError(f"Neznámý nebo duplicitní provider {symbol}.")
            providers[symbol] = file["path"]
    if set(providers) != set(interface_by_id):
        raise BuildError("Rozhraní bez provideru.")
    structure = {"contract": "A2_STRUCTURE", "version": 2, "rules": [],
                 "packages": deepcopy(layout["packages"]),
                 "interfaces": [{"id": i["id"], "definition": i["signature"]} for i in interfaces],
                 "files": [], "implementation": {"version": 1, "scopes": [],
                                                  "interfaces": interfaces, "files": []}}
    scopes = structure["implementation"]["scopes"]
    source_owners: dict[str, set[str]] = {}
    artifact_owners: dict[str, set[str]] = {name: set() for name in artifacts}
    rule_cache = {}

    def rule(value: dict, path: str, reason: str) -> str:
        serialized = canonical(value)
        if serialized not in rule_cache:
            reference = f"/structure/rules/{len(structure['rules'])}"
            structure["rules"].append(serialized)
            scope = {"source": reference, "paths": [], "reason": reason}
            scopes.append(scope)
            rule_cache[serialized] = scope
        scope = rule_cache[serialized]
        if path not in scope["paths"]:
            scope["paths"].append(path)
        return scope["source"]

    for item in files:
        path = item["path"]
        unique(item["requirements"], f"{path}: requirements")
        if not item["requirements"] or not set(item["requirements"]) <= requirements_by_id.keys():
            raise BuildError(f"{path}: chybějící nebo neznámé requirements.")
        arches = [architecture_id(a, plan) for a in item["architecture"]]
        unique(arches, f"{path}: architecture")
        dependencies = set(item.get("dependencies", []))
        for symbol in item["requires"]:
            if symbol not in providers:
                raise BuildError(f"{path}: neznámý symbol {symbol}.")
            if providers[symbol] != path:
                dependencies.add(providers[symbol])
        if path in dependencies or not dependencies <= paths:
            raise BuildError(f"{path}: neplatná přímá závislost.")
        refs = []
        selected = {}
        for selector in item["source"]:
            for excerpt in source.select(selector):
                selected[excerpt["section"]] = excerpt
        if not selected:
            raise BuildError(f"{path}: chybí přesný SSOT výňatek.")
        for ident, excerpt in selected.items():
            source_owners.setdefault(ident, set()).add(path)
            refs.append(rule({"kind": "exact_ssot_excerpt", **excerpt}, path,
                             f"Přímý normativní podklad implementace §{ident}; bez zkrácení."))
        for decision in item.get("decisions", []):
            if decision not in layout.get("decisions", {}):
                raise BuildError(f"{path}: neznámý opravný dodatek {decision}.")
            value = decision_excerpt(root, layout["decisions"][decision])
            hashes[value["path"]] = value["document_sha256"]
            refs.append(rule(value, path, f"Výslovné řešení rozporu {decision}."))
        unresolved = list(item.get("unresolved", []))
        for selection in item.get("artifact_records", []):
            name = selection["artifact"]
            if name not in {s["path"] for s in layout["artifact_inputs"]}:
                raise BuildError(f"{path}: neznámý vstupní artefakt {name}.")
            if not selection["ids"]:
                unresolved.append(f"Nejsou vybrané konkrétní záznamy z {name}.")
            if name not in artifacts:
                unresolved.append(f"Chybí validovaný {name}.")
                continue
            for ident in selection["ids"]:
                if ident not in artifacts[name]["records"]:
                    raise BuildError(f"{path}: neznámý {name}#{ident}.")
                artifact_owners[name].add(ident)
                refs.append(rule({"kind": "validated_artifact_record", "artifact": name,
                                  "artifact_sha256": artifacts[name]["sha256"], "record_id": ident,
                                  "record": artifacts[name]["records"][ident]}, path,
                                 "Výslovná record-level implementační vazba."))
        facets = []
        for kind, definition in item["facets"].items():
            if kind not in FACETS or not definition.strip():
                raise BuildError(f"{path}: neplatný facet {kind}.")
            facets.append({"kind": kind, "definition": definition, "source_refs": refs})
        structure["files"].append({"path": path, "purpose": item["purpose"],
                                   "language": item["language"], "kind": "text",
                                   "dependencies": sorted(dependencies), "provides": item["provides"],
                                   "requires": item["requires"], "behavior": item["behavior"],
                                   "requirement_ids": item["requirements"], "architecture_item_ids": arches})
        structure["implementation"]["files"].append({
            "path": path, "facets": facets,
            "interface_bindings": [{"id": ident, "version": interface_by_id[ident]["version"]}
                                   for ident in sorted(set(item["provides"] + item["requires"]))],
            "acceptance_criteria": item["acceptance"], "test_scenarios": item["scenarios"],
            "assumptions": [], "unresolved_questions": [{"question": q, "critical": True} for q in unresolved],
            "required_facets": list(item["facets"]),
            "expected_output_tokens": item["expected_output_tokens"], "allow_empty": False})
        blockers.extend(f"UNRESOLVED:{path}:{q}" for q in unresolved)

    given_files = []
    for spec in layout.get("given_files", []):
        name, target = spec["artifact"], relative_path(spec["path"])
        if name not in artifacts:
            continue
        if target.casefold() in {p.casefold() for p in paths}:
            raise BuildError(f"Kolize hotového vstupu s generovaným souborem: {target}")
        paths.add(target)
        payload = artifacts[name]
        content = local_bytes(root, name)
        if sha(content) != payload["sha256"]:
            raise BuildError(f"Artefakt se změnil během kompilace: {name}")
        artifact_owners[name].update(payload["records"])
        reference = rule({"kind": "immutable_given_content", "source": name,
                          "sha256": payload["sha256"], "target": target}, target,
                         "Přesná kopie validovaného vstupu; nikdy generativní úloha.")
        structure["files"].append({"path": target, "purpose": "Přesná neměnná projekce " + name,
                                   "language": "JSON", "kind": "text", "dependencies": [],
                                   "provides": [], "requires": [],
                                   "behavior": "COPY_VERIFIED_INPUT: převzít exact bytes, nic nevymýšlet ani nepřepsat.",
                                   "requirement_ids": spec["requirements"],
                                   "architecture_item_ids": [architecture_id(a, plan) for a in spec["architecture"]]})
        structure["implementation"]["files"].append({"path": target,
            "facets": [{"kind": "serialization", "definition": "Převzetí přesných bytes s kontrolou SHA-256.",
                        "source_refs": [reference]}], "interface_bindings": [],
            "acceptance_criteria": ["Output SHA-256 je shodné s validovaným vstupem."],
            "test_scenarios": ["Změna jediného byte způsobí selhání importu/BUILD gate."],
            "assumptions": [], "unresolved_questions": [], "required_facets": ["serialization"],
            "expected_output_tokens": 1, "allow_empty": False})
        given_files.append({"source": name, "path": target, "sha256": payload["sha256"],
                            "bytes": len(content), "delivery": "COPY_VERIFIED_INPUT"})

    snapshot = {"requirements": requirements, "plan": plan, "structure": structure}
    obligations = global_obligations(snapshot)
    generated = {s["source"] for s in scopes}
    for scope in layout["global_scopes"]:
        if scope["source"] in generated or scope["source"] not in obligations:
            raise BuildError(f"Duplicitní nebo neplatná působnost {scope['source']}.")
        if not scope["paths"] or not set(scope["paths"]) <= paths or not scope["reason"].strip():
            raise BuildError(f"Neplatný vlastník povinnosti {scope['source']}.")
        generated.add(scope["source"])
        scopes.append(deepcopy(scope))
    missing_scopes = sorted(set(obligations) - generated)
    blockers.extend(f"UNSCOPED_OBLIGATION:{s}" for s in missing_scopes)
    uncovered = sorted(set(source.sections) - source_owners.keys())
    blockers.extend(f"UNOWNED_SOURCE_SECTION:{s}" for s in uncovered)
    for name, value in artifacts.items():
        blockers.extend(f"UNOWNED_ARTIFACT_RECORD:{name}#{ident}"
                        for ident in sorted(set(value["records"]) - artifact_owners[name]))
    # Preambule je samostatná povinnost, pokud obsahuje produktový obsah.
    if source.preamble.strip() and not layout.get("preamble_reviewed", False):
        blockers.append("UNREVIEWED_PREAMBLE")
    covered_req = {r for f in structure["files"] for r in f["requirement_ids"]}
    covered_arch = {a for f in structure["files"] for a in f["architecture_item_ids"]}
    missing_req = sorted(set(requirements_by_id) - covered_req)
    missing_arch = sorted({a["id"] for a in plan["architecture_items"]} - covered_arch)
    blockers.extend(f"UNOWNED_REQUIREMENT:{r}" for r in missing_req)
    blockers.extend(f"UNOWNED_ARCHITECTURE:{a}" for a in missing_arch)
    # Výňatky se nikdy automaticky neořezávají. Rozpad odpovědnosti řeší layout.
    context_sizes = {}
    for file, contract in zip(structure["files"], structure["implementation"]["files"], strict=True):
        applicable = [obligations[s["source"]] for s in scopes if file["path"] in s["paths"]]
        size = len(canonical({"file": file, "contract": contract, "obligations": applicable,
                              "interfaces": [interface_by_id[b["id"]] for b in contract["interface_bindings"]],
                              "requirements": [requirements_by_id[r] for r in file["requirement_ids"]]}).encode("utf-8"))
        context_sizes[file["path"]] = size
        if size > layout["max_context_bytes"]:
            blockers.append(f"CONTEXT_TOO_LARGE:{file['path']}:{size}>{layout['max_context_bytes']}")
    host_checks = []
    if check_host:
        from kajovo.core.delivery_preparation import validate_delivery_structure
        from kajovo.core.contracts import ContractError
        try:
            _, additions = validate_delivery_structure(requirements, plan, structure, "GENERATE")
            if additions:
                blockers.append("IMPLICIT_HOST_DEPENDENCY_REPAIR")
            else:
                host_checks.append("schema_and_traceability")
        except ContractError as exc:
            blockers.append(f"HOST_VALIDATION:{exc}")
        if not blockers:
            from kajovo.core.context_compiler import ContextCompiler
            from kajovo.core.generate_batch import build_manifest
            try:
                compiler = ContextCompiler(snapshot)
                for file in structure["files"]:
                    compiler.compile(file["path"])
                host_checks.append("all_contexts_compiled")
                # Sestavení manifestu je čistě lokální; žádný klient/klíč zde není.
                given_paths = {f["path"] for f in given_files}
                manifest = build_manifest("LW0D_SALVAGE_PREFLIGHT", "", plan, structure,
                                          layout["model"], None, requirements=requirements,
                                          paths=[f["path"] for f in structure["files"] if f["path"] not in given_paths],
                                          maximum_quality=True)
                if {f["path"] for f in manifest["omitted"]} != given_paths:
                    raise BuildError("Manifest vynechal jiné než přesně kopírované soubory.")
                host_checks.extend(["all_request_budgets", "batch_jsonl_integrity"])
            except ContractError as exc:
                blockers.append(f"HOST_CONTEXT_OR_BUDGET:{exc}")
    else:
        blockers.append("HOST_VALIDATION_NOT_RUN")
    return {"contract": "LW0D_IMPLEMENTATION_DRAFT", "version": 1,
            "ready": not blockers, "source_hashes": hashes,
            "layout_sha256": sha(canonical(layout).encode("utf-8")),
            "candidate": structure, "blockers": sorted(set(blockers)),
            "given_files": given_files,
            "report": {"file_count": len(structure["files"]), "interface_count": len(interfaces),
                       "covered_requirements": len(covered_req), "missing_requirements": missing_req,
                       "missing_architecture": missing_arch, "source_section_count": len(source.sections),
                       "unowned_sections": uncovered, "missing_scopes": missing_scopes,
                       "context_bytes": context_sizes, "host_checks": host_checks,
                       "runtime_verified": False, "batch_submitted": False}}


def final_structure(result: dict) -> dict:
    required_checks = {"schema_and_traceability", "all_contexts_compiled", "all_request_budgets",
                       "batch_jsonl_integrity"}
    if not result["ready"] or result["blockers"] or not required_checks <= set(result["report"]["host_checks"]):
        raise BuildError("Finální A2 blokováno; nejprve odstraňte konkrétní nálezy z draft reportu.")
    return deepcopy(result["candidate"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--final", action="store_true", help="Pouze kompletně ověřené A2 na stdout.")
    parser.add_argument("--summary", action="store_true", help="Pouze report a blokátory; žádný A2 výstup.")
    args = parser.parse_args(argv)
    try:
        if args.final and args.summary:
            raise BuildError("--final a --summary jsou vzájemně výlučné.")
        result = build(args.root)
        output = final_structure(result) if args.final else result
        if args.summary:
            output = {k: v for k, v in result.items() if k != "candidate"}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 2
    except (BuildError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"A2 BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
