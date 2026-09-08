# Pravidla repozitáře

Kanonickou specifikací je `docs/SSOT.md`. Ověřujte chování ve zdrojovém kódu a testech; dokumentace nenahrazuje důkaz funkčnosti.

## Struktura

- `kajovo/app`: vstupní body aplikace.
- `kajovo/core`: orchestrace, API, kontrakty, bezpečnost, logy a účtenky.
- `kajovo/ui`: desktopové rozhraní PySide6.
- `utf8nobom`: pomocný převodník textů se zálohou.
- `tests`: automatické regresní a desktopové testy.
- `scripts`, `Build`: instalace, spuštění a sestavení.

## Změny a ověření

Zachovávejte uživatelské změny. Nenahrávejte klíče, runtime logy, databáze ani generované binární balíčky. Placená API volání nespouštějte jako součást běžných testů.

Po změně spusťte `python -m pytest -q`, `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build` a `python -m pip check`. Nově nalezenou chybu zajistěte regresním testem. Změny kontraktů promítněte do SSOT.

Komunikace, dokumentace a nové komentáře jsou v češtině. Textové soubory používejte v UTF-8 bez BOM; Python má čtyřmezerné odsazení.
