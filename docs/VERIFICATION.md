# Ověření repozitáře — 9. září 2026

## Výsledek a rozsah

Opravy vznikají přímo na `main`. Podkladem je čtení implementace, regresní testy, kontrola API kontraktů, sestavení a desktopové vykreslení. Původní auditní tvrzení nejsou důkazem správnosti. Kanonické chování popisuje [SSOT](SSOT.md).

Poslední lokální sada má **75 úspěšných testů a 10 podtestů**. Statické kontroly F/B/E9, kontrola konzistence závislostí a kontrola rozdílů Git jsou čisté. Audit nainstalovaných Python závislostí nehlásí známou zranitelnost. Lokální projekt samotný se v databázi PyPI neaudituje; jeho kontrolou je tento zdrojový audit a testy.

Tento výsledek není důkazem absence všech chyb ani certifikací bezpečnosti. Živé OpenAI, SMTP, vzdálený SSH server a sestavení macOS nejsou součástí provedeného runtime ověření. Nebyl vytvořen API klíč ani proveden placený OpenAI požadavek.

## Opravené skupiny nálezů

| Oblast | Nález a náprava |
| --- | --- |
| Repozitář | Odstraněny neplatné testy cizí aplikace, neaktuální specifikace a diagramy, nefunkční staré vstupní body, pomocné skripty a historické zdrojové ZIPy. Odstraněné verzované soubory jsou obnovitelné z Git historie. |
| IN upload | Archiv bez bezpečnostních filtrů nahrazen omezeným textovým balíčkem ze skenu; kontrola citlivých souborů, junctions, symlinků a skutečně načtených hashů. |
| Cesty a výstupy | Validace celého manifestu, duplicit/case kolizí, nadřazených souborů a Windows zařízení; atomické nahrazení jednotlivých souborů. |
| Generování | Neplatný JSON, chybná cesta nebo neplatná návaznost chunků nemohou být vykázané jako úspěšný prázdný výstup. QFILE vyžaduje úplný jediný chunk a textový obsah. |
| Batch | Opraveny nepovolené JSONL atributy, bezpečnost kořene výstupu, chybové zápisy, duplicity/neúplnost chunků, načítání textu a evidence získaných response ID. |
| Vlastní kaskády | Úplná lokální JSON Schema validace, zákaz síťových referencí, kontrola očekávaných souborů před zápisem, jednotné run ID a účtenky kroků. |
| HTTP a retry | Správný multipart Content-Type, převinutí streamu, respektování timeoutu, úplné stránkování, správná metoda aktualizace atributů, žádné druhé SDK→REST provedení mutace, oprava circuit breakeru. |
| Evidence | Účtenky všech mezikroků, skutečný model odpovědi, ochrana před duplicitním započtením response ID, oddělené odpovědi jedné dávky. Lokální chyba účtenky neopakuje placený dotaz. |
| Ceny | Opraveny tisícinásobně chybné jednotky fallbacku a sazba file search podle volání; validace cen, verze cache, konzervativní ověření a odstranění skrytých placených cenových dotazů. |
| Hesla | Odstraněn zastaralý environment fallback při mazání, omezení ztráty při migraci, klíč se neobjevuje v argumentech procesu setx. |
| SSH a Windows | Správný SHA-256 pin, uzavření SSH spojení při chybě, snímek pin politiky běhu, distribuovaný lokální kolektor, timeout a kontrola návratového kódu. |
| Qt lifecycle | Odstraněno násilné ukončování workerů a kolize vlastních signálů s QThread.finished; okno čeká na aktivní operace. |
| Souběh | Dokončení používá svůj OUT/projekt/politiku, překrývající se zapisující běhy jsou blokované, výchozí a běhová teplota mají oddělené ovladače. |
| Logy a ReRun | Kolize běhu či zkráceného názvu nepřepisuje důkazy; ReRun nezaměňuje navrženou odpověď za uložený soubor. |
| Desktop a Git | Opraveny importy, Qt6 tisk, datum filtru, progress parser, přepínání obalených záložek, malé obrazovky, binární soubory v editoru a nepovolená mutace remote při refresh. Pull používá aktivní větev a fast-forward. |
| UTF-8 převodník | Zálohy se nepřepisují, vstupy se deduplikují, Git a odkazy se nekonvertují, ZIP uchovává metadata a traversal se odmítá, běžné soubory se zapisují atomicky. |
| Distribuce a CI | Jediný zdroj závislostí, aktualizované vývojové nástroje, diagnostický skript a prostředky v distribuci, kontroly návratových kódů instalace/build, pytest a LFS v CI. |
| Dokumentace | SSOT, provozní návody a nápověda odpovídají skutečným funkcím; nefunkční šifrování není prezentováno jako ochrana dat. Kódování zdrojových textů se testuje. |

## Provedené kontroly

| Kontrola | Důkaz |
| --- | --- |
| Python | Windows, CPython 3.13.0, PySide6 6.11.2, OpenAI SDK 3.9.0 |
| Regrese a integrace | `python -m pytest -q`: 75 passed, 10 subtests passed |
| Statická analýza | `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build`: bez nálezu |
| Konzistence instalace | `python -m pip check`: bez konfliktů |
| Známé zranitelnosti balíčků | `python -m pip_audit --progress-spinner off`: žádný nález v nainstalovaných závislostech |
| Hlavní toky | Celé offline QA, QFILE, GENERATE, MODIFY, kaskáda a vytvoření batch, skutečné lokální soubory/SQLite |
| GUI | Vykresleno a vizuálně zkontrolováno všech 12 záložek, test výšky okna 700 px |
| Diagnostika | Lokální Windows kolektor vytvořil JSON a vlastní log; SSH pin a cleanup ověřeny náhradou klienta |
| Wheel | Sestaven, instalován do izolovaného cíle, ověřeny importy, font a diagnostický skript |
| Windows EXE | PyInstaller sestavil distribuci; izolovaný offscreen proces nastartoval bez stderr chyby. Testovací proces byl následně explicitně ukončen; nejde o interaktivní end-to-end test EXE. |
| Text a prostředky | UTF-8 bez BOM, skutečné načtení fontů a PNG, `git diff --check` |

## Zbývající hranice a heuristická upozornění

Širší heuristická kontrola Ruff S není čistá: hlásí především záchyty výjimek při volitelných operacích, spouštění lokálních programů, sestavení seznamu SQL placeholderů a nekryptografický jitter retry. Nejsou plošně potlačeny ani vydávány za 128 potvrzených zranitelností. SQL hodnoty se předávají parametrizovaně; jitter nechrání tajemství; spouštění oprav vyžaduje potvrzení. Záchyty výjimek a synchronní desktopové operace zůstávají oblastí pro provozní sledování.

PyInstaller hlásí nepřítomné volitelné `tzdata`; aplikace nepoužívá pojmenované IANA zóny. Offscreen Qt upozorňuje na nepřítomný vlastní adresář výchozích fontů; oba distribuované fonty se v testu úspěšně načítají.

Logy nejsou šifrované, detekce tajných údajů je heuristická, vícesouborový zápis není jedna transakce a odhad ceny nenahrazuje kompletní vyúčtování účtu. Síťové retry nemůže garantovat přesně jedno provedení operace při ztracené odpovědi. Bez provozních vstupů a účtů nelze tyto hranice nahradit tvrzením o absolutně bezchybném systému.
