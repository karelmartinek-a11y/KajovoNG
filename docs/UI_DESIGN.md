# Návrh a smlouva desktopového rozhraní

## Inventura rozhraní

[Úplný inventář](UI_INVENTORY.json) zachycuje AST aktuálních modulů `kajovo/desktop`: třídy, metody, konstrukce ovládacích prvků a propojení signálů s odkazy do zdrojových souborů. Zahrnuje i pomocné konstrukce; nejde o počet současně viditelných polí.

## Informační architektura

| Sekce | Obsah a akce |
|---|---|
| Zadání | Projekt, GENERATE/MODIFY/QA/QFILE/KASKADA, LIVE/BATCH, model, prompt a výsledek, Maximum Quality, připojené zdroje, IN/OUT, návaznost response_id, teplota, modely A1/A2/A3, snapshot, diagnostika Windows/SSH, Nový/Uložit/Načíst/Spustit/Zastavit/ReRun |
| Kaskády | Knihovna definic, vytvoření/uložení/uložení pod jiným názvem/načtení, přidání/duplikace/odstranění/přesun kroků, model, teplota, instrukce, text/JSON vstup, soubory API i lokální, proměnné předchozích kroků, návaznost, text/JSON výstup, manifest/prompts/vlastní schéma, očekávané cesty a OUT |
| Zdroje | Soubory API: obnovit, nahrát, smazat vybrané/vše, připojit/odpojit; úložiště: vytvořit/smazat vybrané/vše, seznam souborů, přidání podle ID/z API, odebrání, podrobnosti a atributy JSON, připojit/odpojit |
| Dávky | Projekt, místní datum odeslání pracovní dávky, oddělený stav API a uložení do OUT, Dokončit, seznam/počty/poslední a příští kontrola, interval a konec sledování, stažení raw i souborového výstupu, částečné chyby, zrušení, opakování vybraných cest a oprava s připomínkou |
| Historie | Běhy, request/response, filtry běh/odpověď/datum/fulltext, detail, TXT/tisk, Dokončit u nepřevzatých BATCH, pokračování ReRun ostatních běhů; provozní log |
| Verze | Lokální adresář a Git, založení, stav, remote/push/pull, milníky vytvořit/obnovit/odstranit, odstranění repozitáře, strom, editor, porovnání s milníkem |
| Nastavení | Klíč zobrazit/uložit/smazat, výchozí model a teplota, bezpečnost vstupů, deny přípony/globy, timeouty API/BATCH, SMTP host/port/login/heslo/TLS/SSL/odesílatel/příjemce/uložit/test, SSH |
| Modely a nápověda | Katalog účtu, hledání a filtry schopností, výchozí a aktivní model, pevná matice, návody a vysvětlení režimů |

Aktuální UI neobsahuje workflow pro zkušební generativní Responses ani zkušební BATCH dávky. Historická preflight data ze starších LOGů mohou být čitelná kvůli zpětné kompatibilitě, ale nesmějí vytvářet aktivní akci, která by odeslala placenou zkoušku.

## Validační matice

API pravidla zůstávají centrálně v `core/request_rules.py`, `core/model_registry.py` a `core/response_policy.py`. `response_policy.py` je neplacená validační hranice: lokální kontrola plus ne-generativní čtení katalogu/metadat existujících prostředků. UI zobrazuje důvod lokálního zákazu a předává stejný snímek nastavení do backendu. Změna dostupnosti nesmí tiše vybrat jiný model.

| Volba / situace | Pravidlo a reakce |
|---|---|
| Model | Přesný identifikátor pevné matice a dostupnost v katalogu účtu; neznámý zůstává viditelný jako nedostupný |
| Režim × model × přílohy | [Matice modelů](MODEL_MATRIX.md), [matice požadavků](REQUEST_MATRIX.md), [1024 kombinací](REQUEST_COMBINATIONS.csv) |
| BATCH | Jen GENERATE/MODIFY; requirements, plán, struktura a případný quality gate běží LIVE; pouze A3/B3 soubory BATCH; před pracovní dávkou lokální validace, žádná zkušební dávka |
| Maximum Quality | Výchozí vypnuto; dostupné jen pro GENERATE/MODIFY v LIVE i BATCH; A2Q/B2Q a nejvyšší reasoning podporovaný maticí skutečného modelu kroku |
| Teplota | 0–2 pouze v podporovaném režimu modelu; jinak neposílat |
| response_id | Podpora návaznosti modelu; dostupné i pro živou přípravu GENERATE/MODIFY BATCH; samostatné souborové položky dávky návaznost nepoužívají |
| Files / vector stores | Validní ID, typ a velikost souborů, schopnosti modelu, pravidla file_search a BATCH; stav existujícího prostředku lze ověřit ne-generativním čtením, nikdy pomocným placeným Response |
| MODIFY | Existující IN pro LIVE i BATCH přípravu; zapisující režimy vyžadují OUT; souběžné zapisující běhy nesmějí mít překrývající se OUT |
| IN = OUT | OUT sleduje IN; snapshot a bezpečné cesty platí i při přepisu |
| Diagnostika | GENERATE/MODIFY BATCH ponechá IN a vypne i odznačí OUT; SSH vyžaduje spojení a případný pin; spuštění oprav vždy s existujícím potvrzením obsahu/cíle/hash |
| Kaskáda | Neprázdné kroky, přesné modely, JSON objekt/seznam vstupu, validní schéma, JSON výstup při schématu, bezpečné relativní očekávané cesty, proměnné pouze známých kroků |
| Síť / průběh | Operace ve workeru, neznámá doba jako neurčitý průběh; procenta jen z doložených jednotek; zavření nesmí zahodit běžící worker |
| Nastavení | Rozsahy podle `core/config.py`; SMTP TLS a SSL se vylučují; hesla nepatří do uloženého JSON |
| Git a zápis | Bezpečné cesty, ochrana vyloučených adresářů/tajemství, potvrzení destruktivních operací |

Podrobné řádky všech datových parametrů a rozsahů nových ovladačů jsou v [UI validační matici](UI_VALIDATION_MATRIX.csv). Sloupec druhu rozlišuje datový kontrakt a omezení ovladače; interní nastavení nemají vlastní přepínač. API kombinace se záměrně odkazují na jednu centrální matici, aby se pravidla nerozcházela.

Pod promptem je checkbox „Maximum Quality — maximální propracovanost“ s doprovodným textem: „Přidá nezávislou kontrolu návrhu před generováním souborů a použije nejvyšší úroveň reasoning, kterou zvolený model pro daný krok podporuje. Zvyšuje kvalitu, cenu a dobu běhu.“ Standard používá requirements a běžnou politiku reasoning; checkbox přidává kontrolní průchod a maximum podporované modelem. Nevznikají nové modelové comboboxy: A0R sdílí A1, A2Q sdílí A2, B fáze používají hlavní model MODIFY. Pro QA/QFILE/KASKÁDU se do běhu předává `maximum_quality=false`.

Uložení a načtení zadání zachovává Maximum Quality a přípravný snapshot. ReRun načte checkpoint vybraného běhu, ověří jeho integritu a obnoví fázi i kvalitu. Checkpoint A0R/B0R pokračuje plánem, A1/B1 strukturou, A2/B2 případným quality gate a A2Q/B2Q soubory. Pokud nový běh checkpoint nemá, obnova odstraní převzaté podklady a začne přípravu znovu. Změněné zadání, kvalita nebo poškozený checkpoint nesmí tiše použít starou specifikaci. Podrobnosti verze 1 `preparation_snapshot` a verze 2 dávkového manifestu jsou v [SSOT](SSOT.md).

## Vizuální návrh

Klidná světlá pracovní plocha s trvalou tmavou navigací. Jedna hlavní akce na obrazovce, běžné a pokročilé parametry oddělené. Konzistentní pole, jednotně zarovnané popisky, české názvy a viditelné jednotky. Tabulky mají čitelné hlavičky, výběr řádku a vodorovné posouvání. Obsah se posouvá svisle, hlavní navigace a přístup k aktivním běhům zůstávají dosažitelné.

## Oponentura z pohledu uživatele a zapracované požadavky

1. „Nevím, co nastavit jako první.“ Zadání začíná projektem, cílem, modelem a promptem. Pokročilé parametry jsou označené a neblokují čtení zadání.
3. „Nevím, co posílám.“ Připojené soubory a úložiště mají souhrn přímo u zadání a odkaz na správu zdrojů.
4. „Zavřel jsem průběh a nevím, zda běží.“ Aktivní běhy mají trvalý seznam a tlačítko znovu otevřít. Skrytí neznamená zastavení.
5. „V malém okně se nevejdou tlačítka.“ Hlavní akce zůstávají dostupné; formuláře mají posuv a žádné rozložení se po sestavení nepřestavuje přemísťováním starých prvků.
6. „Změna modelu mi přepíše volbu.“ Nedostupný uložený model se označí a zablokuje spuštění; náhradu volí uživatel.
7. „Dávka je hotová, ale nemám soubory.“ Stav API, stažení a ověření souborů jsou odlišné stavy s vlastním souhrnem chyb.
8. „Omylem jsem něco smazal.“ Mazání na serveru, odstranění Git, obnova milníku a spuštění oprav zachovávají potvrzovací dialogy.
9. „Nechci platit za automatickou zkoušku před skutečnou prací.“ UI ani backend nesmí automaticky spustit samostatný generativní test kompatibility; placený generativní požadavek vzniká pouze jako skutečná pracovní operace zvolená uživatelem.

## Dialogový kontrakt

Nové dialogy zachovají titul, účel, všechny informační položky, potvrzení/zrušení a technické podrobnosti zaznamenané v inventáři. Průběh běhu obsahuje stav/fázi, čas, ETA nebo její nedostupnost, stáří poslední aktivity, dvě úrovně průběhu, log, Stop, upozornění po dokončení a Skrytí. Hromadná operace obsahuje stav, počty, log a dostupné zavření až po dokončení. Upload obsahuje aktuální soubor, počet/průběh, log a kooperativní zrušení; ESC/X neruší životnost workeru.

Příprava GENERATE zobrazuje „Profesionální requirements“, „Architektonický plán“ a „Implementační struktura“; MODIFY „Change requirements“, „Plán změny“ a „Implementační struktura změny“. „Quality gate“ se objeví pouze při Maximum Quality. Technický A0 pro dlouhé zadání je samostatná operace. Následuje skutečné generování A3/B3 nebo odeslání souborových úloh, čekání na dávku, import/validace a zápis. Předání do Batch ani stav API `completed` nesmí zobrazovat úspěšné uložení souborů; import má vlastní výsledek včetně částečných chyb a dry-run. „Soubory kompletní, funkčnost neověřena“ neznamená ověřenou funkčnost kódu.

## Ověření implementace

Funkční inventář se porovná s novými akcemi a testy. Skript `scripts/render_ui.py` pořizuje snímky hlavních sekcí a druhů dialogů ze skutečně vykresleného Qt rozhraní, v běžném i menším okně. Kontrola zahrnuje ořez, kontrast, jednotky, tab pořadí, prázdné/chybové stavy a dostupnost hlavních akcí. Backendové testy, desktopové testy, Ruff, zákaz placené preflight vrstvy a konzistence závislostí jsou povinné před zápisem na main.

## Implementační mapa

| Oblast inventáře | Nový modul |
|---|---|
| Hlavní okno, navigace, běhy, ReRun, modely | `desktop/application.py`, `desktop/recovery.py` |
| Soubory a vector stores | `desktop/resources.py` |
| Kaskády | `desktop/cascades.py` |
| Dávky a bezpečný import | `desktop/batches.py` |
| Request/response historie | `desktop/history.py` |
| Git a editor | `desktop/versions.py` |
| API klíč, provoz, bezpečnost, SMTP | `desktop/settings.py` |
| Průběhy, potvrzení, souborové a textové dialogy | `desktop/dialogs.py` |
| Samostatná okna, výběr modelu, splash | `desktop/windows.py` |
| Vizuální prvky a asynchronní úlohy | `desktop/design.py`, `desktop/jobs.py` |

Původní moduly nejsou importovány ani zachovány jako záložní cesta. Backendové kontrakty API, souborů a běhů zůstávají společné. Snímkovací skript pokrývá také aktivní, dokončený, zastavený a chybový průběh, detail, samostatné sekce a výběr souboru. Vizuální kontrola se zaměřuje na dostupnost patiček při 150% škálování; navigace a dlouhé formuláře používají posuv. Nativní systémové dialogy vyžadují ověření na Windows.

## Snímky rozhraní

Skutečně vykreslené Qt rozhraní s izolovanými ukázkovými daty. Zadání je z plochy 1366 × 900; dialogy z logické plochy 911 × 480 při 150% škálování.

![Zadání](ui/zadani.png)

![Průběh na malé ploše](ui/prubeh.png)
