"""Statický forenzní inventář desktopového UI a jeho vazeb.

Audit je odvozený read model nad zdrojovým kódem. Kanonickou pravdou zůstává
implementace; inventář lze kdykoliv deterministicky znovu vytvořit.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


UI_FACTORIES = {
    "QWidget",
    "QFrame",
    "QLabel",
    "QPushButton",
    "QToolButton",
    "QLineEdit",
    "QPlainTextEdit",
    "QTextEdit",
    "QTextBrowser",
    "QComboBox",
    "QSpinBox",
    "QDoubleSpinBox",
    "QCheckBox",
    "QRadioButton",
    "QListWidget",
    "QTreeWidget",
    "QTableWidget",
    "QTabWidget",
    "QStackedWidget",
    "QSplitter",
    "QProgressBar",
    "QDialogButtonBox",
    "QDateEdit",
    "QFileDialog",
    "QInputDialog",
    "button",
    "label",
    "text",
    "editor",
    "combo",
    "number",
    "table",
    "card",
    "notice",
    "badge",
    "empty_state",
    "metric_card",
    "toolbar",
    "page_header",
    "section_title",
}

POPUP_CALLS = {
    "msg_info",
    "msg_warning",
    "msg_critical",
    "msg_question",
    "dialog_open_file",
    "dialog_open_files",
    "dialog_save_file",
    "dialog_select_dir",
    "dialog_input_text",
}

SIGNALS = {
    "clicked",
    "toggled",
    "triggered",
    "currentTextChanged",
    "currentIndexChanged",
    "currentItemChanged",
    "itemSelectionChanged",
    "itemDoubleClicked",
    "textChanged",
    "valueChanged",
    "accepted",
    "rejected",
    "finished",
    "timeout",
    "editingFinished",
    "stateChanged",
}


def _name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return type(node).__name__


def _literal(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except Exception:
        return _name(node)


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return _name(node.func)


def _assigned_name(parent: ast.AST | None) -> str:
    if isinstance(parent, ast.Assign):
        return ", ".join(_name(target) for target in parent.targets)
    if isinstance(parent, ast.AnnAssign):
        return _name(parent.target)
    return ""


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    result: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            result[child] = parent
    return result


def _context(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> tuple[str, str]:
    cls = ""
    method = ""
    current: ast.AST | None = node
    while current in parents:
        current = parents[current]
        if not method and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            method = current.name
        if isinstance(current, ast.ClassDef):
            cls = current.name
            break
    return cls, method


def _core_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        if "core" not in module:
            continue
        for alias in node.names:
            names.add(alias.asname or alias.name)
    return names


def _backend_calls(method: ast.AST, core_names: set[str]) -> list[str]:
    calls: set[str] = set()
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        expression = _name(node.func)
        root = expression.split(".", 1)[0]
        if (
            root in core_names
            or expression.startswith("self.client.")
            or expression.startswith("client.")
            or expression.startswith("OpenAIClient")
            or expression.startswith("self.jobs.")
            or expression.startswith("self.log.")
            or expression.startswith("self.bundle.")
        ):
            calls.add(expression)
    return sorted(calls)


def _validation_evidence(method: ast.AST) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node in ast.walk(method):
        if isinstance(node, ast.Raise):
            records.append({"line": node.lineno, "kind": "raise", "value": _name(node.exc)})
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in {"msg_warning", "msg_critical"} or (
                isinstance(node.func, ast.Attribute)
                and _name(node.func.value) == "QMessageBox"
                and name in {"warning", "critical"}
            ):
                records.append(
                    {
                        "line": node.lineno,
                        "kind": "user_validation",
                        "value": _literal(node.args[2] if len(node.args) > 2 else None),
                    }
                )
            elif name == "setEnabled":
                records.append(
                    {
                        "line": node.lineno,
                        "kind": "availability_guard",
                        "value": _name(node.args[0]) if node.args else "",
                    }
                )
    return sorted(records, key=lambda item: (item["line"], item["kind"]))


def audit_module(path: Path, root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    parents = _parents(tree)
    core_names = _core_imports(tree)
    classes: dict[str, dict[str, Any]] = {}
    methods: dict[tuple[str, str], ast.AST] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            method_names = [
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes[node.name] = {
                "class": node.name,
                "line": node.lineno,
                "bases": [_name(base) for base in node.bases],
                "methods": method_names,
            }
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[(node.name, child.name)] = child

    controls: list[dict[str, Any]] = []
    connections: list[dict[str, Any]] = []
    popups: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        cls, method = _context(node, parents)
        name = _call_name(node)
        parent = parents.get(node)
        if name in UI_FACTORIES:
            record: dict[str, Any] = {
                "line": node.lineno,
                "class": cls,
                "method": method,
                "kind": name,
                "assigned_to": _assigned_name(parent),
                "arguments": [_literal(arg) for arg in node.args[:4]],
            }
            if name == "button":
                record["label"] = _literal(node.args[0] if node.args else None)
                action = node.args[1] if len(node.args) > 1 else next(
                    (kw.value for kw in node.keywords if kw.arg == "action"), None
                )
                record["action"] = _name(action)
            controls.append(record)

        if isinstance(node.func, ast.Attribute) and node.func.attr == "connect":
            signal_expr = _name(node.func.value)
            signal = signal_expr.rsplit(".", 1)[-1]
            if signal in SIGNALS or signal:
                connections.append(
                    {
                        "line": node.lineno,
                        "class": cls,
                        "method": method,
                        "signal": signal_expr,
                        "target": _name(node.args[0]) if node.args else "",
                    }
                )

        if name in POPUP_CALLS or (
            isinstance(node.func, ast.Attribute)
            and _name(node.func.value) in {"QMessageBox", "QFileDialog", "QInputDialog"}
        ):
            popups.append(
                {
                    "line": node.lineno,
                    "class": cls,
                    "method": method,
                    "kind": _name(node.func),
                    "title": _literal(node.args[1] if len(node.args) > 1 else None),
                }
            )

    method_records: list[dict[str, Any]] = []
    for (cls, method_name), node in sorted(methods.items()):
        method_records.append(
            {
                "class": cls,
                "method": method_name,
                "line": node.lineno,
                "backend_calls": _backend_calls(node, core_names),
                "validations": _validation_evidence(node),
            }
        )

    method_sets = {name: set(record["methods"]) for name, record in classes.items()}
    unresolved: list[dict[str, Any]] = []
    for record in [*connections, *controls]:
        target = str(record.get("target") or record.get("action") or "")
        cls = str(record.get("class") or "")
        if not target.startswith("self.") or not cls:
            continue
        token = target.split("(", 1)[0].split(".", 2)
        if len(token) == 2 and token[1] not in method_sets.get(cls, set()):
            unresolved.append(
                {
                    "line": record["line"],
                    "class": cls,
                    "target": target,
                    "reason": "self metoda není definována v dané třídě",
                }
            )

    return {
        "source": path.relative_to(root).as_posix(),
        "classes": sorted(classes.values(), key=lambda item: item["line"]),
        "controls": sorted(controls, key=lambda item: item["line"]),
        "connections": sorted(connections, key=lambda item: item["line"]),
        "popups": sorted(popups, key=lambda item: item["line"]),
        "methods": method_records,
        "unresolved": sorted(unresolved, key=lambda item: item["line"]),
    }


def audit_desktop(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).resolve()
    desktop = root_path / "kajovo" / "desktop"
    modules = [
        audit_module(path, root_path)
        for path in sorted(desktop.glob("*.py"))
        if path.name != "ui_audit.py"
    ]
    # Vazba na zděděnou metodu je platná pouze po dohledání skutečného předka.
    trees = {path.stem: ast.parse(path.read_text(encoding="utf-8"))
             for path in sorted(desktop.glob("*.py"))}
    known = {}
    for module_name, tree in trees.items():
        imports = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imports[alias.asname or alias.name] = (node.module or "", alias.name)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                known[(module_name, node.name)] = (node, imports)

    def resolves(module_name, class_name, method, visited=None):
        visited = set() if visited is None else visited
        identity = (module_name, class_name)
        if identity in visited:
            return False
        visited.add(identity)
        item = known.get(identity)
        if item is None:
            return False
        node, imports = item
        if any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method
               for child in node.body):
            return True
        for base in node.bases:
            if not isinstance(base, ast.Name):
                continue
            source, name = imports.get(base.id, (module_name, base.id))
            if source == "PySide6.QtWidgets":
                from PySide6 import QtWidgets
                qt_class = getattr(QtWidgets, name, None)
                if qt_class is not None and callable(getattr(qt_class, method, None)):
                    return True
            elif resolves(source.rsplit(".", 1)[-1], name, method, visited):
                return True
        return False

    for module in modules:
        module_name = Path(module["source"]).stem
        module["unresolved"] = [issue for issue in module["unresolved"]
                                if not resolves(module_name, issue["class"], issue["target"].split(".")[-1])]
    totals = {
        "modules": len(modules),
        "classes": sum(len(module["classes"]) for module in modules),
        "controls": sum(len(module["controls"]) for module in modules),
        "connections": sum(len(module["connections"]) for module in modules),
        "popups": sum(len(module["popups"]) for module in modules),
        "methods": sum(len(module["methods"]) for module in modules),
        "unresolved": sum(len(module["unresolved"]) for module in modules),
    }
    return {
        "schema_version": 2,
        "method": (
            "AST všech kajovo/desktop modulů: obrazovky, komponenty, ovládací prvky, "
            "signálové vazby, popupy, validace a dohledatelné backendové volání handlerů."
        ),
        "totals": totals,
        "modules": modules,
    }


def write_inventory(root: str | Path, destination: str | Path) -> dict[str, Any]:
    result = audit_desktop(root)
    path = Path(destination)
    if not path.is_absolute():
        path = Path(root) / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
