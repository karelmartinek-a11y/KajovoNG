# Pravidla repozitáře

Kanonickou specifikací je `docs/SSOT.md`. Ověřujte chování ve zdrojovém kódu a testech; dokumentace nenahrazuje důkaz funkčnosti.

## Struktura

- `kajovo/app`: vstupní body aplikace.
- `kajovong`: modulový a konzolový spouštěč.
- `kajovo/core`: orchestrace, API, kontrakty, bezpečnost, logy a účtenky.
- `kajovo/desktop`: desktopové rozhraní PySide6.
- `utf8nobom`: pomocný převodník textů se zálohou.
- `tests`: automatické regresní a desktopové testy.
- `scripts`, `Build`: instalace, spuštění a sestavení.

## Změny a ověření

Kanonický uživatelský spouštěč Windows je kořenový `start.bat`. Musí pracovat z kořene repozitáře, vytvořit chybějící `.venv` pomocí Pythonu 3.12+, ověřit provozní závislosti z `pyproject.toml` včetně jejich verzí a tranzitivní konzistence a při potřebě je doinstalovat. Aplikaci spouští pouze po úspěšném ověření, výhradně interpretem `.venv`. Funkční prostředí při běžném startu nevyžaduje instalaci ani síť. Existující neplatné prostředí se automaticky nemaže ani nepřepisuje. Tento kontrakt zachovávejte při změnách spouštění a závislostí.

Zachovávejte uživatelské změny. Nenahrávejte klíče, runtime logy, databáze ani generované binární balíčky. Placená API volání nespouštějte jako součást běžných testů.

Po změně spusťte `python -m pytest -q`, `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build` a `python -m pip check`. Nově nalezenou chybu zajistěte regresním testem. Změny kontraktů promítněte do SSOT.

## Kontrakty odpovědí a jejich návaznosti

Každé dosažitelné API volání, jeho kontraktová varianta a předávka vyžaduje konkrétní kontrakt a runtime validaci před použitím. Platí to i pro chyby, refusal, neúplný výsledek, timeout, opakování, fallback, obnovu a ne-JSON odpovědi. Transportní obálku a doménový výsledek ověřujte odděleně.

Nepoužívejte `{}`, `true`, objekt bez popsaného obsahu, pole bez přesných položek, neomezené `additionalProperties`, `Any` ani jejich přejmenované obdoby jako masku odpovědi nebo předávaných doménových dat. Strukturovaná data se nesmí schovat do textu. Pevné objekty musí odmítat nepopsaná pole; všechny reference a alternativy musí vést na konkrétní definice. Dynamické klíče vyžadují doložený doménový význam, omezení klíčů a přesný rekurzivní typ hodnot. Předem známé klíče určují masku konkrétního volání. Chybná maska nebo odpověď zastaví krok; nesmí aktivovat obecný fallback ani vymyšlenou výchozí hodnotu.

Udržujte `docs/response-contract-inventory.json` a skutečné sestavené masky v `docs/response-runtime-schemas.json`. Zachovávejte stabilní ID míst volání, podmínky dosažitelnosti, všechny příjemce, mapování polí a testové důkazy. AST inventura a otisky nenahrazují úplné čtení ani sémantický audit. Změna zdroje, registru, volání nebo větve vyžaduje aktualizaci důkazů; pouhé přepsání otisku není audit.

Spusťte také `python tools/verify_response_contracts.py`, `python tools/verify_response_contracts.py --require-complete` a regresní testy `test_response_schema_exactness.py`, `test_response_handoff_exactness.py`, `test_native_provider_contracts.py`, `test_response_inventory.py` a `test_smtp_response_contracts.py`. Přísný průchod musí odmítnout neověřené položky inventáře. Celkový PASS nevydávejte při zbývajícím nepokrytém volání, neurčené variantě nebo neověřené předávce, ani když ostatní testy prošly. Rozsah a otevřené položky popisuje `docs/RESPONSE_CONTRACTS.md`.

Používejte projektové prostředí `.venv` s Pythonem 3.12 nebo novějším. Na Windows lze příkazy spustit přes `.venv\Scripts\python.exe`; systémový `python` nemusí splňovat požadavky projektu. Testy musí nahrazovat síťové služby a pracovat s dočasnými soubory, nikoli s provozními daty a přihlašovacími údaji.

Testy členěte podle ověřované oblasti. Užitečné regresní testy zachovávejte bez ohledu na jejich stáří; odstraňujte pouze testy nesouvisející s aplikací nebo s prokazatelně neplatným kontraktem. Dokumentace a komentáře popisují platné chování, účel a omezení, nikoli historii oprav. Výsledky konkrétního ověření uvádějte v předání změny; nevytvářejte v repozitáři datované auditní zprávy. README popisuje obsluhu, `Build/README.md` sestavení a SSOT systémové kontrakty.

Generované adresáře `Build/lib`, `Build/Kajovo`, `Build/bdist.*` a `dist` nejsou zdrojový kód. Při prohledávání a úpravách je vynechávejte. Před odstraněním adresáře ověřte jeho absolutní cestu a obsah; nemažte plošně ignorované soubory ani prostředí `.venv`.

Komunikace, dokumentace a nové komentáře jsou v češtině. Textové soubory používejte v UTF-8 bez BOM; Python má čtyřmezerné odsazení.
