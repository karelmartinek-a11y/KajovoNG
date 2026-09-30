"""Modul nesmí nenápadně přepsat vlastní dříve definovanou funkci."""

import ast
from collections import Counter
from pathlib import Path


def test_core_module_functions_have_single_definition():
    root = Path(__file__).resolve().parents[1]
    shadowed = []
    for path in (root / "kajovo/core").rglob("*.py"):
        if path.name.startswith("._"):
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        names = Counter(node.name for node in module.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
        shadowed.extend((path.relative_to(root).as_posix(), name) for name, count in names.items() if count > 1)
    assert shadowed == []
