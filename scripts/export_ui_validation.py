"""Odvodí inventář UI ze současných tříd a signálů, nikoli ze starých cest."""
from __future__ import annotations

import ast
import csv
import io
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kajovo.studio.ui_audit import audit_studio


def render(root=ROOT):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(["druh", "parametr", "výchozí hodnota / možnosti", "pravidlo", "zdroj"])
    path = root / "kajovo/core/runs/config.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "UiRunConfig")
    for node in cls.body:
        if isinstance(node, ast.AnnAssign):
            writer.writerow(["datový parametr", "UiRunConfig." + node.target.id,
                             ast.unparse(node.value) if node.value else "povinný",
                             "Typ " + ast.unparse(node.annotation) + "; sémantika viz SSOT a validate_run_options",
                             "kajovo/core/runs/config.py:" + str(node.lineno)])
    for module in audit_studio(root)["modules"]:
        for control in module["controls"]:
            writer.writerow(["ovladač", control["class"] + "." + control["assigned_to"],
                             control["kind"], "Statická konstrukce; nedokazuje funkční obsluhu",
                             module["source"] + ":" + str(control["line"])])
        for connection in module["connections"]:
            writer.writerow(["signál", connection["class"] + "." + connection["signal"],
                             connection["target"], "Explicitní vazba signálu; chování pokrývají testy oblasti",
                             module["source"] + ":" + str(connection["line"])])
    return stream.getvalue()


if __name__ == "__main__":
    (ROOT / "docs/UI_VALIDATION_MATRIX.csv").write_text(render(), encoding="utf-8", newline="")
