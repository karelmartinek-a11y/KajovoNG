"""Offline důkazy předávek nesmějí záviset na lokálním textovém kódování."""

import ast
from pathlib import Path


def test_closure_text_files_have_explicit_encoding():
    root = Path(__file__).resolve().parent
    paths = sorted(root.glob("*closure*.py")) + [root / "test_delivery_http_graph.py"]
    missing = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            method = node.func.attr
            if method not in {"read_text", "write_text"}:
                continue
            positional = 1 if method == "read_text" else 2
            if len(node.args) >= positional or any(key.arg == "encoding" for key in node.keywords):
                continue
            missing.append(f"{path.name}:{node.lineno}:{method}")
    assert not missing, "Chybí explicitní kontrakt kódování: " + ", ".join(missing)
