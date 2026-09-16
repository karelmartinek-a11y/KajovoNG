"""Jednorázový deterministický codemod pro R04 první krok.

Skript smí uspět pouze nad přesně rozpoznaným legacy blokem. Po aplikaci se
spolu s dočasným workflow odstraní, aby v produkčním stromu nezůstal migrační
runtime.
"""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "kajovo" / "core" / "pipeline.py"
SELF = ROOT / "scripts" / "_refactor_pipeline.py"
WORKFLOW = ROOT / ".github" / "workflows" / "refactor-codemod.yml"


def main() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    pattern = re.compile(
        r"\n@dataclass\nclass UiRunConfig:\n.*?\n\nclass RunWorker\(QThread\):",
        re.DOTALL,
    )
    replacement = "\nfrom .runs.config import UiRunConfig\n\n\nclass RunWorker(QThread):"
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Refaktor odmítnut: očekáván 1 UiRunConfig blok, nalezeno {count}.")

    if "@dataclass" not in updated:
        updated = updated.replace("from dataclasses import dataclass\n", "")

    if updated == text:
        raise SystemExit("Refaktor odmítnut: pipeline se nezměnila.")
    if "from .runs.config import UiRunConfig" not in updated:
        raise SystemExit("Refaktor odmítnut: chybí nový UiRunConfig import.")

    PIPELINE.write_text(updated, encoding="utf-8", newline="\n")

    # Dočasný migrační mechanismus nesmí zůstat součástí výsledného stromu.
    SELF.unlink()
    WORKFLOW.unlink()


if __name__ == "__main__":
    main()
