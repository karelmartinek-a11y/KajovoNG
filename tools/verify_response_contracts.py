"""Inventář skutečných response masek a úplná kontrola jejich datových uzlů."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from itertools import combinations
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kajovo.core import comic_types
from kajovo.core.cascade_contract import runtime_schema_for_step
from kajovo.core.cascade_production import document_artifact_format
from kajovo.core.cascade_schemas import legacy_schema_for_step
from kajovo.core.cascade_types import CascadeDecisionOption, CascadeOutput, CascadeStep, CASCADE_FILE_TYPES
from kajovo.core.contracts import file_response_format, historical_file_response_format, structure_response_format
from kajovo.core.generate_batch import plan_format, structure_format
from kajovo.core.orchestration.preparation import FORMATS
from kajovo.core.photo_prompt import professionalize_payload
from kajovo.core.provider_contracts import DELETES, LISTS, MODELS, native_contract, validate_native_schema
from kajovo.core.requirements import enriched_plan_format, enriched_structure_format, requirements_format
from kajovo.core.structured_output import (
    builtin_format, file_content_format, obj, qa_answer_format, qfile_plan_format,
    schema_preparation_format, text_format, validate_schema,
)


def response_contract_catalog():
    """Používá produkční továrny; duplicity názvů historických variant rozlišuje klíč."""
    result = {stage: fmt["format"]["schema"] for stage, fmt in FORMATS.items()}
    factories = {
        "TEXT_RESPONSE": text_format, "FILE_CONTENT_V1": file_content_format,
        "SCHEMA_PREPARATION": schema_preparation_format,
        "QA_ANSWER_V2": qa_answer_format, "QFILE_PLAN_V1": qfile_plan_format,
        "A1_PLAN_LEGACY": plan_format, "A2_STRUCTURE_V2_LEGACY": structure_format,
        "A3_HISTORICAL": historical_file_response_format,
    }
    result.update({name: factory()["format"]["schema"] for name, factory in factories.items()})
    for name in ("B1_PLAN", "C_FILES_ALL"):
        result[name] = builtin_format(name)["format"]["schema"]
    for mode in ("GENERATE", "MODIFY"):
        result[mode + "_REQUIREMENTS_LEGACY"] = requirements_format(mode)["format"]["schema"]
        result[mode + "_PLAN_LEGACY"] = enriched_plan_format(mode)["format"]["schema"]
        for implementation in (False, True):
            result[f"{mode}_STRUCTURE_LEGACY_{implementation}"] = enriched_structure_format(mode, implementation=implementation)["format"]["schema"]
    for stage in ("A2_STRUCTURE", "B2_STRUCTURE"):
        result[stage] = structure_response_format(stage)["format"]["schema"]
    for stage, action in (("A3_FILE", None), ("B3_FILE_ADD", "add"), ("B3_FILE_MODIFY", "modify")):
        result[stage] = file_response_format("A3_FILE" if action is None else "B3_FILE", "output.txt", 0, action)["format"]["schema"]
    result["PHOTO_PLAN_V1"] = professionalize_payload("gpt-6-astra", "Zesvětli fotografii.")["text"]["format"]["schema"]
    for name in ("BIBLE", "DESCRIPTOR", "STORY", "SCRIPT", "STORYBOARD", "CONTINUITY"):
        result["COMIC_" + name] = getattr(comic_types, name + "_SCHEMA")
    for kind in ("manifest", "prompts"):
        _, schema = legacy_schema_for_step(CascadeStep(output_type="json", output_schema_kind=kind))
        result["CASCADE_LEGACY_" + kind] = schema
    for extension in CASCADE_FILE_TYPES:
        output = CascadeOutput(id="file", name="Soubor", kind="file", file_type=extension, file_name="output." + extension)
        result["CASCADE_FILE_" + extension] = runtime_schema_for_step(CascadeStep(outputs=[output]))
        if extension not in {"txt", "md", "json", "csv", "png", "jpg", "jpeg"}:
            result["CASCADE_DOCUMENT_" + extension] = document_artifact_format(output.file_name)["format"]["schema"]
    outputs = [
        CascadeOutput(id="text", name="Text", kind="text"),
        CascadeOutput(id="json", name="JSON", kind="json", json_schema=obj({"count": {"type": "integer"}})),
        CascadeOutput(id="decision", name="Rozhodnutí", kind="decision", decision_options=[CascadeDecisionOption(value="A"), CascadeDecisionOption(value="B")]),
        CascadeOutput(id="file", name="Soubor", kind="file", file_type="txt", file_name="output.txt"),
    ]
    for size in range(1, len(outputs) + 1):
        for selected in combinations(outputs, size):
            result["CASCADE_OUTPUTS_" + "_".join(output.kind for output in selected)] = runtime_schema_for_step(CascadeStep(outputs=list(selected)))
    return result


def schema_nodes(schema, path=()):
    """Každý uzel včetně všech větví a lokálních definic; nevybírá vzorek."""
    yield path, schema
    for key in ("properties", "$defs"):
        for name, child in schema.get(key, {}).items():
            yield from schema_nodes(child, (*path, key, name))
    if "items" in schema:
        yield from schema_nodes(schema["items"], (*path, "items"))
    for index, child in enumerate(schema.get("anyOf", [])):
        yield from schema_nodes(child, (*path, "anyOf", index))


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs/response-contract-inventory.json"
RUNTIME_SCHEMAS = ROOT / "docs/response-runtime-schemas.json"


def own_sources(root=ROOT):
    """Vlastní Python zdroje; distribuční kopie a binární artefakty vynechává."""
    for folder in ("kajovo", "kajovong", "utf8nobom", "Build", "scripts", "tools"):
        for path in sorted((root / folder).rglob("*.py")):
            relative = path.relative_to(root)
            if relative.parts[0] == "Build" and len(relative.parts) > 2 and (
                relative.parts[1] in {"lib", "Kajovo"} or relative.parts[1].startswith("bdist.")
            ):
                continue
            yield path


def own_source_assets(root=ROOT):
    """Síťové skripty, registry a fyzické kontrakty mimo Python také vyžadují audit."""
    extensions = {".ps1", ".sh", ".bat", ".cmd", ".js", ".ts", ".tsx", ".mjs", ".cjs",
                  ".json", ".yaml", ".yml", ".toml"}
    selected = set()
    for folder in ("kajovo", "kajovong", "utf8nobom", "Build", "scripts", "tools",
                   "resources/orchestration/contracts", ".github/workflows"):
        for path in (root / folder).rglob("*"):
            relative = path.relative_to(root)
            if relative.parts[0] == "Build" and len(relative.parts) > 2 and (
                relative.parts[1] in {"lib", "Kajovo"} or relative.parts[1].startswith("bdist.")
            ):
                continue
            if path.is_file() and path.suffix.lower() in extensions:
                selected.add(path)
    selected.update(path for path in root.glob("*.bat") if path.is_file())
    selected.update(path for path in (root / "requirements").glob("*.txt") if path.is_file())
    if (root / "pyproject.toml").is_file():
        selected.add(root / "pyproject.toml")
    yield from sorted(selected)


def response_call_sites(root=ROOT):
    """Nezávislá AST inventura fasády, transportu, wrapperů a síťových hranic.

    Názvy fasády se odvozují z implementace, nejsou ručním seznamem API.
    Jde o kandidáty statických hranic, nikoli důkaz všech datových předávek.
    """
    client = ast.parse((root / "kajovo/core/openai_client.py").read_text(encoding="utf-8"))
    names = {node.name for node in ast.walk(client)
             if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    names |= {"_req", "_list_all", "_create_response", "_send_response", "_schema_request",
              "text_request", "create_image_batch", "request", "send_message", "sendmail",
              "exec_command", "recv_exit_status", "getattr", "connect", "ehlo", "starttls", "login",
              "get_transport", "get_remote_server_key", "getresponse", "urlopen", "send", "recv"}
    result = []

    class Calls(ast.NodeVisitor):
        def __init__(self, relative, source):
            self.relative, self.source = relative, source
            self.scope = []
            self.ordinals = {}
            self.functions = []

        def visit_ClassDef(self, node):
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def visit_FunctionDef(self, node):
            self.scope.append(node.name)
            self.functions.append(node)
            self.generic_visit(node)
            self.functions.pop()
            self.scope.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
            if name in names:
                scope = ".".join(self.scope) or "<module>"
                key = (scope, name)
                self.ordinals[key] = self.ordinals.get(key, 0) + 1
                result.append({
                    "id": f"{self.relative}::{scope}::{name}::{self.ordinals[key]}",
                    "path": self.relative, "line": node.lineno, "end_line": node.end_lineno,
                    "scope": scope, "call": name, "expression": ast.unparse(node),
                    "call_sha256": hashlib.sha256(ast.dump(node).encode()).hexdigest(),
                    "caller_sha256": hashlib.sha256(ast.dump(self.functions[-1] if self.functions else node).encode()).hexdigest(),
                })
            self.generic_visit(node)

    for path in own_sources(root):
        source = path.read_text(encoding="utf-8-sig")
        Calls(path.relative_to(root).as_posix(), source).visit(ast.parse(source, filename=str(path)))
    return sorted(result, key=lambda row: row["id"])


def transport_registry(root=ROOT):
    """Endpointy získá z obou skutečných dispatch tabulek transportu."""
    from kajovo.core import openai_transport
    source = ast.parse((root / "kajovo/core/openai_transport.py").read_text(encoding="utf-8"))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == "operation_spec")
    assignments = {node.target.id: node.value for node in function.body
                   if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    rows = []
    for key, value in zip(assignments["exact"].keys, assignments["exact"].values, strict=True):
        method, path = ast.literal_eval(key)
        rows.append({"id": f"{method} {path}", "method": method, "path": path,
                     "path_kind": "exact", "operation": getattr(openai_transport, value.id).name})
    for node in assignments["patterns"].elts:
        method, path = [ast.literal_eval(part) for part in node.elts[:2]]
        rows.append({"id": f"{method} {path}", "method": method, "path": path,
                     "path_kind": "regex", "operation": getattr(openai_transport, node.elts[2].id).name})
    return sorted(rows, key=lambda row: row["id"])


def runtime_catalog():
    domain = response_contract_catalog()
    for schema in domain.values():
        validate_schema(schema)
    native = {name: native_contract(name) for name in sorted(set(MODELS) | set(LISTS) | set(DELETES))}
    for schema in native.values():
        validate_native_schema(schema)
    return {"domain": domain, "native": native}


def verify_inventory(inventory, discovered, *, require_complete=False):
    rows = inventory["calls"]
    indexed = {row["id"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Inventář obsahuje duplicitní ID.")
    actual = {row["id"]: row for row in discovered}
    if set(indexed) != set(actual):
        raise ValueError(f"Inventář neodpovídá zdrojům: nové={sorted(set(actual) - set(indexed))}; chybějící={sorted(set(indexed) - set(actual))}.")
    for identifier, row in actual.items():
        if any(indexed[identifier][field] != row[field] for field in ("call_sha256", "caller_sha256")):
            raise ValueError(f"Volání nebo větvení procesu se změnilo bez aktualizace inventáře: {identifier}.")
    if require_complete:
        pending = [row["id"] for row in rows if row["verification"]["status"] != "verified"]
        if pending:
            raise ValueError(f"Audit je PARTIAL: {len(pending)} hranic nemá úplně doložené kontrakty a předávky.")
        for row in rows:
            if not row["variants"] or any(
                not variant.get("contract_source") or not variant.get("runtime_validation")
                or not variant.get("consumers") or not variant.get("tests")
                for variant in row["variants"]
            ):
                raise ValueError(f"Neúplný důkaz kontraktových variant: {row['id']}.")


def verify_source_index(inventory, root=ROOT):
    expected = {row["path"]: row["sha256"] for row in inventory["sources"]}
    actual = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for path in own_sources(root)}
    if expected != actual:
        changed = sorted(key for key in set(expected) | set(actual) if expected.get(key) != actual.get(key))
        raise ValueError(f"Změna vlastních zdrojů vyžaduje nové doložení inventáře: {changed}.")
    expected_assets = {row["path"]: row["sha256"] for row in inventory.get("source_assets", [])}
    actual_assets = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
                     for path in own_source_assets(root)}
    if expected_assets != actual_assets:
        changed = sorted(key for key in set(expected_assets) | set(actual_assets) if expected_assets.get(key) != actual_assets.get(key))
        raise ValueError(f"Změna skriptů nebo fyzických kontraktů vyžaduje nové doložení inventáře: {changed}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    compiled = runtime_catalog()
    catalog = compiled["domain"]
    rows = [{"id": name, "nodes": sum(1 for _ in schema_nodes(schema)), "schema": schema} for name, schema in catalog.items()]
    calls = response_call_sites()
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    verify_source_index(inventory)
    if inventory["transport_registry"] != transport_registry():
        raise ValueError("Registr operací transportu se změnil bez kontraktového auditu.")
    if compiled != json.loads(RUNTIME_SCHEMAS.read_text(encoding="utf-8")):
        raise ValueError("Skutečné runtime masky neodpovídají inventáři; změna varianty vyžaduje audit.")
    verify_inventory(inventory, calls, require_complete=args.require_complete)
    result = {"contracts": rows, "call_sites": calls, "inventory": inventory,
              "native_contracts": compiled["native"],
              "scope": "Kontrola továren Responses, 20 nativních operací a statické inventury. Responses provider obálka a nedoložené předávky jsou otevřené položky; tento příkaz neuděluje celkový PASS."}
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Ověřeno masek: {len(rows)}; datových uzlů: {sum(row['nodes'] for row in rows)}; statických hranic: {len(calls)}; úplnost auditu: {inventory['status']}.")


if __name__ == "__main__":
    main()
