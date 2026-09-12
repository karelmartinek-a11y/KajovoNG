"""Statický guard: validační/kontrolní funkce nesmějí spouštět placený generativní transport."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "kajovo"
PAID_METHODS = {"create_response", "_send_response", "create_batch"}
VALIDATION_NAME_PARTS = ("validate", "verify", "check", "probe", "preflight")


def _called_attribute_names(node: ast.AST) -> set[str]:
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def test_validation_named_functions_cannot_call_paid_generation_transport():
    violations = []
    for path in sorted(RUNTIME.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lowered = node.name.lower()
            if not any(part in lowered for part in VALIDATION_NAME_PARTS):
                continue
            paid = _called_attribute_names(node) & PAID_METHODS
            if paid:
                violations.append(
                    f"{path.relative_to(RUNTIME).as_posix()}::{node.name} -> {sorted(paid)}"
                )
    assert violations == [], (
        "Validační/kontrolní funkce nesmí odesílat placenou generativní práci:\n"
        + "\n".join(violations)
    )


def test_removed_live_verification_scripts_are_not_reintroduced():
    forbidden_names = {
        "verify_openai_live.py",
        "verify_batch_live.py",
        "verify_generate_batch_live.py",
        "verify_response_contracts_live.py",
        "verify_workflows_live.py",
    }
    present = {path.name for path in (ROOT / "scripts").glob("*.py")}
    assert present.isdisjoint(forbidden_names)
