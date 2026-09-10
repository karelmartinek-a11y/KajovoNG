# KájovoNG — specifikace systému

## Účel a rozsah

KájovoNG je desktopový klient OpenAI Responses API pro vytváření a úpravy textových souborů, dotazy, uživatelské kaskády a dávkové požadavky. Zahrnuje správu vzdálených souborů a vector stores, evidenci běhů a nákladů, Git, SMTP a diagnostiku Windows a SSH. Součástí projektu je samostatný převodník textů `utf8nobom`.

Tento dokument je kanonickou technickou specifikací. [README](../README.md) popisuje spuštění a [návod sestavení](../Build/README.md) distribuci. Chování ověřují zdrojový kód a automatické testy. Změny kontraktů se promítají do specifikace i odpovídajících testů.

## Prostředí a spuštění

Projekt vyžaduje Python 3.12 nebo novější. Závislosti a jejich povolené verze určuje [pyproject.toml](../pyproject.toml): PySide6, OpenAI SDK, requests, paramiko, keyring, beautifulsoup4 a jsonschema. Vývojové nástroje jsou v extra `dev`. `requirements.txt` instaluje projekt, `requirements-dev.txt` projekt s vývojovými závislostmi.

Vstupní body `kajovong`, `python -m kajovong` a `python -m kajovo.app.main` volají společný spouštěč. Ten vytvoří QApplication, načte písma, ikonu a nastavení a zobrazí maximalizované hlavní okno. Chybějící generovanou ikonu nahrazuje vložená ikona. Prostředky se vyhledávají ve zdrojovém stromu, instalaci nebo balíčku PyInstaller.

Instalační a spouštěcí skripty Windows pracují z kořene projektu a používají `.venv`. Při přímém modulovém spuštění určuje relativní umístění provozních dat aktuální pracovní adresář procesu. Absolutní cesty nastavení zůstávají absolutní.

Kanonickým uživatelským spouštěčem Windows je kořenový `start.bat`, který volá `scripts/start.ps1` a `scripts/start_app.py`. Nezávisle na aktuálním adresáři používá kořen repozitáře a přímo `.venv/Scripts/python.exe`, bez aktivace prostředí. Chybějící `.venv` vytvoří dostupným Pythonem 3.12 nebo novějším. Existující neúplné prostředí nebo prostředí s nevyhovujícím Pythonem nepřepisuje; oznámí chybu. Python samotný se automaticky neinstaluje.

Před každým spuštěním ověří pip (chybějící doplní přes `ensurepip`), přítomnost a povolené verze všech provozních závislostí podle aktuálního `pyproject.toml` a tranzitivní konzistenci pomocí `pip check`. Při nesplnění instaluje provozní požadavky přes pip a kontrolu zopakuje. Neinstaluje extra `dev` a bez potřeby neaktualizuje balíčky. Vyhovující prostředí ověří bez sítě; doplnění balíčků může potřebovat internet. Nevyřešený konflikt, nedostupné balíčky nebo jiná chyba zabrání spuštění aplikace a vrátí nenulový kód; `start.bat` ponechá chybovou zprávu viditelnou do stisku klávesy. Úspěšná kontrola spustí `kajovo.app.main` ze zdrojového stromu. `start.bat -CheckOnly` provede přípravu a ověření bez otevření UI.

## Architektura

| Oblast | Zdrojové moduly | Odpovědnost |
| --- | --- | --- |
| Spuštění | `kajovo/app`, `kajovong` | Inicializace a společný vstupní bod |
| Běžné běhy | `pipeline.py` | GENERATE, MODIFY, QA, QFILE, batch, přílohy a výstupy |
| Vlastní kaskády | `cascade_types.py`, `cascade_pipeline.py`, `cascade_log.py` | Kroky, substituce, schémata, soubory a evidence |
| API | `openai_client.py`, `retry.py`, `compat.py`, `model_capabilities.py` | SDK/REST, chyby, stránkování, opakování a schopnosti modelů |
| Souborové hranice | `contracts.py`, `filescan.py`, `utils.py` | Kontrakty, sken vstupu, validace cest, hashe a zápisy |
| Evidence a ceny | `runlog.py`, `receipt.py`, `pricing.py`, `cost_accounting.py`, `price_sources.py`, `batch_costs.py`, `pricing_audit.py` | Logy, SQLite účtenky, ceník a doplnění evidence |
| Nastavení | `config.py`, `secret_store.py`, `resources.py` | Konfigurace, hesla a prostředky |
| Diagnostika | `diagnostics/windows.py`, `diagnostics/ssh.py`, `notifications.py` | Sběr diagnostiky a SMTP |
| Desktop | `kajovo/ui` | Hlavní okno, panely, dialogy a workery Qt |
| Převod textů | `utf8nobom/app.py`, `utf8nobom/py.py` | Tk rozhraní, zálohy a převod souborů i položek ZIP |

Názvy modulů bez adresáře označují soubory pod `kajovo/core`.

## Uživatelské rozhraní

Hlavní okno má dvanáct záložek: RUN, FILES API, KASKÁDA, VECTOR STORES, SETTINGS, SMTP, MODELS, BATCH, GITHUB, PRICING, REQUEST/RESPONSE a HELP. RUN odděluje zadání a výsledek, parametry a adresáře, přílohy, diagnostiku a provozní detail. Ovládání spuštění a zastavení leží mimo obsah těchto sekcí. Teplota aktuálního běhu a výchozí teplota nastavení mají samostatné ovladače.

Rozhraní používá společné tmavé téma a Montserrat 10 bodů. Cílové plochy jsou 1366×768 a 1920×1080 při škálování 100 %, 125 % a 150 %. Rozměry se vztahují k dostupné ploše monitoru po odečtení systémových okrajů. Pod šířkou panelu 1200 logických pixelů se editor kaskády a vector stores přepínají po sekcích. Řádky nástrojů se podle dostupného místa zalamují. Tabulky, textové editory a rozsáhlá nastavení dovolují posouvání; hlavní akce RUN zůstávají viditelné. Dialogy mají dostupnou patičku a samostatně posuvný obsah. Ceník a účtenky mají vlastní sekce; finanční souhrn nabízí přepnutí na technické podklady.

`ProgressEvent` přenáší etapu, stav, dokončený počet, celkový počet, jednotku, detail a monotónní čas události. Text logu ani pevně vážená procenta neurčují dokončení. A3/B3 započítávají soubor po získání a ověření celého obsahu; zápis na disk je samostatná etapa. Kaskáda započítává dokončené kroky. API bez měřitelného postupu používá neurčitý indikátor. Trvání a stáří poslední události se obnovují každou sekundu; tato obnova nedokazuje aktivitu poskytovatele. ETA vzniká po třech dokončených srovnatelných jednotkách z mediánu posledních pěti dob, pouze pro aktuální etapu. Čekání na potvrzení ceny nezvyšuje měřenou dobu jednotky. Změna etapy vzorky resetuje. Ukončení, chyba, zrušení a předání do Batch mají odlišné stavy; koncové indikátory již neanimují práci.

BATCH zobrazuje počet zpracovaných úloh včetně chyb a čas posledního úspěšného ověření i další naplánované kontroly. ETA vzdálené fronty není odhadována. Dokončení API není potvrzením zápisu do OUT. Uploadový dialog povolí zavření po zrušení až po potvrzení konce workerem.

Síťové operace panelů, jejich opakování, Git příkazy a SMTP test se vykonávají mimo GUI vlákno. Synchronní UI adaptér během čekání obsluhuje vykreslení a časovače a nepřijímá další uživatelský vstup; dlouhé rušitelné úlohy používají vlastní workery. Zobrazené průběžné textové logy mají limit 2000 bloků; uložená provozní evidence tím není omezena.

FILES API spravuje vzdálené soubory; VECTOR STORES úložiště a jejich přílohy. Odpojení souboru od úložiště a odstranění z Files API jsou odlišné operace. MODELS filtruje modely podle pevné matice a zobrazuje její pravidla. REQUEST/RESPONSE zobrazuje uložené požadavky a odpovědi. PRICING spravuje ceník a evidenci nákladů.

GITHUB pracuje s lokálním repozitářem a příkazy Git. Obnovení stavu nepřepisuje remote. Zápis, commit, synchronizaci a změnu remote vyvolávají příslušné uživatelské akce. Pull používá aktuální větev a fast-forward. Příkazy mají časový limit a nepovolují interaktivní terminálový prompt. Editor dovoluje uložit pouze úspěšně načtený UTF-8 soubor.

## Konfigurace a provozní data

| Výchozí umístění | Obsah |
| --- | --- |
| `kajovo_settings.json` | Nastavení; vzor je `kajovo_settings.example.json` |
| `kajovo.sqlite` | Databáze účtenek |
| `LOG/RUN_DDMMYYYYHHMM_XXXX` | Data běhu; poslední čtyři znaky jsou náhodné |
| `LOG/ui_session.log` | Zprávy desktopového rozhraní |
| `cache/price_table.json` | Ceník |
| `kajovo/core/openai_model_matrix.json` | Pevná verzovaná pravidla všech doložených modelů |
| `cache/cascades` | Definice vlastních kaskád |

`AppSettings` umožňuje změnit umístění databáze, LOG a cache. Načítání vyžaduje JSON objekt, kontroluje typy, konečnost čísel a rozsahy; neznámé klíče ignoruje. Seznamy allow/deny mohou být `null`. Výchozí teplota je 0,2, povolený rozsah 0 až 2. Port SMTP musí být 1 až 65535.

Výchozí retry má šest pokusů, počáteční prodlevu 0,8 s, strop 20 s, jitter do 0,25 s a circuit breaker po šesti chybách s prodlevou 20 s. Ceník má výchozí TTL 72 hodin a zapnuté automatické obnovení. SMTP má port 587 a STARTTLS.

`logging.max_total_mb` a `logging.max_runs` se ukládají a validují, ale implementace podle nich automaticky nemaže logy. Panel BATCH načítá seznam na pracovním vlákně a při nedokončených dávkách opakuje načtení podle `batch_poll_interval_s`. Po `batch_timeout_s` sledování skončí, aniž by zrušilo vzdálenou dávku; ruční obnovení zahájí nové sledování. `security.allow_upload_sensitive` výslovně povoluje soubory zachycené heuristikou citlivých názvů a obsahu. Ostatní filtry skenu zůstávají účinné.

## Běhy a souborové kontrakty

| Režim | Zpracování | Výsledek |
| --- | --- | --- |
| GENERATE | A1_PLAN → A2_STRUCTURE → A3_FILE pro jednotlivé soubory | Textové soubory v OUT |
| MODIFY | B1_PLAN → B2_STRUCTURE → B3_FILE | Změny souborů v OUT; podporuje dry-run |
| QA | Jeden požadavek Responses | Text odpovědi |
| QFILE | Jeden požadavek s kontraktem A3_FILE | Jeden úplný textový soubor v OUT |
| GENERATE BATCH | A1/A2 živě, A3_FILE po jednotlivých souborech v dávce | ID dávky, kontrolovaný import v BATCH |
| MODIFY BATCH | Jeden JSONL požadavek C_FILES_ALL | ID dávky, následné stažení v BATCH |
| KASKÁDA | Seřazené kroky uživatelské definice | Odpovědi, JSON a očekávané soubory kroků |

Běžný běh vyžaduje klíč, model a neprázdné zadání. Příprava v jádře ověří aktuální katalog účtu, pevnou matici modelu a před odesláním přesnou kombinaci pomocí zkušebního volání. Chybějící katalog nelze obejít volitelnou konfigurací. Neověřená volba z katalogu spustí automatické ověření; nepotvrzená kombinace neodešle pracovní požadavek. GENERATE včetně Batch dovoluje samostatný model pro A1, A2 a A3; prázdná volba použije hlavní model. GENERATE a MODIFY navazují přes `previous_response_id`; dostupnost starší odpovědi se ověřuje před odesláním. Zadání delší než 150 000 znaků zavádějí přes A0 po 20 000 znacích. QA a MODIFY BATCH používají textové části vstupní zprávy. GENERATE přikládá uživatelské soubory v A1; A2 využívá návaznost. Dávkové A3 používá samostatný snímek specifikace.

Každý generující požadavek používá `text.format.type=json_schema` a `strict=true`, včetně QA, přípravy schémat, zkušebních volání a jednotlivých řádků Batch. Schéma určuje kontrakt kroku, nikoli pevný seznam modelů. Textové kroky používají objekt s povinným řetězcem `text`; obsluha dostává rozbalený text. Před odesláním se ověřuje konzervativní podporovaná podmnožina schémat, uzavření objektů, povinné vlastnosti, typy, lokální odkazy a velikost. Odpověď vyžaduje `status=completed`, nesmí obsahovat odmítnutí ani chybu a musí celá projít JSON parserem a lokálním schématem. Odmítnutí a neúplná odpověď nepostupují k zápisu souborů.

Parser vyžaduje JSON objekt; dovoluje jeho extrakci z okolního textu. JSON pole samo o sobě není souborovým kontraktem. Manifesty obsahují relativní `path`; manifesty zápisu vyžadují přítomný textový `content`. Explicitní prázdný řetězec je platný obsah, chybějící pole, `null` a jiné typy se odmítají před prvním zápisem.

A3_FILE a B3_FILE obsahují označení `contract`, požadovanou `path`, textový `content` a objekt `chunking`. Části začínají indexem 0. `has_more` je boolean; pokračování vyžaduje celočíselný `next_chunk_index` rovný aktuálnímu indexu plus jedna. Obsah částí se spojuje bez vloženého oddělovače. Smyčka odmítne index nad 5000. Neplatný JSON nebo jiné označení kontraktu se zkouší nejvýše třikrát; neúspěch vyvolá chybu. QFILE vyžaduje `chunk_index: 0` a `has_more: false`; SEND AS BATCH pro něj není dostupné.

GENERATE negeneruje obsah položek PNG/JPG/JPEG; seznam zapisuje do `MISSINGFILES.md`. MODIFY přijímá akce `add` a `modify`, jiné akce odmítá. Při dry-run změny OUT neprovádí.

### Cesty a zápisy

Cesty nesmějí obsahovat absolutní umístění, disk či UNC, traversal, prázdné segmenty, nepovolené znaky Windows, rezervovaná zařízení ani segmenty končící tečkou nebo mezerou. Souborový manifest nepovoluje zpětná lomítka, duplicity bez ohledu na velikost písmen ani konflikt souboru s jeho podadresářem. Vyhodnocená cesta musí zůstat pod OUT i po rozvinutí souborových odkazů.

Manifest zápisu se validuje před první změnou jeho souborů. `atomic_write_text` zapisuje UTF-8 bez BOM do dočasného souboru ve stejném adresáři, provede flush/fsync a atomické nahrazení. Při nahrazování existujícího souboru přebírá jeho režim oprávnění. Vícesouborová operace není transakcí: chyba disku nebo přerušení mezi zápisy může ponechat část změn. Validace cesty neuzamyká souborový systém proti souběžným změnám jiným procesem.

VERSING vytvoří uvnitř OUT snapshot pojmenovaný názvem kořene a suffixem `DDMMYYYYHHMMSS`. Vynechává prostředí, LOG a rozpoznané snapshoty; odkazy kopíruje jako odkazy. Existující snapshot nepřepisuje. ReRun používá uloženou strukturu a návaznost; soubor přeskočí pouze při doloženém zápisu a existujícím výstupu, nikoli na základě samotného návrhu obsahu v odpovědi.

### Batch

GENERATE BATCH provede A1/A2 živě a po ověření specifikace vytvoří jeden řádek JSONL na každý vybraný textový soubor. Řádek obsahuje `custom_id`, `method: POST`, `url: /v1/responses` a `body`. Dávkové A3 nepoužívá `previous_response_id`; má společné zadání, plán a specifikaci. MODIFY BATCH vytváří jediný požadavek C_FILES_ALL. JSONL se nahrává s účelem `batch`. Stav `batch_pending` znamená odeslání, nikoli dokončení generování.

GENERATE BATCH dovoluje návaznost, připojená úložiště a diagnostiku IN v živé přípravě. Modely A1/A2 musí mít ověřenou schopnost připojeného file search. A2 převádí potřebné závěry do samostatné specifikace. Diagnostika OUT je při odesílání dávky zakázaná. MODIFY BATCH návaznost, úložiště ani diagnostiku nepřijímá. Akce „Zrušit zpracování“ volá endpoint cancel a nemaže dávku.

A2 verze 2 obsahuje společná pravidla, balíčky s verzemi, rozhraní s jedinečnými ID a přesnými definicemi a soubory s poli `path`, `purpose`, `language`, `kind`, `dependencies`, `provides`, `requires`, `behavior`. Souborové závislosti odkazují jen na jiné položky manifestu; rozhraní jen na definovaná ID. Chybějící poskytovatel nebo neznámý odkaz blokuje A3. Neplatný A2 může vyvolat nejvýše dvě opravná živá volání. Binární a vynechané soubory jsou evidovány jako nedodané.

`generate_batch` v evidenci běhu obsahuje neměnný snímek se SHA-256, požadavky a mapování ID na cesty. Jedna dávka má jediný model A3, 1 až 50 000 položek a nejvýše 200 MB JSONL. Přístup a definitivní podporu konkrétního modelu ověřuje služba; lokální pravidlo připouští známé textové rodiny. Každý A3_FILE musí mít index 0, počet částí 1, `has_more: false` a `next_chunk_index: null`. Neúplný výstup se neukládá jako hotový soubor.

Hybridní import běží na pracovním vlákně a stáhne výstupní i chybové JSONL do evidence běhu. Nejprve kontroluje ID celé dávky; neznámé ID zastaví import, duplicita zneplatní daný soubor. Cestu porovnává s uloženým manifestem. Úspěšné položky zapisuje atomicky, existující změněný obsah zachovává. Stav je `partial` nebo `files_complete_unverified`; druhý stav nepotvrzuje sestavení ani funkčnost. Hash souborů chrání opakovaný import i následné uživatelské změny.

Ruční opakování a oprava vybraných cest vytvářejí novou dávku bez A1/A2 se stejným snímkem. Oprava přidává připomínku a aktuální obsah. `generate_batches` a `batch_imports` zachovávají vztahy a výsledky přes restart. Starý ReRun bez společné specifikace není podkladem hybridní dávky. Živé přípravné odpovědi mají standardní sazbu, souborové odpovědi dávkovou sazbu; účtenky se deduplikují podle response ID.

Stažení preferuje OUT uložené u souvisejícího běhu, poté OUT panelu nebo adresář vybraný uživatelem. Uchovává nezpracovaný `batch_<id>_output.jsonl`, zpracovává souborové kontrakty a eviduje jednotlivá response ID. Neúplné nebo duplicitní části souboru hlásí jako chybu. Chyba jedné položky nevrací zpět již uložené položky. Stažení dávky není vícesouborovou transakcí.

Počet částí souboru je celé číslo 0 až 5001; nula označuje dosud neurčený počet. Index části je 0 až 5000. Kladný deklarovaný počet se mezi odpověďmi nesmí měnit a musí souhlasit s koncovou částí. `has_more` je boolean, pokračování odkazuje na následující index a poslední část má `next_chunk_index: null`. Stejná kontrola platí pro synchronní generování i import dávky. Import připouští části v jiném pořadí, ale odmítá rozporné konce a kolize cílových cest bez ohledu na velikost písmen. Již uložený výsledek téže dávky jiná položka nepřepisuje. Balíček musí mít explicitní seznam `files`; neplatný typ se nepovažuje za prázdný výsledek.

## Vlastní kaskády

`CascadeDefinition` obsahuje název, verzi, časy, `default_out_dir` a kroky. Prázdnou kaskádu worker odmítne. Každý `CascadeStep` má model, volitelnou teplotu, instrukce, vstupní text nebo strukturovaný `input_content_json`, existující ID a lokální cesty příloh, výraz návaznosti, typ výstupu, schéma a `expected_out_files`.

Načítání vyžaduje textové typy názvů, cest, modelů a zadání. Výraz návaznosti může být text nebo null. Verze musí být kladné celé číslo, časové údaje konečná nezáporná čísla; nula se při uložení zachovává. Poškozené hodnoty se nenahrazují aktuálním časem, výchozí verzí ani textovou reprezentací objektu.

Podporované substituce jsou `{{step.N.response_id}}`, `{{step.N.json}}`, `{{step.N.out_file_path:REL_PATH}}` a `{{step.N.out_file_id:REL_PATH}}`; kroky se číslují od 1. Substituce používá výsledky provedených kroků. Strukturovaný vstup může být objekt nebo seznam objektů. Lokální přílohy se nahrávají s účelem `user_data`.

JSON výstup používá předvolbu `manifest`, `prompts`, vlastní schéma nebo automaticky připravené schéma. Program převádí konkrétní objekty do strict formátu; nepřítomná volitelná pole přenáší jako nullable hodnoty a obnovuje je před kontrolou původního kontraktu. U neurčitého schématu provede samostatnou přípravu s pevným strict kontraktem a nejvýše dvě opravy návrhu. Schéma ukládá k běhu. Selhání přípravy zastaví pracovní krok bez požadavku na ruční psaní schématu. Původní významové podmínky ověřuje i lokálně. Externí odkazy jsou zakázané. V předvolbě `prompts` se dynamická pole `input_content_json` a `output_schema_custom` přenášejí jako serializovaný JSON text a program je rozbalí. Textové kroky rovněž používají strict schéma; výsledek obsahuje jejich rozbalené odpovědi.

Zápis manifestu do OUT spouští neprázdný `expected_out_files`. Bez něj samotný JSON manifest soubory neukládá. OUT běhu má přednost před `default_out_dir`. Všechny očekávané cesty musí být v manifestu před prvním zápisem. Ukládá se celý validovaný manifest, včetně dalších souborů; očekávané soubory se následně nahrávají do Files API a jejich cesty a ID jsou dostupné dalším krokům. Selhání uploadu nevrací lokální zápis zpět. Každá odpověď kroku se eviduje pod společným ID běhu.

## IN, přílohy a vzdálené prostředky

Sken IN aplikuje allow/deny seznamy přípon a masek. Vynechává Git, prostředí, runtime adresáře, cache, symlinky, junctions a rozpoznané snapshoty. Výchozí limit souboru je 10 MiB. Prázdné, binární a nečitelné soubory nejsou nahratelné. Citlivé názvy a rozpoznané vzory tajných údajů sken blokuje, pokud uživatel výslovně nepovolí jejich upload; manifest i tehdy zachovává příznak citlivosti. Detekce je heuristická.

Balíček IN je textový soubor s JSONL položkami `path` a `content`, nikoli ZIP. Načtený obsah musí odpovídat SHA-256 ze skenu a být dekódovatelný jako UTF-8. Celkový limit balíčku je 40 MiB. Podle schopností modelu může aplikace vytvořit i vector store a čekat na indexaci. Ručně vybrané přílohy nepodléhají stejnému skenu. Podporované dokumenty se připojují jako `input_file`, obrázky jako `input_image`. Nepodporovaná příloha nebo neověřitelná metadata běh zastaví. Kontrola velikosti zahrnuje součet příloh; pravidla jsou v [matici požadavků](REQUEST_MATRIX.md).

Běžné běhy mohou vytvářet vzdálené soubory a úložiště pro IN a diagnostiku. Dokončení je automaticky neodstraňuje; spravují se v příslušných panelech. Totéž platí pro přílohy a výstupy kaskád. Zkušební volání používají skutečné vybrané prostředky a nevytvářejí pomocná úložiště. Vlastní dávkové soubory se po převzetí výsledků a vyúčtování odstraňují; neúspěšný úklid je zaznamenaný.

## Komunikace a opakování

Klient používá OpenAI SDK pro vybrané operace a REST pro ostatní nebo při nedostupném SDK. Čtecí operace mohou po selhání SDK použít REST. Chyba mutace SDK se předává jako `OpenAIError` bez druhého provedení přes REST. Seznamy souborů, úložišť a dávek zpracovávají stránkování.

SDK má vypnuté vlastní retry. REST vrstva má nejvýše čtyři pokusy, počáteční prodlevu 0,8 s a strop 8 s; respektuje číselný Retry-After do tohoto stropu. Generující POST `/responses` a vytvoření `/batches` mají jediný pokus. Ostatní operace mohou opakovat timeouty, chyby spojení, HTTP 429 a 5xx. Nad nimi mohou volající používat aplikační retry. Nastavení `response_timeout_s` má výchozí hodnotu 300 s a řídí čekání na odpověď stejně pro SDK i REST. Je dostupné v SETTINGS. Časový limit nezaručuje dokončení služby a neprokazuje příčinu její prodlevy.

`model_registry` čte pouze distribuovaný `openai_model_matrix.json`; pravidla nemění katalog účtu, historie chyb ani stažený web. Matice obsahuje přesné modely a snapshoty, endpointy, modality, nástroje, reasoning, sampling, cache a limity. Neznámý model je nepovolený. Úplné osy a zdroje jsou v [matici požadavků](REQUEST_MATRIX.md) a [matici modelů](MODEL_MATRIX.md).

`response_policy` před každým pracovním LIVE odesláním provede zkušební volání s celým skutečným payloadem a ověří jeho výstup. Automaticky nemění teplotu, tool_choice, schéma, přílohy ani limit výstupu. Identita obsahuje celý payload a režim; změna kterékoliv volby vyžaduje novou zkoušku. Výsledek není odhadem schopností modelu. Přístup ke skutečným file_id, předchozí odpovědi a stav vector store se ověřují odděleně. Příprava se zástupnými ID provádí jen statickou kontrolu; zástupná ID se neodesílají.

BATCH nejprve kontroluje všechny řádky včetně jednotného modelu a poté vytváří skutečnou zkušební dávku pro neověřené payloady. Pracovní upload/odeslání je podmíněné completed a úspěšným platným výsledkem každého řádku. Čekání trvá nejvýše 60 sekund; nedokončená zkouška pracovní odeslání zastaví a vrátí ID/stav. Příští spuštění přebere tutéž dávku. Okno OpenAI je 24 hodin. Jedna kombinace se nemůže prokázat výsledkem jiného endpointu. Úspěšné dávkové výsledky platí do pracovního odeslání, nejvýše hodinu od převzetí. Neurčité vytvoření bez ID se automaticky neopakuje.

Cache důkazů je oddělená podle SHA-256 kontextu klíče, endpointu a verze matice; klíč se neukládá. Staré důkazy se nemigrují. HTTP chyba obsahuje model, transport, param/code/request_id podle údajů API. Nepodporovaný parametr je hlášen jako rozpor s maticí; chyby účtu, prostředku, obsahu a sítě se za chybu statické podpory nevydávají. Matice zůstává neměnná i po odmítnutí API.

Lokální kontrola známých parametrů a schémat předchází zkušebním voláním a uploadům pracovních dat; po dosazení vzdálených ID se zopakuje. Každý dávkový řádek se kontroluje před uploadem JSONL i před vytvořením vzdálené dávky. Změnu oprávnění, odstranění prostředku nebo výpadek mezi kontrolou a odesláním nelze vyloučit; takové chyby se evidují odděleně od lokálního odmítnutí.

Multipart požadavek nemá globální JSON Content-Type a při opakování obnovuje pozici vstupního streamu. Circuit breaker odkládá další pokus po opakovaných chybách. Při ztracené síťové odpovědi není zaručeno právě jedno provedení operace na serveru.

## Souběh, zastavení a zabezpečení

Workery běžných běhů i kaskád při vytvoření pořizují hlubokou kopii konfigurace a nastavení, včetně vnořených seznamů a definic kroků. Následné změny původních objektů neovlivňují běh. Hlavní okno odmítá překrývající se OUT souběžných zapisujících běhů, které samo spravuje; nejde o systémový zámek pro jiné procesy či ruční operace panelů. Dokončení a oznámení používají údaje příslušného běhu.

STOP je kooperativní. Kontrola zastavení probíhá mezi operacemi; probíhající síťový požadavek nebo retry může návrat oddálit. Okno čeká na aktivní workery a nepoužívá násilné ukončení QThread.

SMTP a SSH hesla se ukládají přes OS keyring. Při nedostupném úložišti existuje dočasný fallback v prostředí procesu. Ukládaný JSON obsahuje prázdná pole hesel. Načtená hesla z JSON se přesouvají do úložiště a soubor se při úspěchu přepíše bez nich.

API klíč se před inicializací API panelů načítá z uživatelského záznamu `HKCU\Environment\OPENAI_API_KEY`. Uložená hodnota má přednost před zděděným prostředím procesu; pouze při neexistujícím záznamu se použije proměnná `OPENAI_API_KEY`. Načtená hodnota sjednotí prostředí aktuálního procesu i jednotlivé panely. Chyba čtení se oznámí a aplikace nepoužije potenciálně zastaralý klíč. Uložení nejprve zapíše a zpětně ověří trvalou hodnotu; až po úspěchu aktivuje nový klíč. Při selhání ověření se pokusí obnovit původní záznam a oznámí chybu. Smazání ukládá prázdný záznam, aby starý klíč nemohl znovu ožít ze zděděného prostředí. Restart Windows ani rodičovského terminálu není nutný. Toto umístění není šifrovaným úložištěm API klíče; klíč se nepředává programu `setx` jako argument procesu.

SSH pin je Base64 SHA-256 veřejného host key s volitelným prefixem `SHA256:`. Porovnání rozlišuje velikost písmen. Lokální diagnostika spouští distribuovaný PowerShell kolektor s časovým limitem a kontrolou návratového kódu. Spuštění navržené opravy vyžaduje samostatné potvrzení. SMTP používá nastavené SSL nebo STARTTLS; odeslání testovací zprávy je uživatelskou akcí.

SSH OUT spouští schválený obsah `.sh` přes stdin vzdáleného `sh -s` na hostiteli ze snímku konfigurace běhu. Windows OUT používá lokální `.bat`. Potvrzení uvádí cíl a SHA-256 skriptu; výstupy obou variant mají samostatné logy. SSH vyžaduje známý host key a případný odpovídající pin. SMTP ověřuje certifikát i jméno serveru v SSL a STARTTLS; neplatné hlavičky vracejí chybu bez připojení.

Textový kontrakt odmítá nedokončené a chybové odpovědi, odmítnutí modelu a odpověď bez textu. JSON objekt nesmí obsahovat duplicitní klíče ani nestandardní číselné konstanty. Kaskáda kontroluje modely, teploty, schémata a odkazy na předchozí kroky před první síťovou operací. Chybějící hodnotu odkazu nenahrazuje prázdným textem.

## Logy, účtenky a ceny

Adresář běhu obsahuje `files`, `requests`, `responses`, `manifests`, `misc`, `events.jsonl` a `run_state.json`. Vytvoření odmítá existující adresář téhož běhu. Názvy uložených JSON kombinují bezpečný zkrácený název a hash. Stav běžného běhu rozlišuje vytvoření, běh, dokončení, zastavení a selhání.

Logy obsahují zadání, odpovědi a případně zdrojový kód. Známá tajná pole a řetězce s Bearer hodnotami se redigují, ale volný text může obsahovat další citlivá data. Logy nejsou šifrované a ovladač šifrování je neaktivní. Správa přístupu a uchování provozních dat je odpovědností provozovatele.

SQLite používá WAL. Migrace `user_version=1` před změnou existující evidence vytváří zálohu `.before-cost-v1.bak`. Tabulka `receipts` zachovává původní údaje, přesnou částku `total_usd` jako desetinný řetězec a `pricing_snapshot_json`. Starší záznam bez doloženého snímku nemá dodatečně vymyšlenou přesnou cenu. Vložení deduplikuje response ID v rámci `provider_profile`; více odpovědí jedné dávky jsou samostatné účtenky. Archivace nemění náklady. Oprava účtenky vytváří položku `receipt_revisions`, nikoli přepis původního řádku; doplňující poznámky mají tabulku `receipt_notes`.

`cost_accounting.py` počítá v Decimal, kanonicky v USD za milion tokenů. Běžný vstup = celkový vstup − cache read − cache write. Zápis cache nahrazuje běžnou vstupní sazbu. Reasoning je podmnožina výstupních tokenů a nepřičítá se podruhé. Neplatná nebo chybějící spotřeba a chybějící sazba mají neznámou cenu, nikoli nulu. Batch vyžaduje vlastní sazbu. Prahy dlouhého kontextu musí být doloženy pro daný model; nevyjádřitelná cenová kombinace se neimportuje. Poplatky file search používají skutečný počet volání. Úložiště je samostatná průběžná služba; globální bezplatný limit účtu se neodečítá po jednotlivých bězích.

`price_sources.py` čte oficiální Markdown tabulky Standard a Batch, kontroluje sloupce a jednotky a doplňuje dostupné modelové snapshoty a limity z modelových stránek. Nedoložené kategorie zůstávají neznámé. JSON import je ruční neověřený zdroj; sazby v cache `schema_version=2` zachovávají kompatibilní názvy `*_per_1k`. Síťová chyba zachovává poslední ceník. Snímek účtenky obsahuje zdroj a čas ověření a nezmění se při aktualizaci cache. Datovaný model bez explicitního řádku může mít pouze neověřený odhad aliasu. Tvrdý limit nepoužívá neověřené sazby ani ceník starší 72 hodin. Nestandardní service tier bez příslušných sazeb nemá doloženou cenu.

Před první generující operací worker sestaví skutečný payload a přes `POST /v1/responses/input_tokens` získá počet vstupních tokenů. Posílá pouze parametry podporované počítacím endpointem včetně předchozí odpovědi a příloh. Odhad je vázán hashem na payload a snímek sazeb. Změna maxima výstupu vyžaduje nový odhad a potvrzení. Bez alespoň deseti dokončených srovnatelných vzorků modelu, fáze a reasoning používá výslovné scénáře 2k/8k/32k; jinak P10, medián a P90. Scénáře respektují nastavené a známé modelové maximum. Není možné přesně předpovědět dosud nevytvořené vstupy dalších kroků.

`cost_scopes` uchovává volitelný limit USD; `cost_operations` podklady odhadu, rezervaci, spotřebu, výsledek a stav oznámení. Rezervace probíhá v `BEGIN IMMEDIATE` před odesláním. Pro celou připravenou dávku se rezervuje součet maxim. Strop používá nejdražší možnou kategorii vstupu včetně zápisu cache a skutečné `max_output_tokens`. Neurčený výstup nebo dynamické nástroje nemají doložený strop. Při nedostatku rozpočtu UI umožní zvýšení limitu nebo zastavení. Nejistý výsledek přijetí požadavku ponechá rezervaci nevyřešenou. Generující požadavky a vytvoření dávek nemají skryté automatické opakování po timeoutu. Lokální chyba evidence nevyvolá nové placené volání.

Evidence rozlišuje `_outcome` (výsledek služby nebo nejistý transport) a `_pricing_status` (vyčíslená nebo neznámá cena). Dokončená odpověď bez doložené ceny není timeout. Sazba vráceného snapshotu může být použita jen s doloženým zdrojem; shoda veškerých metadat s aliasem se nevyžaduje. Seznam modelových dokumentů pro ceník vychází z cenových tabulek. Zkušební volání ukládají vlastní požadavky a odpovědi do LOG; při samostatném spuštění bez cenového dialogu uloží skutečnou spotřebu s neověřenou cenou do účtenek.

`cost_dialog.py` spojuje worker s potvrzovacím oknem přes queued Qt signál a událost. Síť, odhad a kurz ČNB běží mimo vlákno UI. Zavření nebo zrušení dialogu neodešle generování. GENERATE BATCH po A2 vždy potvrzuje konkrétní A3 dávku a ukazuje dosavadní vyčíslenou přípravu. Vybrané opakování a opravy souborů používají stejný rozpočtový scope. Zkušební LIVE i BATCH rovněž používají odhad a evidenci nákladů; nejde o bezplatný dry-run. Po živém dokončení nebo chybě se otevře výsledná účtenka s nevyřešenými položkami. Vynucené ukončení procesu může zanechat rezervaci bez výsledku; její stav zůstává v databázi.

`batch_costs.py` načítá terminální výstup i chybový JSONL a účtuje položky podle uložených custom ID a sazeb. Nezapisuje do OUT. Neúplná či duplicitní data zůstanou nevyřešená. Oznámení výsledné účtenky má trvalý příznak zobrazení; restart je neztratí. Import souborů je samostatná operace. BATCH sleduje dávky po omezenou dobu podle nastavení; ruční obnovení pokračuje v načítání a vyúčtování.

Audit LOG doplňuje chybějící účtenky a nepřeceňuje existující historii. PRICING stránkuje po 100 záznamech, filtruje projekt/běh/model a archiv, zobrazuje úplný součet doložených cen a počet nevyčíslených položek. Export JSON/CSV zahrnuje celý filtr. Výsledné ceny jsou výpočtem aplikace podle usage, nikoli účetní fakturou OpenAI nebo úplným přehledem jiných aplikací v účtu. Uložený kurz ČNB a datum slouží pouze k orientačnímu přepočtu CZK; nemění limit USD.

Oficiální kontrakty: [počítání tokenů](https://developers.openai.com/api/docs/guides/token-counting), [ceník](https://developers.openai.com/api/docs/pricing), [cache](https://developers.openai.com/api/docs/guides/prompt-caching).

## Převodník UTF-8

`python -m utf8nobom.py` spouští samostatné Tk rozhraní. Převodník vytváří kopie a ZIP zálohy mimo vstupní adresáře. Překrývající se vstupy deduplikuje, stejně pojmenované adresáře rozlišuje v názvech záloh a existující zálohu nepřepisuje. Git metadata a odkazy nekonvertuje. ZIP s traversal položkou odmítne beze změny; při přepisu zachovává komentář archivu a metadata položek. Oprava kódování je heuristická a výsledek je třeba posoudit podle konkrétních dat; originál zůstává v záloze.

## Distribuce a ověření

Editor kaskády ověřuje model, teplotu, strukturovaný vstup a dostupnost vlastního schématu před změnou uloženého kroku. Externí návaznost je dostupná i v prvním kroku. Dostupnost voleb Batch závisí na režimu: GENERATE má živou přípravu, MODIFY souhrnný požadavek. Minimální explicitní `max_output_tokens` je 16. Kombinace režimů a příznaků vymezuje `docs/REQUEST_MATRIX.md` a reprodukovatelný export `docs/REQUEST_COMBINATIONS.csv`.

Wheel obsahuje balíčky `kajovo`, `kajovong`, `utf8nobom`, diagnostický skript, logo a oba fonty. Testy nejsou distribuovanými balíčky. Fonty ve zdrojovém stromu používají Git LFS. Windows sestavení vytváří `dist/Kajovo/Kajovo.exe`, macOS sestavení `dist/Kajovo.app`; název lze předat sestavovacímu skriptu. Sestavení provádí instalaci závislostí, generování ikon a PyInstaller. Generované adresáře a binární distribuce nejsou zdrojovým kódem.

| Testy | Ověřované chování |
| --- | --- |
| `test_workflows.py` | Offline režimy, batch, účtenky, snímky konfigurace a validace výstupu |
| `test_contracts.py`, `test_cascade.py` | JSON, cesty, souborové výstupy, schémata a kaskády |
| `test_filesystem_boundaries.py`, `test_security_regressions.py` | Souborové hranice, citlivé vstupy, hashe a bezpečnost zápisu |
| `test_api_client.py`, `test_retry.py`, `test_text_chunks.py` | HTTP, SDK, stránkování, opakování a dělení textu |
| `test_config.py`, `test_diagnostics.py` | Nastavení, hesla a SSH pin |
| `test_pricing.py`, `test_receipts.py`, `test_runlog.py` | Cenové jednotky, cache, databáze a logy |
| `test_desktop.py`, `test_repository_contract.py` | Importy, Qt, ovládání, prostředky a kódování |
| `test_utf8nobom.py` | Zálohy, deduplikace vstupů a převod ZIP |

Povinné kontroly jsou `python -m pytest -q`, `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build` a `python -m pip check`, spuštěné v projektovém prostředí. CI používá Windows a Python 3.13. Automatické testy nahrazují HTTP a nevyžadují produkční API klíč.

Úspěch kontrol platí pro jejich rozsah. Živé OpenAI, SMTP, vzdálené SSH, oprávnění účtu, interaktivní provoz a platformní distribuce vyžadují ověření v odpovídajícím prostředí. Automatické testy nejsou zárukou nepřítomnosti všech chyb.
