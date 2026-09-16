"""Jednorázová synchronizace normativní dokumentace s refaktorem."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SSOT = ROOT / "docs" / "SSOT.md"
README = ROOT / "README.md"
SELF = ROOT / "scripts" / "_refactor_docs.py"
WORKFLOW = ROOT / ".github" / "workflows" / "refactor-doc-codemod.yml"


def replace_once(text: str, pattern: re.Pattern[str], replacement: str, label: str) -> str:
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit(f"Dokumentační refaktor odmítnut: {label}, nalezeno {count} shod.")
    return updated


def update_ssot() -> None:
    text = SSOT.read_text(encoding="utf-8")
    text = replace_once(
        text,
        re.compile(
            r"API klíč se před inicializací API panelů.*?klíč se nepředává programu `setx` jako argument procesu\.\n",
            re.DOTALL,
        ),
        "API klíč se před inicializací API panelů načítá primárně z OS credential storage přes `keyring`. "
        "Uložený credential má přednost před zděděnou proměnnou procesu. Starý Windows záznam "
        "`HKCU\\\\Environment\\\\OPENAI_API_KEY` slouží pouze jako migrační zdroj: aplikace jej načte, "
        "zapíše do credential storage, provede readback a teprve po úspěšném ověření legacy hodnotu odstraní. "
        "Při neúspěšném zápisu nebo readbacku legacy záznam zůstává zachován. Pokud credential storage není "
        "dostupné, může aplikace použít legacy záznam nebo explicitní runtime `OPENAI_API_KEY`; tato fallback "
        "cesta sama legacy hodnotu nemaže. Explicitní smazání se persistuje jako interní stav prázdného klíče, "
        "aby se nemohl znovu aktivovat stale klíč zděděný od parent procesu. Načtená hodnota sjednotí prostředí "
        "aktuálního procesu i jednotlivé panely. Uložení vždy provádí write + readback a při selhání se pokusí "
        "obnovit předchozí credential. Restart Windows ani rodičovského terminálu není nutný. Klíč se nezapisuje "
        "do settings JSON, LOG ani Run Bundle.\n",
        "API key storage paragraph",
    )
    row = "| Běžné běhy | `pipeline.py` | GENERATE, MODIFY, QA, QFILE, batch, přílohy a výstupy |"
    replacement = (
        "| Běžné běhy | `pipeline.py`, `runs/*` | Qt worker/adaptér a postupně oddělované Qt-nezávislé "
        "kontrakty běhu, polling a bezpečný delivery pro GENERATE, MODIFY, QA a QFILE |"
    )
    if text.count(row) != 1:
        raise SystemExit(f"Dokumentační refaktor odmítnut: architecture row, nalezeno {text.count(row)} shod.")
    text = text.replace(row, replacement, 1)
    SSOT.write_text(text, encoding="utf-8", newline="\n")


def update_readme() -> None:
    text = README.read_text(encoding="utf-8")
    text = replace_once(
        text,
        re.compile(
            r"Na Windows volba „Uložit“ zachová klíč.*?starého klíče z prostředí\.",
            re.DOTALL,
        ),
        "Volba „Uložit“ zachová API klíč v OS credential storage přes `keyring`; uložený credential má "
        "přednost před proměnnou prostředí terminálu a restart Windows není nutný. Starší Windows uložení v "
        "`HKCU\\Environment\\OPENAI_API_KEY` se při prvním načtení bezpečně migruje až po ověřeném readbacku. "
        "Volba smazání zabrání i opětovnému načtení stale klíče z prostředí.",
        "README API key paragraph",
    )
    README.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    update_ssot()
    update_readme()
    SELF.unlink()
    WORKFLOW.unlink()


if __name__ == "__main__":
    main()
