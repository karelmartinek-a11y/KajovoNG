"""Odvodí úplný seznam konstrukcí, vlastností a vazeb nového studia ze zdrojů."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def inventory(root):
    modules = []
    for path in sorted((root / "kajovo" / "studio").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        records = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                calls = []
                for call in ast.walk(node):
                    if not isinstance(call, ast.Call):
                        continue
                    name = ast.unparse(call.func)
                    kind = "volání"
                    if name == "action":
                        kind = "akce a handler"
                    elif name.endswith(".connect"):
                        kind = "vazba signálu"
                    elif name.split(".")[-1] in {"text", "check", "choice", "add"} or name.startswith("Q"):
                        kind = "konstrukce prvku"
                    elif "validate" in name or "assert_" in name:
                        kind = "validace"
                    elif name.split(".")[-1].startswith("set"):
                        kind = "vlastnost prvku"
                    elif "Dialog" in name:
                        kind = "dialog"
                    calls.append({"line": call.lineno, "kind": kind, "expression": ast.unparse(call)})
                records.append({"method": node.name, "line": node.lineno, "calls": calls})
        modules.append({"file": path.relative_to(root).as_posix(), "methods": records,
                        "imports": [ast.unparse(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]})
    return {"schema_version": 1, "scope": "kajovo/studio", "modules": modules,
            "note": "Statický inventář zahrnuje i dynamické továrny; skutečné instance, texty, souřadnice a stavy obsahují manifesty snímků. Vazba ve zdroji sama nedokazuje úspěch síťové operace."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/ui/studio-inventory.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = inventory(root)
    target = root / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Zapsáno modulů: {len(result['modules'])}")


if __name__ == "__main__":
    main()
