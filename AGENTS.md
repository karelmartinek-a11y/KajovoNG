# Pravidla repozitáře

Kanonickou specifikací je `docs/SSOT.md`. Ověřujte chování ve zdrojovém kódu a testech; dokumentace nenahrazuje důkaz funkčnosti.

## Struktura

- `kajovo/app`: vstupní body aplikace.
- `kajovong`: modulový a konzolový spouštěč.
- `kajovo/core`: orchestrace, API, kontrakty, bezpečnost, logy a účtenky.
- `kajovo/ui`: desktopové rozhraní PySide6.
- `utf8nobom`: pomocný převodník textů se zálohou.
- `tests`: automatické regresní a desktopové testy.
- `scripts`, `Build`: instalace, spuštění a sestavení.

## Změny a ověření

Kanonický uživatelský spouštěč Windows je kořenový `start.bat`. Musí pracovat z kořene repozitáře, vytvořit chybějící `.venv` pomocí Pythonu 3.12+, ověřit provozní závislosti z `pyproject.toml` včetně jejich verzí a tranzitivní konzistence a při potřebě je doinstalovat. Aplikaci spouští pouze po úspěšném ověření, výhradně interpretem `.venv`. Funkční prostředí při běžném startu nevyžaduje instalaci ani síť. Existující neplatné prostředí se automaticky nemaže ani nepřepisuje. Tento kontrakt zachovávejte při změnách spouštění a závislostí.

Zachovávejte uživatelské změny. Nenahrávejte klíče, runtime logy, databáze ani generované binární balíčky. Placená API volání nespouštějte jako součást běžných testů.

Po změně spusťte `python -m pytest -q`, `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build` a `python -m pip check`. Nově nalezenou chybu zajistěte regresním testem. Změny kontraktů promítněte do SSOT.

Používejte projektové prostředí `.venv` s Pythonem 3.12 nebo novějším. Na Windows lze příkazy spustit přes `.venv\Scripts\python.exe`; systémový `python` nemusí splňovat požadavky projektu. Testy musí nahrazovat síťové služby a pracovat s dočasnými soubory, nikoli s provozními daty a přihlašovacími údaji.

Testy členěte podle ověřované oblasti. Užitečné regresní testy zachovávejte bez ohledu na jejich stáří; odstraňujte pouze testy nesouvisející s aplikací nebo s prokazatelně neplatným kontraktem. Dokumentace a komentáře popisují platné chování, účel a omezení, nikoli historii oprav. Výsledky konkrétního ověření uvádějte v předání změny; nevytvářejte v repozitáři datované auditní zprávy. README popisuje obsluhu, `Build/README.md` sestavení a SSOT systémové kontrakty.

Generované adresáře `Build/lib`, `Build/Kajovo`, `Build/bdist.*` a `dist` nejsou zdrojový kód. Při prohledávání a úpravách je vynechávejte. Před odstraněním adresáře ověřte jeho absolutní cestu a obsah; nemažte plošně ignorované soubory ani prostředí `.venv`.

Komunikace, dokumentace a nové komentáře jsou v češtině. Textové soubory používejte v UTF-8 bez BOM; Python má čtyřmezerné odsazení.
