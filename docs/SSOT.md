# KájovoNG — specifikace systému

## Účel a rozsah

KájovoNG je desktopový klient OpenAI Responses API pro vytváření a úpravy textových souborů, dotazy, uživatelské kaskády a dávkové požadavky. Zahrnuje správu vzdálených souborů a vector stores, evidenci běhů, Git, SMTP a diagnostiku Windows a SSH. Součástí projektu je samostatný převodník textů `utf8nobom`.

Tento dokument je kanonickou technickou specifikací. [README](../README.md) popisuje spuštění a [návod sestavení](../Build/README.md) distribuci. Chování ověřují zdrojový kód a automatické testy. Změny kontraktů se promítají do specifikace i odpovídajících testů.

## Prostředí a spuštění

Projekt vyžaduje Python 3.12 nebo novější. Závislosti a jejich povolené verze určuje [pyproject.toml](../pyproject.toml): PySide6, OpenAI SDK, requests, paramiko, keyring a jsonschema. Vývojové nástroje jsou v extra `dev`. `requirements.txt` instaluje projekt, `requirements-dev.txt` projekt s vývojovými závislostmi.

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
| API | `openai_client.py`, `response_policy.py`, `retry.py`, `compat.py`, `model_capabilities.py` | SDK/REST, neplacená validační politika, chyby, stránkování, opakování a schopnosti modelů |
| Souborové hranice | `contracts.py`, `filescan.py`, `utils.py` | Kontrakty, sken vstupu, validace cest, hashe a zápisy |
| Evidence | `runlog.py` | Logy, požadavky, odpovědi a stavy běhů |
| Nastavení | `config.py`, `secret_store.py`, `resources.py` | Konfigurace, hesla a prostředky |
| Diagnostika | `diagnostics/windows.py`, `diagnostics/ssh.py`, `notifications.py` | Sběr diagnostiky a SMTP |
| Desktop | `kajovo/desktop` | Hlavní okno, panely, dialogy a workery Qt |
| Převod textů | `utf8nobom/app.py`, `utf8nobom/py.py` | Tk rozhraní, zálohy a převod souborů i položek ZIP |

Názvy modulů bez adresáře označují soubory pod `kajovo/core`.

## Uživatelské rozhraní

Hlavní okno má devět sekcí v levé navigaci: Zadání, Kaskády, Zdroje, Dávky, Historie, Verze projektu, Modely, Nastavení a Nápověda. Zadání odděluje prompt, parametry a adresáře, diagnostiku a výsledek. Spustit, Zastavit a přístup k aktivním běhům zůstávají mimo posuvný obsah. Teplota běhu a výchozí teplota mají samostatné ovladače. Nastavení a Dávky lze otevřít i v samostatném okně se stejným obsahem a stavem. [Inventář a návrh UI](UI_DESIGN.md) popisuje pokrytí funkcí a odkazy na validační matice.

Rozhraní v `kajovo/desktop` používá světlou pracovní plochu, tmavou navigaci a Montserrat 10 bodů. Formuláře a navigace dovolují posouvání i na malé logické ploše při zvýšeném DPI; hlavní akce Zadání zůstávají dosažitelné. Dialogy přizpůsobují velikost dostupnému oknu, podrobnosti mají posuv a společný přepínač technických podkladů. Tabulky mají čitelné jednotky, kopírování výběru a vodorovný posuv. Nové rozhraní je samostatná implementace; balík `kajovo/ui` se nedistribuuje.

`ProgressEvent` přenáší etapu, stav, dokončený počet, celkový počet, jednotku, detail a monotónní čas události. Text logu ani pevně vážená procenta neurčují dokončení. A3/B3 započítávají soubor po získání a ověření celého obsahu; zápis na disk je samostatná etapa. Kaskáda započítává dokončené kroky. API bez měřitelného postupu používá neurčitý indikátor. Trvání a stáří poslední události se obnovují každou sekundu; tato obnova nedokazuje aktivitu poskytovatele. ETA vzniká po třech dokončených srovnatelných jednotkách z mediánu posledních pěti dob, pouze pro aktuální etapu. Změna etapy vzorky resetuje. Ukončení, chyba, zrušení a předání do Batch mají odlišné stavy; koncové indikátory již neanimují práci.

BATCH zobrazuje počet zpracovaných úloh včetně chyb, čas poslední úspěšné aktualizace stavu a další naplánovanou kontrolu. ETA vzdálené fronty není odhadována. Dokončení API není potvrzením zápisu do OUT. Uploadový dialog povolí zavření po zrušení až po potvrzení konce workerem.

Síťové operace panelů, Git příkazy a SMTP test běží v asynchronních workerech. Správce Jobs drží worker do signálu finished; teprve poté předá výsledek UI a případně zahájí navazující obnovu. Správa zdrojů a Git blokuje konfliktní akce po dobu operace. Průběžné textové logy mají limit 2000 bloků; uložená evidence tím není omezena.

Zdroje obsahují soubory API a vector stores. Odpojení souboru od úložiště a odstranění z Files API jsou odlišné operace. Modely filtrují katalog podle pevné matice a zobrazují pravidla. Historie zobrazuje uložené požadavky a odpovědi.

GITHUB pracuje s lokálním repozitářem a příkazy Git. Obnovení stavu nepřepisuje remote. Zápis, commit, synchronizaci a změnu remote vyvolávají příslušné uživatelské akce. Pull používá aktuální větev a fast-forward. Příkazy mají časový limit a nepovolují interaktivní terminálový prompt. Editor dovoluje uložit pouze úspěšně načtený UTF-8 soubor.

## Konfigurace a provozní data

| Výchozí umístění | Obsah |
| --- | --- |
| `kajovo_settings.json` | Nastavení; vzor je `kajovo_settings.example.json` |
| `LOG/RUN_DDMMYYYYHHMM_XXXX` | Data běhu; poslední čtyři znaky jsou náhodné |
| `LOG/ui_session.log` | Zprávy desktopového rozhraní |
| `kajovo/core/openai_model_matrix.json` | Pevná verzovaná pravidla všech doložených modelů |
| `cache/cascades` | Definice vlastních kaskád |

`AppSettings` umožňuje změnit umístění databáze, LOG a cache. Načítání vyžaduje JSON objekt, kontroluje typy, konečnost čísel a rozsahy; neznámé klíče ignoruje. Seznamy allow/deny mohou být `null`. Výchozí teplota je 0,2, povolený rozsah 0 až 2. Port SMTP musí být 1 až 65535.

Výchozí retry má šest pokusů, počáteční prodlevu 0,8 s, strop 20 s, jitter do 0,25 s a circuit breaker po šesti chybách s prodlevou 20 s. SMTP má port 587 a STARTTLS.

`logging.max_total_mb` a `logging.max_runs` se ukládají a validují, ale implementace podle nich automaticky nemaže logy. Panel BATCH načítá seznam na pracovním vlákně a při nedokončených dávkách opakuje načtení podle `batch_poll_interval_s`. Po `batch_timeout_s` sledování skončí, aniž by zrušilo vzdálenou dávku; UI uvede, že vypršel pouze lokální monitorovací limit, a ruční obnovení zahájí nové sledování. `security.allow_upload_sensitive` výslovně povoluje soubory zachycené heuristikou citlivých názvů a obsahu. Ostatní filtry skenu zůstávají účinné.

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

Běžný běh vyžaduje klíč, model a neprázdné zadání. Před pracovním odesláním jádro provádí pouze deterministickou lokální validaci payloadu, pevné matice modelu, schémat, cest a kombinace voleb a podle potřeby ne-generativní čtení katalogu účtu nebo již existujících vzdálených prostředků. **Žádný samostatný placený preflight, testovací Response ani zkušební BATCH se před pracovní operací neposílá.** První generativní POST konkrétní etapy je její skutečný pracovní požadavek. GENERATE včetně Batch dovoluje samostatný model pro A1, A2 a A3; prázdná volba použije hlavní model. GENERATE a MODIFY navazují přes `previous_response_id`; dostupnost starší odpovědi lze ověřit ne-generativním načtením. Zadání delší než 150 000 znaků zavádějí přes A0 po 20 000 znacích. QA a MODIFY BATCH používají textové části vstupní zprávy. GENERATE přikládá uživatelské soubory v A1; A2 využívá návaznost. Dávkové A3 používá samostatný snímek specifikace.

Každý generující pracovní požadavek používá `text.format.type=json_schema` a `strict=true`, včetně QA, funkční přípravy schémat a jednotlivých řádků Batch. Schéma určuje kontrakt kroku, nikoli pevný seznam modelů. Textové kroky používají objekt s povinným řetězcem `text`; obsluha dostává rozbalený text. Před odesláním se lokálně ověřuje konzervativní podporovaná podmnožina schémat, uzavření objektů, povinné vlastnosti, typy, lokální odkazy a velikost. Odpověď vyžaduje `status=completed`, nesmí obsahovat odmítnutí ani chybu a musí celá projít JSON parserem a lokálním schématem. Odmítnutí a neúplná odpověď nepostupují k zápisu souborů.

Parser vyžaduje JSON objekt; dovoluje jeho extrakci z okolního textu. JSON pole samo o sobě není souborovým kontraktem. Manifesty obsahují relativní `path`; manifesty zápisu vyžadují přítomný textový `content`. Explicitní prázdný řetězec je platný obsah, chybějící pole, `null` a jiné typy se odmítají před prvním zápisem.

A3_FILE a B3_FILE obsahují označení `contract`, požadovanou `path`, textový `content` a objekt `chunking`. Části začínají indexem 0. `has_more` je boolean; pokračování vyžaduje celočíselný `next_chunk_index` rovný aktuálnímu indexu plus jedna. Obsah částí se spojuje bez vloženého oddělovače. Smyčka odmítne index nad 5000. Neplatný JSON nebo jiné označení kontraktu se zkouší nejvýše třikrát; neúspěch vyvolá chybu. QFILE vyžaduje `chunk_index: 0` a `has_more: false`; SEND AS BATCH pro něj není dostupné.

GENERATE v A3 automaticky nedodává položky označené `kind=binary`, PNG/JPG/JPEG ani typy explicitně vyloučené z automatického generování; všechny takové očekávané položky zapisuje do `MISSINGFILES.md`. Pokud po běhu zůstává alespoň jeden takový nedodaný očekávaný soubor, terminální stav LIVE GENERATE je `partial`; `completed` neznamená úplný výstup, pokud evidence obsahuje `missing_deliverables`. ReRun, který díky doloženým již existujícím zápisům nemusí vytvářet nový soubor, eviduje `no_changes`. MODIFY přijímá akce `add` a `modify`, jiné akce odmítá. Při dry-run změny OUT neprovádí.

### Cesty a zápisy

Cesty nesmějí obsahovat absolutní umístění, disk či UNC, traversal, prázdné segmenty, nepovolené znaky Windows, rezervovaná zařízení ani segmenty končící tečkou nebo mezerou. Souborový manifest nepovoluje zpětná lomítka, duplicity bez ohledu na velikost písmen ani konflikt souboru s jeho podadresářem. Vyhodnocená cesta musí zůstat pod OUT i po rozvinutí souborových odkazů.

Manifest zápisu se validuje před první změnou jeho souborů. `atomic_write_text` zapisuje UTF-8 bez BOM do dočasného souboru ve stejném adresáři, provede flush/fsync a atomické nahrazení. Při nahrazování existujícího souboru přebírá jeho režim oprávnění. Vícesouborová operace není transakcí: chyba disku nebo přerušení mezi zápisy může ponechat část změn. Validace cesty neuzamyká souborový systém proti souběžným změnám jiným procesem.

VERSING vytvoří uvnitř OUT snapshot pojmenovaný názvem kořene a suffixem `DDMMYYYYHHMMSS`. Vynechává prostředí, LOG a rozpoznané snapshoty; odkazy kopíruje jako odkazy. Existující snapshot nepřepisuje. ReRun používá uloženou strukturu a návaznost; soubor přeskočí pouze při doloženém zápisu a existujícím výstupu, nikoli na základě samotného návrhu obsahu v odpovědi.

### Batch

GENERATE BATCH provede A1/A2 živě a po ověření specifikace vytvoří jeden řádek JSONL na každý vybraný textový soubor. Řádek obsahuje `custom_id`, `method: POST`, `url: /v1/responses` a `body`. Dávkové A3 nepoužívá `previous_response_id`; má společné zadání, plán a specifikaci. MODIFY BATCH vytváří jediný požadavek C_FILES_ALL. JSONL se před síťovým uploadem kompletně lokálně validuje a následně se nahrává s účelem `batch` jako skutečný vstup pracovní dávky. Stav `batch_pending` znamená odeslání pracovní dávky, nikoli dokončení generování.

Před pracovní dávkou se **nevytváří žádný pomocný JSONL, žádný testovací Files upload a žádný testovací `POST /batches`**. Po lokální validaci existuje právě jeden pracovní vstupní soubor a právě jeden pokus o vytvoření pracovní dávky. Neurčitý výsledek tohoto jediného pracovního `POST /batches` se eviduje jako `submission_unknown` a před jakýmkoli dalším pracovním submittem se musí dohledat podle přesného `input_file_id`; automatické opakování neurčitého vytvoření je zakázané.

GENERATE BATCH dovoluje návaznost, připojená úložiště a diagnostiku IN v živé přípravě. Modely A1/A2 musí mít podle pevné matice schopnost připojeného file search. A2 převádí potřebné závěry do samostatné specifikace. Diagnostika OUT je při odesílání dávky zakázaná. MODIFY BATCH návaznost, úložiště ani diagnostiku nepřijímá. Akce „Zrušit zpracování“ volá endpoint cancel a nemaže dávku.

A2 verze 2 obsahuje společná pravidla, balíčky s verzemi, rozhraní s jedinečnými ID a přesnými definicemi a soubory s poli `path`, `purpose`, `language`, `kind`, `dependencies`, `provides`, `requires`, `behavior`. Souborové závislosti odkazují jen na jiné položky manifestu; rozhraní jen na definovaná ID. Chybějící poskytovatel nebo neznámý odkaz blokuje A3. Neplatný A2 může vyvolat nejvýše dvě opravná živá volání, protože tato volání opravují skutečný pracovní artefakt A2 a nejsou samostatným testem kompatibility. Binární a vynechané soubory jsou evidovány jako nedodané.

Před validací každé nové odpovědi A2 v LIVE i BATCH probíhá `prepare_structure`: na kopii specifikace doplní chybějící `dependencies`, pokud vyžadované rozhraní deklaruje v `provides` právě jeden soubor. Zachovává existující vazby a jejich pořadí, nevytváří duplicity ani vlastní závislost. Vlastní poskytované rozhraní nebo již uvedený poskytovatel další vazbu nepotřebuje. Při více poskytovatelích bez existující volby se žádný nevybírá odhadem. Příprava nemění neznámé odkazy ani nevymýšlí chybějící rozhraní či poskytovatele.

`validate_structure` zůstává přísný a původní manifest neopravuje. Po ověření tvaru a jednoznačnosti identifikátorů hlásí vztahové chyby souhrnně, s cestami souborů, identifikátory rozhraní a dostupnými poskytovateli. Opravné volání dostává všechny zjištěné problémy i aktuální připravený manifest; dvě opravná volání jsou horní mez, nikoli počet opravovaných vazeb. Jednoznačné doplnění nevyžaduje další API volání. Původní odpovědi zůstávají zachované, kandidáti `A2_prepared_candidate_*` a úspěšně ověřený `A2_validated_structure` se ukládají samostatně s evidencí doplněných vazeb. Následující BATCH používá připravenou strukturu ve společném snímku. Již uložené dávkové manifesty, požadavky a jejich hashe se zpětně nenormalizují.

`generate_batch` v evidenci běhu obsahuje neměnný snímek se SHA-256, požadavky a mapování ID na cesty. Jedna dávka má jediný model A3, 1 až 50 000 položek a nejvýše 200 MB JSONL. Definitivní přijetí konkrétního modelu ověřuje až skutečný pracovní požadavek služby; lokální pravidlo připouští pouze známé podporované textové rodiny. Každý A3_FILE musí mít index 0, počet částí 1, `has_more: false` a `next_chunk_index: null`. Neúplný výstup se neukládá jako hotový soubor.

Hybridní import běží na pracovním vlákně a stáhne výstupní i chybové JSONL do evidence běhu. Nejprve kontroluje ID celé dávky; neznámé ID zastaví import, duplicita zneplatní daný soubor. Cestu porovnává s uloženým manifestem. Úspěšné položky zapisuje atomicky, existující změněný obsah zachovává. Stav je `partial` nebo `files_complete_unverified`; druhý stav nepotvrzuje sestavení ani funkčnost. Hash souborů chrání opakovaný import i následné uživatelské změny.

Ruční opakování a oprava vybraných cest vytvářejí novou pracovní dávku bez A1/A2 se stejným snímkem. Oprava přidává připomínku a aktuální obsah. Ani opakování, ani oprava nevytváří před vlastní pracovní dávkou pomocnou zkušební dávku. `generate_batches` a `batch_imports` zachovávají vztahy a výsledky přes restart. Starý ReRun bez společné specifikace není podkladem hybridní dávky.

Stažení preferuje OUT uložené u souvisejícího běhu, poté OUT panelu nebo adresář vybraný uživatelem. Uchovává nezpracovaný `batch_<id>_output.jsonl`, zpracovává souborové kontrakty a eviduje jednotlivá response ID. Neúplné nebo duplicitní části souboru hlásí jako chybu. Chyba jedné položky nevrací zpět již uložené položky. Stažení dávky není vícesouborovou transakcí.

Počet částí souboru je celé číslo 0 až 5001; nula označuje dosud neurčený počet. Index části je 0 až 5000. Kladný deklarovaný počet se mezi odpověďmi nesmí měnit a musí souhlasit s koncovou částí. `has_more` je boolean, pokračování odkazuje na následující index a poslední část má `next_chunk_index: null`. Stejná kontrola platí pro synchronní generování i import dávky. Import připouští části v jiném pořadí, ale odmítá rozporné konce a kolize cílových cest bez ohledu na velikost písmen. Již uložený výsledek téže dávky jiná položka nepřepisuje. Balíček musí mít explicitní seznam `files`; neplatný typ se nepovažuje za prázdný výsledek.

## Vlastní kaskády

`CascadeDefinition` obsahuje název, verzi, časy, `default_out_dir` a kroky. Prázdnou kaskádu worker odmítne. Každý `CascadeStep` má model, volitelnou teplotu, instrukce, vstupní text nebo strukturovaný `input_content_json`, existující ID a lokální cesty příloh, výraz návaznosti, typ výstupu, schéma a `expected_out_files`.

Načítání vyžaduje textové typy názvů, cest, modelů a zadání. Výraz návaznosti může být text nebo null. Verze musí být kladné celé číslo, časové údaje konečná nezáporná čísla; nula se při uložení zachovává. Poškozené hodnoty se nenahrazují aktuálním časem, výchozí verzí ani textovou reprezentací objektu.

Podporované substituce jsou `{{step.N.response_id}}`, `{{step.N.json}}`, `{{step.N.out_file_path:REL_PATH}}` a `{{step.N.out_file_id:REL_PATH}}`; kroky se číslují od 1. Substituce používá výsledky provedených kroků. Strukturovaný vstup může být objekt nebo seznam objektů. Lokální přílohy se nahrávají s účelem `user_data`.

JSON výstup používá předvolbu `manifest`, `prompts`, vlastní schéma nebo automaticky připravené schéma. Program převádí konkrétní objekty do strict formátu; nepřítomná volitelná pole přenáší jako nullable hodnoty a obnovuje je před kontrolou původního kontraktu. U neurčitého schématu provede samostatnou funkční přípravu s pevným strict kontraktem a nejvýše dvě opravy návrhu. Tyto požadavky vytvářejí artefakt potřebný pro vlastní práci a nejsou preflightem kompatibility. Schéma ukládá k běhu. Selhání přípravy zastaví pracovní krok bez požadavku na ruční psaní schématu. Původní významové podmínky ověřuje i lokálně. Externí odkazy jsou zakázané. V předvolbě `prompts` se dynamická pole `input_content_json` a `output_schema_custom` přenášejí jako serializovaný JSON text a program je rozbalí. Textové kroky rovněž používají strict schéma; výsledek obsahuje jejich rozbalené odpovědi.

Zápis manifestu do OUT spouští neprázdný `expected_out_files`. Bez něj samotný JSON manifest soubory neukládá. OUT běhu má přednost před `default_out_dir`. Všechny očekávané cesty musí být v manifestu před prvním zápisem. Ukládá se celý validovaný manifest, včetně dalších souborů; očekávané soubory se následně nahrávají do Files API a jejich cesty a ID jsou dostupné dalším krokům. Selhání uploadu nevrací lokální zápis zpět. Každá odpověď kroku se eviduje pod společným ID běhu.

## IN, přílohy a vzdálené prostředky

Sken IN aplikuje allow/deny seznamy přípon a masek. Vynechává Git, prostředí, runtime adresáře, cache, symlinky, junctions a rozpoznané snapshoty. Výchozí limit souboru je 10 MiB. Prázdné, binární a nečitelné soubory nejsou nahratelné. Citlivé názvy a rozpoznané vzory tajných údajů sken blokuje, pokud uživatel výslovně nepovolí jejich upload; manifest i tehdy zachovává příznak citlivosti. Detekce je heuristická.

Balíček IN je textový soubor s JSONL položkami `path` a `content`, nikoli ZIP. Načtený obsah musí odpovídat SHA-256 ze skenu a být dekódovatelný jako UTF-8. Celkový limit balíčku je 40 MiB. Podle schopností modelu může aplikace vytvořit i vector store a čekat na indexaci. Ručně vybrané přílohy nepodléhají stejnému skenu. Podporované dokumenty se připojují jako `input_file`, obrázky jako `input_image`. Nepodporovaná příloha nebo neověřitelná metadata běh zastaví. Kontrola velikosti zahrnuje součet příloh; pravidla jsou v [matici požadavků](REQUEST_MATRIX.md).

Běžné běhy mohou vytvářet vzdálené soubory a úložiště pro IN a diagnostiku. Dokončení je automaticky neodstraňuje; spravují se v příslušných panelech. Totéž platí pro přílohy a výstupy kaskád. Validační politika kvůli testu kompatibility nevytváří žádný pomocný soubor, vector store, Response ani Batch. Vlastní pracovní dávkové soubory se po převzetí výsledků odstraňují; neúspěšný úklid je zaznamenaný.

## Komunikace a opakování

Klient používá OpenAI SDK pro vybrané operace a REST pro ostatní nebo při nedostupném SDK. Čtecí operace mohou po selhání SDK použít REST. Chyba mutace SDK se předává jako `OpenAIError` bez druhého provedení přes REST. Seznamy souborů, úložišť a dávek zpracovávají stránkování.

SDK má vypnuté vlastní retry. REST vrstva má nejvýše čtyři pokusy, počáteční prodlevu 0,8 s a strop 8 s; respektuje číselný Retry-After do tohoto stropu. Generující POST `/responses` a vytvoření `/batches` mají jediný pokus. Ostatní operace mohou opakovat timeouty, chyby spojení, HTTP 429 a 5xx. Nad nimi mohou volající používat aplikační retry. Nastavení `response_timeout_s` má výchozí hodnotu 300 s a řídí čekání na odpověď stejně pro SDK i REST. Je dostupné v Nastavení. Časový limit nezaručuje dokončení služby a neprokazuje příčinu její prodlevy.

`model_registry` čte pouze distribuovaný `openai_model_matrix.json`; pravidla nemění katalog účtu, historie chyb ani stažený web. Matice obsahuje přesné modely a snapshoty, endpointy, modality, nástroje, reasoning, sampling, cache a limity. Neznámý model je nepovolený. Úplné osy a zdroje jsou v [matici požadavků](REQUEST_MATRIX.md) a [matici modelů](MODEL_MATRIX.md).

`response_policy` je výhradně **neplacená validační hranice**. Připraví a lokálně ověří payload, ověří model proti katalogu účtu a podle potřeby ne-generativním GET načte metadata již existujícího `file_id`, `previous_response_id` nebo vector store. Nesmí volat generativní transport `_send_response`, nesmí vytvářet zkušební Files upload, nesmí vytvářet zkušební Batch a nesmí persistovat „důkaz“ úspěšného placeného testu. Definitivní podporu konkrétní kombinace na straně poskytovatele tak potvrzuje až skutečný pracovní požadavek; případné odmítnutí se vrátí jako chyba tohoto pracovního požadavku.

Pro BATCH platí stejný princip. Všechny řádky pracovního JSONL se před uploadem deterministicky lokálně validují, včetně jednotného modelu, tvaru payloadu, strict schématu a známých referencí. Potom se nahrává přímo pracovní JSONL a vytváří se přímo pracovní dávka. Mezi lokální validací a pracovní dávkou nesmí existovat zkušební dávka ani jiná placená generativní operace.

`preflight_pending`, `preflight_batches` a historické záznamy zkušebních dávek nejsou součástí aktuálního stavového modelu a nový runtime je nevytváří. Pokud se taková pole nacházejí ve starém LOG z dřívější verze, smí je kompatibilní čtecí kód pouze zobrazit nebo ignorovat; nesmí podle nich vytvořit novou zkušební dávku ani pokračovat v placeném preflight workflow. Historické záznamy se nemigrují do nových pracovních stavů automatickým odesláním.

Lokální kontrola známých parametrů, schémat a cest předchází uploadům pracovních dat. Každý dávkový řádek se kontroluje před uploadem JSONL i před vytvořením vzdálené pracovní dávky. Změnu oprávnění, odstranění prostředku nebo výpadek mezi kontrolou a odesláním nelze vyloučit; takové chyby se evidují odděleně od lokálního odmítnutí. Žádná z těchto možností není důvodem k automatickému placenému testovacímu volání.

Multipart požadavek nemá globální JSON Content-Type a při opakování obnovuje pozici vstupního streamu. Circuit breaker odkládá další pokus po opakovaných chybách. Při ztracené síťové odpovědi není zaručeno právě jedno provedení operace na serveru.

## Souběh, zastavení a zabezpečení

Workery běžných běhů i kaskád při vytvoření pořizují hlubokou kopii konfigurace a nastavení, včetně vnořených seznamů a definic kroků. Následné změny původních objektů neovlivňují běh. Hlavní okno odmítá překrývající se OUT souběžných zapisujících běhů, které samo spravuje; nejde o systémový zámek pro jiné procesy či ruční operace panelů. Dokončení a oznámení používají údaje příslušného běhu.

STOP je kooperativní. Kontrola zastavení probíhá mezi operacemi; probíhající síťový požadavek nebo retry může návrat oddálit. Okno čeká na aktivní workery a nepoužívá násilné ukončení QThread.

SMTP a SSH hesla se ukládají přes OS keyring. Při nedostupném úložišti existuje dočasný fallback v prostředí procesu. Ukládaný JSON obsahuje prázdná pole hesel. Načtená hesla z JSON se přesouvají do úložiště a soubor se při úspěchu přepíše bez nich.

API klíč se před inicializací API panelů načítá z uživatelského záznamu `HKCU\Environment\OPENAI_API_KEY`. Uložená hodnota má přednost před zděděným prostředím procesu; pouze při neexistujícím záznamu se použije proměnná `OPENAI_API_KEY`. Načtená hodnota sjednotí prostředí aktuálního procesu i jednotlivé panely. Chyba čtení se oznámí a aplikace nepoužije potenciálně zastaralý klíč. Uložení nejprve zapíše a zpětně ověří trvalou hodnotu; až po úspěchu aktivuje nový klíč. Při selhání ověření se pokusí obnovit původní záznam a oznámí chybu. Smazání ukládá prázdný záznam, aby starý klíč nemohl znovu ožít ze zděděného prostředí. Restart Windows ani rodičovského terminálu není nutný. Toto umístění není šifrovaným úložištěm API klíče; klíč se nepředává programu `setx` jako argument procesu.

SSH pin je Base64 SHA-256 veřejného host key s volitelným prefixem `SHA256:`. Porovnání rozlišuje velikost písmen. Lokální diagnostika spouští distribuovaný PowerShell kolektor s časovým limitem a kontrolou návratového kódu. Spuštění navržené opravy vyžaduje samostatné potvrzení. SMTP používá nastavené SSL nebo STARTTLS; odeslání testovací zprávy je uživatelskou akcí.

SSH OUT spouští schválený obsah `.sh` přes stdin vzdáleného `sh -s` na hostiteli ze snímku konfigurace běhu. Windows OUT používá lokální `.bat`. Potvrzení uvádí cíl a SHA-256 skriptu; výstupy obou variant mají samostatné logy. SSH vyžaduje známý host key a případný odpovídající pin. SMTP ověřuje certifikát i jméno serveru v SSL a STARTTLS; neplatné hlavičky vracejí chybu bez připojení.

Textový kontrakt odmítá nedokončené a chybové odpovědi, odmítnutí modelu a odpověď bez textu. JSON objekt nesmí obsahovat duplicitní klíče ani nestandardní číselné konstanty. Kaskáda kontroluje modely, teploty, schémata a odkazy na předchozí kroky před první síťovou operací. Chybějící hodnotu odkazu nenahrazuje prázdným textem.

## Logy a evidence

Adresář běhu obsahuje `files`, `requests`, `responses`, `manifests`, `misc`, `events.jsonl` a `run_state.json`. Vytvoření odmítá existující adresář téhož běhu; výslovné pokračování připravené GENERATE dávky znovu otevře její evidenci. Názvy uložených JSON kombinují bezpečný zkrácený název a hash. Recovery používá stejnou kanonickou naming logiku a význam artefaktu neodvozuje pouze ze suffixu názvu. Stav běžného běhu rozlišuje vytvoření, běh, dokončení, zastavení a selhání.

Logy obsahují zadání, odpovědi a případně zdrojový kód. Známá tajná pole a řetězce s Bearer hodnotami se redigují, ale volný text může obsahovat další citlivá data. Logy nejsou šifrované a ovladač šifrování je neaktivní. Správa přístupu a uchování provozních dat je odpovědností provozovatele.

Aplikace neprovádí cenění, předběžné počítání tokenů, finanční kalkulace, potvrzování rozpočtů ani cenové audity. Odpovědi API včetně původních metadat se ukládají do LOG bez finančního vyhodnocení. Zároveň platí tvrdý nákladový invariant: automatická validace nesmí přidávat samostatné generativní požadavky nebo dávky, jejichž jediným účelem je ověřit budoucí pracovní požadavek. Existující provozní databáze a cache se nemažou ani nemigrují; neznámé položky starého nastavení se při načtení ignorují.

## Dokončení uložených dávek a výchozí model

Po úspěšném odeslání pracovní dávky uživatel pokračuje akcí **Dokončit** v Historii nebo Dávkách. Obě místa používají stejnou operaci na pracovním vlákně. Probíhající vzdálená dávka vrátí informaci o čekání; ukončená dávka se převezme do původního OUT bez A1/A2 nebo nového generování. Žádný mezikrok zkušební dávky v aktuálním workflow neexistuje.

`run_state.json` zachovává `batch_records` podle ID pracovní dávky včetně dostupného serverového `created_at`. Přehled páruje `batch_id` a `generate_batches` s místními běhy a jejich projektem; serverový čas zobrazuje v místním pásmu jako `DD.MM.YYYY HH:mm:ss`. Chybějící čas není odvozován z času vzniku běhu. Cizí dávka bez místních podkladů nemá akci dokončení běhu. Starší stav bez `batch_records` zůstává čitelný.

Serverový stav a uložení do OUT jsou oddělené. `batch_imports[batch_id].import_status` eviduje výsledek konkrétního importu, zatímco `status` běhu zohledňuje úplnost výstupu i dosud nepřevzaté opravné dávky. `files_complete_unverified` označuje úplné uložení souborů, nikoli ověření funkčnosti. Částečný import zůstává opakovatelný. MODIFY ověřuje ID jediného požadavku C1, kontrakt C_FILES_ALL a eviduje hashe importovaných souborů; již změněné nebo cizí soubory nepřepisuje. GENERATE používá uložený manifest a ochranu hashů. Dokončení nesmí běžet současně s další operací stejného běhu nebo s aktivním během používajícím překrývající se OUT. Výsledek obnoví Historii i Dávky.

Zrušení zpracování je dostupné pro `validating`, `in_progress` a `finalizing` a vyžaduje potvrzení. Aplikace nemaže serverové soubory ani nenabízí smazání záznamu dávky, které Batch API nepodporuje.

`default_model` je jediná trvalá předvolba modelu. Výběr v Nastavení a akce **Nastavit jako výchozí** u hlavního modelu i v Modely používají stejné ukládání. Změna se projeví v paměti a ovladačích až po úspěšném zápisu. Předvolba platí pro čisté zadání při spuštění aplikace a akci Nové; rozpracované a načtené zadání, explicitní modely A1/A2/A3, ReRun i odeslaná dávka zachovávají vlastní model. Nová volba vyžaduje podporovaný model z katalogu účtu. Uložená nedostupná předvolba zůstane viditelná bez tichého nahrazení.

## Převodník UTF-8

`python -m utf8nobom.py` spouští samostatné Tk rozhraní. Převodník vytváří kopie a ZIP zálohy mimo vstupní adresáře. Překrývající se vstupy deduplikuje, stejně pojmenované adresáře rozlišuje v názvech záloh a existující zálohu nepřepisuje. Git metadata a odkazy nekonvertuje. ZIP s traversal položkou odmítne beze změny; při přepisu zachovává komentář archivu a metadata položek. Oprava kódování je heuristická a výsledek je třeba posoudit podle konkrétních dat; originál zůstává v záloze.

## Distribuce a ověření

Editor kaskády ověřuje model, teplotu, strukturovaný vstup a dostupnost vlastního schématu před změnou uloženého kroku. Externí návaznost je dostupná i v prvním kroku. Dostupnost voleb Batch závisí na režimu: GENERATE má živou přípravu, MODIFY souhrnný požadavek. Minimální explicitní `max_output_tokens` je 16. Kombinace režimů a příznaků vymezuje `docs/REQUEST_MATRIX.md` a reprodukovatelný export `docs/REQUEST_COMBINATIONS.csv`.

Wheel obsahuje balíčky `kajovo`, `kajovong`, `utf8nobom`, diagnostický skript, logo a oba fonty. Testy nejsou distribuovanými balíčky. Fonty ve zdrojovém stromu používají Git LFS. Windows sestavení vytváří `dist/Kajovo/Kajovo.exe`, macOS sestavení `dist/Kajovo.app`; název lze předat sestavovacímu skriptu. Sestavení provádí instalaci závislostí, generování ikon a PyInstaller. Generované adresáře a binární distribuce nejsou zdrojovým kódem.

Repozitář neobsahuje placené `verify_*_live.py` nástroje, které by kvůli samotnému ověření automaticky odesílaly generativní Responses nebo zkušební dávky. CI a standardní validační postupy jsou offline vůči placeným generativním endpointům: používají mocky, lokální validátory a statické kontroly. Ruční spuštění skutečné pracovní funkce aplikace samozřejmě může vytvořit placený pracovní požadavek; ten není validačním preflightem.

| Testy | Ověřované chování |
| --- | --- |
| `test_batch_completion.py`, `test_batch_completion_ui.py` | Převzetí pracovních dávek, historie, ochrana souborů, souběh a výchozí model |
| `test_workflows.py` | Offline režimy, batch, snímky konfigurace a validace výstupu |
| `test_no_paid_preflight.py`, `test_response_policy.py` | Zákaz placených validačních Responses/BATCH a neplacená validační hranice |
| `test_contracts.py`, `test_cascade.py` | JSON, cesty, souborové výstupy, schémata a kaskády |
| `test_filesystem_boundaries.py`, `test_security_regressions.py` | Souborové hranice, citlivé vstupy, hashe a bezpečnost zápisu |
| `test_api_client.py`, `test_retry.py`, `test_text_chunks.py` | HTTP, SDK, stránkování, opakování a dělení textu |
| `test_config.py`, `test_diagnostics.py` | Nastavení, hesla a SSH pin |
| `test_desktop.py`, `test_repository_contract.py` | Importy, Qt, ovládání, prostředky a kódování |
| `test_utf8nobom.py` | Zálohy, deduplikace vstupů a převod ZIP |

Povinné kontroly jsou `python -m pytest -q`, `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build` a `python -m pip check`, spuštěné v projektovém prostředí. CI používá Windows a Python 3.13. Automatické testy nahrazují HTTP a nevyžadují produkční API klíč.

Úspěch kontrol platí pro jejich rozsah. Skutečné pracovní OpenAI požadavky, SMTP, vzdálené SSH, oprávnění účtu, interaktivní provoz a platformní distribuce vyžadují ověření v odpovídajícím prostředí. Automatické testy nejsou zárukou nepřítomnosti všech chyb.
