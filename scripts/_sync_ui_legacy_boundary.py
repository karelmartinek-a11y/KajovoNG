"""Jednorázová synchronizace UI dokumentace s produkční hranicí legacy desktopu."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "UI_DESIGN.md"
SELF = ROOT / "scripts" / "_sync_ui_legacy_boundary.py"
WORKFLOW = ROOT / ".github" / "workflows" / "sync-ui-legacy-boundary.yml"

OLD = (
    "Produkční sestavu vytváří `kajovo.studio.application.create_window`; stejnou továrnu používají testy a "
    "snímkovací nástroj. Rozhraní neimportuje `kajovo.desktop`. Starší implementace zůstává pro regresní "
    "porovnání, není záložní cestou spouštěče. Samostatný převodník používá Qt a společné komponenty."
)
NEW = (
    "Produkční sestavu vytváří `kajovo.studio.application.create_window`; stejnou továrnu používají testy a "
    "snímkovací nástroj. Rozhraní neimportuje `kajovo.desktop`. Starší implementace zůstává pouze jako zdrojový "
    "regresní referenční materiál: není záložní cestou spouštěče ani součástí instalovaného/distribuovaného "
    "balíku. Kompatibilní `scripts/render_ui.py` deleguje na produkční `scripts/render_studio.py`, takže snímkování "
    "ověřuje skutečné Studio. Samostatný převodník používá Qt a společné komponenty."
)


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        raise SystemExit(f"UI dokumentace se změnila; očekáván 1 kontraktní odstavec, nalezeno {text.count(OLD)}.")
    DOC.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    SELF.unlink()
    WORKFLOW.unlink()


if __name__ == "__main__":
    main()
