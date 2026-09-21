"""Opakovatelná inventura zdrojů, JSON masek, SQL schémat a předávacích míst.

Inventura není důkazem významové správnosti programu. Zachycuje přesný
rozsah statického průchodu a skutečně spuštěných kontrol kontraktů.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.metadata
import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOURCE_SUFFIXES = {".py", ".ps1", ".bat", ".sh", ".js", ".ts", ".tsx"}
DOMAINS = {
    "provider": {"create_response", "create_batch", "create_image", "upload_file", "retrieve_response", "retrieve_batch", "file_content"},
    "contract": {"freeze_order", "prepare_provider_request", "prepare_batch", "prepare_payload", "response_format", "validate_output", "compile_schema", "encode_requests"},
    "storage": {"execute", "executemany", "executescript", "connect", "commit", "rollback", "archive_artifact", "save_json", "update_state", "record_lineage"},
    "filesystem": {"safe_join_under_root", "validate_relative_path", "write_bytes", "write_text", "replace", "unlink", "copy2", "copytree", "prepare_publish", "commit_publish", "publish_staged_run"},
    "recovery": {"confirmed_id", "has_entry", "mark_submission_started", "mark_submitted", "mark_submission", "work_order_from_mapping", "recover_publish_journal"},
}


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def source_paths():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    paths = []
    for name in result.stdout.decode("utf-8", errors="strict").split("\0"):
        if not name:
            continue
        if name.startswith(("Build/lib/", "Build/Kajovo/", "Build/bdist.", "dist/")):
            continue
        if Path(name).suffix.lower() in SOURCE_SUFFIXES:
            paths.append(name)
    return sorted(paths)


class CallInventory(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.scope = []
        self.calls = []
        self.orders = []

    def visit_FunctionDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Call(self, node):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
        domains = sorted(domain for domain, names in DOMAINS.items() if name in names)
        if domains:
            self.calls.append({"path": self.path, "line": node.lineno, "scope": ".".join(self.scope), "call": ast.unparse(node.func), "domains": domains})
        if name == "freeze_order":
            row = {"path": self.path, "line": node.lineno, "scope": ".".join(self.scope), "fields": {}, "dynamic": []}
            task = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "task"), None)
            if isinstance(task, ast.Dict):
                for key, value in zip(task.keys, task.values, strict=True):
                    if not isinstance(key, ast.Constant) or key.value not in {"stage", "route", "provider_endpoint", "target_path", "expected_target_hash", "contract_name", "schema", "approval_id", "attempt_no"}:
                        continue
                    if isinstance(value, ast.Constant):
                        row["fields"][key.value] = value.value
                    else:
                        row["dynamic"].append(key.value)
                row["has_explicit_target_expectation"] = any(isinstance(key, ast.Constant) and key.value == "expected_target_hash" for key in task.keys)
                row["has_explicit_provider_endpoint"] = any(isinstance(key, ast.Constant) and key.value == "provider_endpoint" for key in task.keys)
            else:
                row["dynamic_task"] = True
            self.orders.append(row)
        self.generic_visit(node)


def inventory():
    sources, calls, orders, errors = [], [], [], []
    for name in source_paths():
        raw = (ROOT / name).read_bytes()
        row = {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "lines": len(raw.splitlines()), "production": name.startswith(("kajovo/", "kajovong/", "utf8nobom/"))}
        if name.endswith(".py"):
            try:
                tree = ast.parse(raw, filename=name)
                visitor = CallInventory(name)
                visitor.visit(tree)
                calls.extend(visitor.calls)
                orders.extend(visitor.orders)
                row["ast"] = "passed"
            except (SyntaxError, UnicodeError, ValueError) as exc:
                row["ast"] = "failed"
                errors.append({"path": name, "error": str(exc)})
        else:
            row["ast"] = "not_python"
        sources.append(row)
    return {"sources": sources, "calls": calls, "work_order_calls": orders, "errors": errors}


def deny_network(*args, **kwargs):
    raise RuntimeError("Kontrola kontraktů nesmí navazovat síťová spojení.")


def schema_inventory():
    import jsonschema
    from kajovo.core.structured_output import file_content_format, qa_answer_format, qfile_plan_format, text_format, validate_schema

    records, errors = [], []
    modules = [
        "kajovo.core.orchestration.preparation",
        "kajovo.core.orchestration.work_order",
        "kajovo.core.orchestration.run_config",
        "kajovo.core.comic_types",
        "kajovo.core.photo_prompt",
        "kajovo.core.cascade_contract",
    ]
    for module_name in modules:
        module = importlib.import_module(module_name)
        for name, value in vars(module).items():
            if not name.endswith("SCHEMA") or not isinstance(value, dict):
                continue
            key = module_name + "." + name
            try:
                jsonschema.Draft202012Validator.check_schema(value)
                records.append({"name": key, "kind": "local_schema", "sha256": fingerprint(value), "status": "passed"})
            except Exception as exc:
                errors.append({"name": key, "error": type(exc).__name__ + ": " + str(exc)})
        formats = getattr(module, "FORMATS", {})
        if isinstance(formats, dict):
            for stage, value in sorted(formats.items()):
                fmt = value.get("format") if isinstance(value, dict) else None
                if not isinstance(fmt, dict):
                    errors.append({"name": module_name + ".FORMATS." + str(stage), "error": "Chybí jednoznačná maska format."})
                    continue
                try:
                    validate_schema(fmt["schema"])
                    if fmt.get("strict") is not True or fmt.get("type") != "json_schema":
                        raise ValueError("Chybí strict JSON Schema.")
                    records.append({"name": str(stage), "contract": fmt["name"], "kind": "provider_mask", "sha256": fingerprint(fmt["schema"]), "status": "passed"})
                except Exception as exc:
                    errors.append({"name": str(stage), "error": type(exc).__name__ + ": " + str(exc)})
    for factory in (file_content_format, qa_answer_format, qfile_plan_format, text_format):
        fmt = factory()["format"]
        validate_schema(fmt["schema"])
        records.append({"name": factory.__name__, "contract": fmt["name"], "kind": "provider_mask", "sha256": fingerprint(fmt["schema"]), "status": "passed"})
    return {"schemas": records, "errors": errors}


def database_inventory():
    from kajovo.core.comic_store import ComicStore
    from kajovo.core.orchestration.repository import OrchestrationRepository

    records = []
    with tempfile.TemporaryDirectory(prefix="kajovo-contracts-") as temporary:
        root = Path(temporary)
        orchestration = OrchestrationRepository(root / "orchestration.sqlite3")
        comics = ComicStore(root / "comics")
        comics.initialize()
        for name, store in (("orchestration", orchestration), ("comics", comics)):
            with store.connect() as db:
                tables = []
                for row in db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"):
                    table = str(row[0])
                    quoted = '"' + table.replace('"', '""') + '"'
                    tables.append({"name": table, "ddl_hash": fingerprint(row[1]), "columns": [list(value) for value in db.execute("PRAGMA table_info(" + quoted + ")")], "foreign_keys": [list(value) for value in db.execute("PRAGMA foreign_key_list(" + quoted + ")")], "indexes": [list(value) for value in db.execute("PRAGMA index_list(" + quoted + ")")]})
                integrity = [str(value[0]) for value in db.execute("PRAGMA integrity_check")]
                foreign_errors = [list(value) for value in db.execute("PRAGMA foreign_key_check")]
                records.append({"name": name, "tables": tables, "foreign_keys_enabled": int(db.execute("PRAGMA foreign_keys").fetchone()[0]), "integrity": integrity, "foreign_key_errors": foreign_errors})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    static = inventory()
    for call in static["work_order_calls"]:
        if (
            call.get("path", "").startswith(("kajovo/", "kajovong/", "utf8nobom/"))
            and not call.get("dynamic_task")
            and not call.get("has_explicit_provider_endpoint")
        ):
            static["errors"].append({
                "path": call["path"],
                "line": call["line"],
                "error": "Kanonický freeze_order nemá explicitní provider_endpoint.",
            })
    original_connect = socket.socket.connect
    original_create = socket.create_connection
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    try:
        schemas = schema_inventory()
        databases = database_inventory()
    finally:
        socket.socket.connect = original_connect
        socket.create_connection = original_create
    report = {"version": 1, "git_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "python": sys.version.split()[0], "openai_sdk": importlib.metadata.version("openai"), "source_fingerprint": fingerprint(static["sources"]), **static, **schemas, "databases": databases, "limitations": ["AST inventura není ruční sémantický důkaz všech funkcí.", "Kontroly DB používají skutečné SQLite databáze, ale výhradně syntetická data.", "Živá OpenAI acceptance není spuštěna."]}
    report["errors"] = static["errors"] + schemas["errors"]
    for db in databases:
        if db["integrity"] != ["ok"] or db["foreign_key_errors"] or db["foreign_keys_enabled"] != 1:
            report["errors"].append({"database": db["name"], "error": "Integrita nebo vynucení FK nesplněno."})
        if db["name"] == "orchestration":
            work_orders = next(
                (table for table in db["tables"] if table["name"] == "work_orders"),
                None,
            )
            columns = {
                row[1] for row in (work_orders or {}).get("columns", [])
            }
            required = {
                "work_order_hash",
                "body_ref",
                "input_hash",
                "attempt_id",
                "provider_endpoint",
                "work_order_json",
            }
            if work_orders is None or not required <= columns:
                report["errors"].append({
                    "database": db["name"],
                    "error": "work_orders nemá úplnou fyzickou vazbu identity/payloadu/endpointu.",
                })
    if args.compare:
        previous = json.loads(args.compare.read_text(encoding="utf-8"))
        for key in ("source_fingerprint", "schemas", "databases"):
            if report[key] != previous[key]:
                report["errors"].append({"comparison": key, "error": "Opakovaný průchod se liší."})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("CONTRACT_LINKS", json.dumps({"git_sha": report["git_sha"], "source_files": len(report["sources"]), "production_files": sum(row["production"] for row in report["sources"]), "source_lines": sum(row["lines"] for row in report["sources"]), "calls": len(report["calls"]), "work_order_sites": report["work_order_calls"], "schemas": report["schemas"], "database_tables": {row["name"]: len(row["tables"]) for row in databases}, "errors": report["errors"]}, ensure_ascii=False))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
