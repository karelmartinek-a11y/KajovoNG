# KájovoNG — specifikace systému

## Účel a rozsah

KájovoNG je desktopový klient OpenAI Responses API pro vytváření a úpravy textových souborů, dotazy, uživatelské kaskády, dávkové požadavky a hromadné úpravy fotografií. Zahrnuje správu vzdálených souborů a vector stores, forenzní evidenci běhů, Run Explorer, Git, SMTP a diagnostiku Windows a SSH. Součástí projektu je samostatný převodník textů `utf8nobom`.

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
| Běžné běhy | `pipeline.py`, `runs/*` | Qt worker/adaptér a postupně oddělované Qt-nezávislé kontrakty běhu, polling a bezpečný delivery pro GENERATE, MODIFY, QA a QFILE |
| Vlastní kaskády | `cascade_types.py`, `cascade_pipeline.py`, `cascade_log.py` | Kroky, substituce, schémata, soubory a evidence |
| Photo Studio | `photo_types.py`, `photo_templates.py`, `photo_prompt.py`, `photo_batch.py`, `photo_results.py`, `studio/photos.py` | Promptové šablony, profesionální přeformulování zadání a úpravy fotografií pouze přes Image Edit BATCH |
| API | `openai_client.py`, `response_policy.py`, `retry.py`, `compat.py`, `model_capabilities.py` | SDK/REST, neplacená validační politika, chyby, stránkování, opakování a schopnosti modelů |
| Souborové hranice | `contracts.py`, `filescan.py`, `utils.py` | Kontrakty, sken vstupu, validace cest, hashe a zápisy |
| Evidence | `runlog.py`, `run_bundle.py`, `recoverable_artifacts.py` | Runtime stav, bezeztrátová kanonická evidence, request/response, kroky, artefakty, checkpointy, lineage a integrita |
| Historie | `studio/history*.py`, `studio/evidence.py` | Run Studio, virtuální DAW timeline, typové detaily, artefakty, centrální politika akcí a přímé bezpečné větvení |
| Nastavení | `config.py`, `secret_store.py`, `resources.py` | Konfigurace, hesla a prostředky |
| Diagnostika | `diagnostics/windows.py`, `diagnostics/ssh.py`, `notifications.py` | Sběr diagnostiky a SMTP |
| Desktop | `kajovo/studio` | Hlavní okno, panely, dialogy a workery Qt |
| Převod textů | `utf8nobom/app.py`, `utf8nobom/py.py` | Qt rozhraní v `studio/converter.py`, zálohy a převod souborů i položek ZIP |

Názvy modulů bez adresáře označují soubory pod `kajovo/core`.

## Uživatelské rozhraní

Hlavní navigace obsahuje Zadání, Fotografie, Komiks, Kaskády, Zdroje, Dávky, Historii, Verze projektu, Modely, Nastavení a Nápovědu. Run Studio je pouze pracovní koncept Historie; globální produkt zůstává KájovoNG / Řídicí studio. Zadání odděluje prompt, parametry a adresáře, diagnostiku a výsledek. Spustit, Zastavit a přístup k aktivním běhům zůstávají mimo posuvný obsah. Teplota běhu a výchozí teplota mají samostatné ovladače. Nastavení a Dávky lze otevřít i v samostatném okně se stejným obsahem a stavem. [Inventář a návrh UI](UI_DESIGN.md) popisuje pokrytí funkcí a odkazy na validační matice.

Rozhraní v `kajovo/studio` používá tmavou pracovní plochu, azurový akcent a Montserrat 11 bodů. Produkční továrna `create_window` je společná testům a snímkování a neimportuje předchozí UI. Formuláře, navigace i skupiny vedlejších akcí dovolují posuv na malé logické ploše; hlavní akce Zadání zůstávají dosažitelné. Samostatná okna vracejí stejné widgety a jejich stav do studia. Skrytí průběhu nemění životnost pracovníka. Správce operací rezervuje překrývající se výstupní adresáře až do skutečného ukončení vlákna.

České chybové sdělení vychází z doloženého kódu či konkrétní výjimky; bez důkazu uvádí neznámou příčinu. Vzdálené dokončení dávky neznamená místní převzetí. Historie nepovolí nové pokračování z běhu s již odeslanou dávkou. Opětovné použití artefaktu vyžaduje povolení a správný otisk a vytváří izolovaný vstupní adresář. Klon nepřebírá identifikátor předchozí odpovědi.

`ProgressEvent` přenáší etapu, stav, dokončený počet, celkový počet, jednotku, detail a monotónní čas události. Text logu ani pevně vážená procenta neurčují dokončení. A3/B3 započítávají soubor po získání a ověření celého obsahu; zápis na disk je samostatná etapa. Kaskáda započítává dokončené kroky. API bez měřitelného postupu používá neurčitý indikátor. Trvání a stáří poslední události se obnovují každou sekundu; tato obnova nedokazuje aktivitu poskytovatele. ETA vzniká po třech dokončených srovnatelných jednotkách z mediánu posledních pěti dob, pouze pro aktuální etapu. Změna etapy vzorky resetuje. Ukončení, chyba, zrušení a předání do Batch mají odlišné stavy; koncové indikátory již neanimují práci.

Dokončené průběhové okno ponechává výsledek a nabízí výchozí tlačítko **OK**, které pouze skryje nebo zavře okno. Akce Zastavit a Zrušit již nejsou viditelné. Ve studiu tento přechod nastává až po skutečném ukončení pracovníka a převzetí výsledku, nikoli dosažením 100 % nebo samotnou koncovou událostí. Skrytý průběh se dokončením sám neotevírá; při opětovném otevření nabízí OK. Opakované sledování obnoví ovládání běžící operace. Dokončení místního sledování nemění význam vzdáleného stavu dávky. Úvodní načítací okno se nadále zavírá automaticky.

BATCH zobrazuje počet zpracovaných úloh včetně chyb, čas poslední úspěšné aktualizace stavu a další naplánovanou kontrolu. ETA vzdálené fronty není odhadována. Dokončení API není potvrzením zápisu do OUT. Uploadový dialog povolí zavření po zrušení až po potvrzení konce workerem.

Síťové operace panelů, Git příkazy a SMTP test běží v asynchronních workerech. Správce Jobs drží worker do signálu finished; teprve poté předá výsledek UI a případně zahájí navazující obnovu. Správa zdrojů a Git blokuje konfliktní akce po dobu operace. Průběžné textové logy mají limit 2000 bloků; uložená evidence tím není omezena.

Zdroje obsahují soubory API a vector stores. Odpojení souboru od úložiště a odstranění z Files API jsou odlišné operace. Modely filtrují katalog podle pevné matice a zobrazují pravidla. Neplacený účtový katalog `GET /models` se po úspěšném načtení ukládá do `cache/model_catalog.json` pod jednosměrným otiskem API klíče; samotný klíč se do cache nezapisuje. Cache ukládá bezpečná metadata účtového záznamu a auditní kopii detailu z aktuální pevné matice, ale při čtení se schopnosti vždy znovu odvodí z aktuální distribuované matice. Cache proto nikdy nepřepisuje ani nerozšiřuje povolené capability. UI používá centrální profily použití a v každém modelovém selectoru zobrazuje pouze modely současně dostupné účtu a kompatibilní s daným workflow. Po načtení či obnovení katalogu vybere doporučený kompatibilní model pro daný účel; explicitní tlačítko Obnovit katalog provede nový GET. Historie je Run Studio nad odvozeným `HistoryIndex` a kanonickým Run Bundle; zobrazuje virtuální stopy StepRecordů, typové detaily, lidské i technické odpovědi, soubory, události, návaznosti a integritu. Legacy běhy se otevírají read-only bez domýšlení chybějících faktů.

GITHUB pracuje s lokálním repozitářem a příkazy Git. Obnovení stavu nepřepisuje remote. Zápis, commit, synchronizaci a změnu remote vyvolávají příslušné uživatelské akce. Pull používá aktuální větev a fast-forward. Příkazy mají časový limit a nepovolují interaktivní terminálový prompt. Editor dovoluje uložit pouze úspěšně načtený UTF-8 soubor.

### Modelový katalog a doporučení

Účtová dostupnost modelů a dokumentované capability jsou dvě různé vrstvy. `StudioContext` načte pro známý účet cache dostupnosti bez sítě a při prvním běhu s klíčem naplánuje bezpečný ne-generativní `GET /models`. Záznam cache je oddělen otiskem účtu. `model_registry` je jediný zdroj pravidel kompatibility a definuje profily minimálně pro Responses, GENERATE A1/A2/A3, A3 Batch, QA, QFILE, KASKÁDU, profesionalizaci fotografického promptu a Image Edit BATCH. Doporučení je deterministické pořadí uvnitř kompatibilní množiny; model mimo účtový katalog nebo mimo pevnou matici se do pracovního selectoru nedostane.

## Konfigurace a provozní data

| Výchozí umístění | Obsah |
| --- | --- |
| `kajovo_settings.json` | Nastavení; vzor je `kajovo_settings.example.json` |
| `LOG/RUN_DDMMYYYYHHMM_XXXX` | Data běhu a Run Bundle; poslední čtyři znaky jsou náhodné |
| `LOG/history_index.json` | Rebuildovatelný read-model Historie; není kanonickou evidencí |
| `LOG/ui_session.log` | Zprávy desktopového rozhraní |
| `kajovo/core/openai_model_matrix.json` | Pevná verzovaná pravidla všech doložených modelů |
| `cache/model_catalog.json` | Účtově oddělená cache bezpečných metadat GET /models a odvozených detailů pevné matice |
| `cache/cascades` | Definice vlastních kaskád |

`AppSettings` umožňuje změnit umístění databáze, LOG a cache. Načítání vyžaduje JSON objekt, kontroluje typy, konečnost čísel a rozsahy; neznámé klíče ignoruje. Seznamy allow/deny mohou být `null`. Výchozí teplota je 0,2, povolený rozsah 0 až 2. Port SMTP musí být 1 až 65535.

Výchozí retry má šest pokusů, počáteční prodlevu 0,8 s, strop 20 s, jitter do 0,25 s a circuit breaker po šesti chybách s prodlevou 20 s. SMTP má port 587 a STARTTLS.

`logging.max_total_mb` a `logging.max_runs` se ukládají a validují, ale implementace podle nich automaticky nemaže logy. Panel BATCH načítá seznam na pracovním vlákně a při nedokončených dávkách opakuje načtení podle `batch_poll_interval_s`. Po `batch_timeout_s` sledování skončí, aniž by zrušilo vzdálenou dávku; UI uvede, že vypršel pouze lokální monitorovací limit, a ruční obnovení zahájí nové sledování. `security.allow_upload_sensitive` výslovně povoluje soubory zachycené heuristikou citlivých názvů a obsahu. Ostatní filtry skenu zůstávají účinné. Toto nastavení se týká uploadové hranice a nemění obsah kanonické evidence běhu.

## Běhy a souborové kontrakty

Každá přípravná fáze GENERATE/MODIFY se ověřuje před přijetím do kanonického snapshotu. A0R/B0R kontroluje schéma, záměr a jednoznačné neprázdné identifikátory a popisy požadavků. A1/B1 samostatně kontroluje identitu a odpovědnosti architektury, platnost odkazů a pokrytí požadavků. Vadný plán nesmí spustit A2/B2. Obnova kontroluje i částečný snapshot requirements nebo plánu před sítí; historické záznamy se automaticky neopravují.

`requirement_ids` odkazují výhradně na explicitní a implicitní požadavky. Schéma pracovního požadavku omezuje reference na aktuální registr ID; identifikátory architektury mají vlastní registr. Akceptační kritéria, invarianty a předpoklady zůstávají zachované pomocí zdrojových odkazů a působnosti implementačních kontraktů. Neplatné odkazy se nesmějí opravovat zahazováním požadavků.

Oprava se vrací do fáze, která vytváří vadný podklad. Každá přípravná fáze má nejvýše tři pokusy včetně prvního; opakování stejného vadného kandidáta se stejnými nálezy skončí dříve. Opravný požadavek nahrazuje pouze kandidáta své fáze a zachovává původní zadání, přílohy a schválené předchůdce. Chyby JSON a schématu procházejí stejným řízením. `ValidationIssue` obsahuje kód, původní fázi, JSON pointer, zprávu, očekávání a skutečnost. Nezávislé chyby schématu a plánu se předávají společně.

Evidence uchovává každý pokus a validaci včetně neúspěšných; úspěšná oprava odkazuje na vyřešené nálezy. API stav `completed` je stav přijaté odpovědi, nikoli schválení pracovní fáze. Příprava a živá souborová generace mají do aplikační validace stav `validating_result`, během opravy `repairing`. Souborová fáze končí až po ověření zápisů nebo dokončeném dry-run. Nové běhy zaznamenávají verzi aplikace, verzi procesního kontraktu a dostupné otisky zdrojů orchestrace; starším běhům se původ kódu nedoplňuje odhadem.

`RemoteResponseError` zachovává vzdálený kód, stav, důvod neúplnosti a identifikátory požadavku a odpovědi; dávková chyba také custom_id a cestu souboru. `failure_detail` ukládá stejné české vysvětlení pro chybové okno a Historii. Chyba pollingu a následné zotavení jsou samostatné události. Neznámý vzdálený výsledek se nesmí zaměnit za bezpečné nové odeslání.

Úplná živá i dávková dodávka souborů končí `files_complete_unverified`: potvrzuje souborové kontrakty a zápisy, nikoli spuštění, sestavení nebo funkčnost výsledného programu. MODIFY bez navržených změn může skončit `completed` s `no_changes`; dry-run nezapisuje OUT. Prázdný soubor vyžaduje explicitní `allow_empty` implementačního kontraktu; prázdný historický výsledek bez tohoto oprávnění vyžaduje posouzení. Chybějící, konfliktní, neúplné a nepodporované soubory nesmějí vést k úplné dodávce. Historie nabízí samostatnou asynchronní kontrolu současných souborů OUT: rozlišuje shodu otisku, změnu, chybějící soubor a neověřitelný stav. Kontrola nepřepisuje historickou evidenci ani výstupy.

| Režim | Zpracování | Výsledek |
| --- | --- | --- |
| GENERATE | A0R_REQUIREMENTS → A1_PLAN → A2_STRUCTURE → volitelně A2Q_QUALITY_GATE → A3_FILE | Textové soubory v OUT |
| MODIFY | B0R_REQUIREMENTS → B1_PLAN → B2_STRUCTURE → volitelně B2Q_QUALITY_GATE → B3_FILE | Změny souborů v OUT; podporuje dry-run |
| QA | Jeden požadavek Responses | Text odpovědi |
| QFILE | Jeden požadavek s kontraktem A3_FILE | Jeden úplný textový soubor v OUT |
| GENERATE BATCH | A0R/A1/A2 a případný A2Q živě, pouze A3_FILE v dávce | ID dávky, kontrolovaný import v BATCH |
| MODIFY BATCH | B0R/B1/B2 a případný B2Q živě, pouze B3_FILE v dávce | ID dávky, kontrolovaný import v BATCH |
| KASKÁDA | Seřazené kroky uživatelské definice | Odpovědi, JSON a očekávané soubory kroků |

Dlouhé zadání se ukládá přesně lokálně jako obnovitelný artefakt. Technický A0 neposílá generativní potvrzení částí; celý vstup přijímá pracovní A0R/B0R. Návaznost přípravy a její kanonické artefakty zůstávají zachované.

Všechny přípravné fáze včetně A0R/B0R jsou samostatné požadavky bez `previous_response_id` a bez připojené serverové historie. Pracují s projekcemi zmrazeného SourcePacku a kanonických podkladů: A0R dostává zdrojové segmenty, A1 requirements a zdroje, SPINE požadavky a plán, DETAIL pouze cílový soubor a relevantní požadavky, akceptaci a rozhraní. MODIFY navíc používá zmrazený inventář a příslušné originály; B2 DETAIL dostává úplný originál upravovaného cíle. Quality gate posuzuje requirements, plán a aktuální implementační graf. Celé původní zadání ani všechny přílohy nejsou automaticky připojovány do každého kroku. Oprava zachovává původní projekci své fáze a poslední chybný kandidát s diagnostikou, nenabaluje historii pokusů. Nová obnova přijímá pouze validní checkpoint V2; čitelnost legacy snapshotu V1 není oprávnění spustit z něj novou V2 přípravu.

Každý přípravný požadavek včetně oprav před odesláním projde `context_limits.preparation_measurement`. Zachová zvolený model a quality policy, respektuje skutečné `context_window`, samostatný vstupní limit a `max_output_tokens` z capability registry a zakazuje truncation. Při přílohách nebo vnější historii je povinné ne-generativní měření skutečného vstupu vázané na hash požadavku. Nedostupné, neplatné nebo nadlimitní měření blokuje generování. Budoucí obsah retrievalu nelze přesně předem změřit; zůstává označenou technickou nejistotou, nikoli garancí dokončení.

Žádný samostatný placený preflight se před pracovní operací neposílá. Lokální validace, čtení existujících metadat a samostatné ne-generativní měření tokenů nejsou generativní sondou.

### Requirements, kvalita a obnova přípravy

Plán V2 nesmí přijmout komponentu s prázdnou nebo pouze bílými znaky vyplněnou `responsibility`; neplatný plán zůstává před SPINE a jeho opakování podléhá `NO_PROGRESS`. Číslo opravného pokusu se vkládá do metadata `kajovo_repair_attempt` ještě před měřením a archivací requestu, takže `retry_attempt` v Run Bundle odpovídá skutečně odeslanému pokusu.

Nová příprava GENERATE/MODIFY používá `orchestration/preparation.py`: requirements a plán V2, `A2_SPINE_V1`/`B2_SPINE_V1` a samostatný `A2_FILE_SPEC_V1`/`B2_FILE_SPEC_V1` pro každý vyráběný textový soubor. Lokálně sestavuje `IMPLEMENTATION_GRAPH_V3`. Runtime checkpoint má verzi 2, `source_snapshot_hash`, requirements, plán, SPINE, file_specs, graf, kanonickou etapu, response ID a hash celého snapshotu bez vlastního hash pole. Obnova ověřuje režim, quality policy, zdrojový hash i uložené kontrakty; přijaté části znovu negeneruje. Hotový A2/B2 při zapnuté kvalitě obnoví pouze dosud nedokončený A2Q/B2Q. Legacy snapshot V1 zůstává čitelný starým validátorem, není však checkpointem nové přípravy.

SPINE se před sémantickou validací připravuje na kopii: chybějící kontraktní dependency se lokálně doplní pouze pro jediného providera potvrzeného současně souborovým `provides` a registrem rozhraní. Existující vazby a jejich pořadí zůstávají zachované; nevzniká duplicita, self-dependency ani odhad při více providerech. `content_dependencies` a režim `verified_content` se takto neodvozují. Neznámé bindings, nedostupní provideři v dependencies a nesoulad deklarací provider/consumer se hlásí společně. Oprava dostává původní vstup, souhrn zjištěných vztahových chyb a posledního připraveného kandidáta. Původní odpověď zůstává nezměněná v evidenci; `A2_SPINE_prepared_candidate_*`/`B2_SPINE_prepared_candidate_*` ukládají připravenou kopii a doplněné vazby. LIVE i BATCH pokračují až z validovaného grafu.

Každá fáze přípravy má nejvýše tři pokusy včetně prvního. Druhý výskyt stejné dvojice hash kandidáta a podpis validační chyby ukončí fázi s `NO_PROGRESS`; další generování ani pracovní dávka se nespustí. Provider response ID do identity kandidáta nevstupuje. Pravidlo zahrnuje sémanticky neplatná data i dokončenou odpověď odmítnutou JSON parserem nebo drátovým schématem. Odlišný kandidát se stejnou chybou sám o sobě není opakováním stejné dvojice. Evidence neúspěšné validace obsahuje číslo pokusu, hash kandidáta, podpis chyby a příznak `no_progress`.

Aktuální přípravné prompty a strict kontrakty definuje `orchestration/preparation.py`. `instructions` obsahuje společné a odborné instrukce fáze; její zdrojová projekce a kanonické podklady patří do `input`. Schéma se vynucuje přes `text.format`, neduplikuje se celé v instrukcích. Instrukce požadují zachování záměru bez nesouvisejícího rozšíření produktu a blokaci při chybějícím zásadním podkladu. Souborová výroba dodává úplný obsah, přípravné kontrakty neurčují technické dělení odpovědi na chunky.

A0R V2 vrací `product_intent`, requirements s `id`, `kind`, `statement`, přesnými `source_refs`, `derived_from`, `necessity`, prioritou a `acceptance_ids`; dále invarianty, flows, lifecycles, acceptance, assumptions a out_of_scope. B0R V2 obaluje tyto požadavky do `change_requirements` a přidává `preserve` a `migration_requirements`. Výstup má explicitní variantu `ready` nebo `blocked`; model nesmí chybějící zdrojové reference vymýšlet.

A1 V2 definuje projekt, `components`, rozhodnutí, balíčky, integrační pravidla a verification intents. B1 V2 přidává disjunktní `files_to_add`, `files_to_modify`, `preserved_files` a baseline findings odpovídající inventáři. SPINE mapuje soubory přes `component_id` a `requirement_ids`, deklaruje rozhraní a akce; DETAIL váže implementační chování na akceptaci a testovací scénáře. Kontrola vyžaduje pokrytí mandatory požadavků, platné reference a poskytovatele rozhraní. Tyto kontroly ověřují deklarované vazby; nepotvrzují chování vygenerované implementace. Starší pole `description`, `architecture_items`, `architecture_item_ids`, `touched_files` a kontrakty `A2_STRUCTURE`/`B2_STRUCTURE` v legacy validátorech nejsou vstupním ani výstupním kontraktem nové V2 přípravy.

`maximum_quality` je boolean s výchozí hodnotou `false`. Standard používá requirements i společné instrukce a nemění běžnou politiku reasoning. Maximum Quality vybírá nejvyšší podporovaný effort z pevné matice skutečného modelu kroku; pořadí je `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. Model bez reasoning nedostane tento parametr; nepřípustné sampling parametry se vynechají. A2Q/B2Q je další živé pracovní volání, nikoli test kompatibility. Aktuální kontrakt `A2Q_QUALITY_GATE_V2`/`B2Q_QUALITY_GATE_V2` vrací `corrected_spine`, `corrected_file_specs` a `findings`. Opravený graf znovu prochází validací a až poté nahradí kanonickou přípravu pro A3/B3. Standard tento průchod nevolá. QA, QFILE a KASKÁDA tuto volbu nepoužívají.

Čtecí legacy validátor v `delivery_preparation.py` zachovává podporu snapshotu s `version: 1`, `mode`, `maximum_quality`, `prompt_hash`, `canonical_stage`, `requirements`, `plan`, `structure`, `response_id` a `snapshot_hash`. To je historický datový formát, nikoli formát nově ukládané přípravy. Aktuální `preparation_snapshot` V2 ukládá `orchestration/preparation.py` po každé přijaté fázi včetně jednotlivých DETAIL objektů; nedokončené části zůstávají explicitně nedokončené. Verze přípravného snímku není verzí dávkového manifestu ani souborového VERSING snapshotu.

Nové standardní běhy vytvářejí před prvním síťovým požadavkem `input_ready`; GENERATE/MODIFY navíc zachovávají preparation checkpointy. KASKADA vytváří `cascade_input_ready` a checkpoint po každém dokončeném kroku. Každý safe CheckpointRecord obsahuje stavový hash, compatibility version, invalidation rules a potřebné artifact/response vazby. Starším legacy běhům se checkpointy nedopočítávají.

Akce Historie `Pokračovat`, `Znovu spustit` a `Opravit` zobrazí čistě lokální preview, validují checkpoint, vytvoří nové Run ID a LineageRecord a ihned spustí existující worker přes Operations přímo v Run Studiu. Workbench se nenaplňuje a aplikace se nepřepíná do Zadání. Zdrojový běh zůstává neměnný a stejné potvrzení nelze odeslat dvakrát. Opravný pokyn je evidován v novém běhu a vstupuje pouze do nově prováděné části. Jedině `Klonovat jako nové zadání` otevře Workbench; načte přesný `ui_state`, odstraní Response ID a recovery metadata a lineage `clone` zapíše až při pozdějším startu.

`skip_paths` dovoluje přeskočit pouze dokončené výstupy s doloženým SHA-256 v `completed_hashes`. Jádro ověřuje hashe před API operacemi a znovu před zápisem nebo odesláním dávky; chybějící či změněný soubor běh zastaví. Ověřené zápisy se přenášejí do evidence pokračujícího běhu. Dávkový manifest uchovává `completed_hashes` mimo digest specifikace a tyto soubory nepočítá do `omitted`; import jejich existenci a obsah znovu ověřuje. Pokud už nejsou potřeba žádné souborové úlohy, nevytváří se prázdná dávka.

MODIFY vyžaduje existující vstupní adresář IN v LIVE i BATCH. UI i jádro jej kontrolují před pracovními uploady a generováním. Před B3 se ověřují akce proti skutečnému IN a dostupnost zachovaných souborů ve schváleném skenu, včetně jejich hashů. Neexistující zachovaný soubor nesmí pokrýt požadavek ani odůvodnit výsledek bez změn. Binární a vyloučené výstupy zůstávají nedodanými položkami se stavem `partial`. Dry-run zachovává návrh v LOG, nemění OUT a při úplném výsledku končí stavem `dry_run`; UI podle něj nespouští opravy OUT ani neoznamuje zápis souborů.

Každý generující pracovní požadavek používá `text.format.type=json_schema` a `strict=true`, včetně QA, funkční přípravy schémat a jednotlivých řádků Batch. Schéma určuje kontrakt kroku, nikoli pevný seznam modelů. QA používá `QA_ANSWER_V2` s výsledkem ready/blocked; souborová výroba používá `FILE_CONTENT_V1` s jediným polem `content`. Před odesláním se lokálně ověřuje konzervativní podporovaná podmnožina schémat, uzavření objektů, povinné vlastnosti, typy, lokální odkazy a velikost. Odpověď vyžaduje `status=completed`, nesmí obsahovat odmítnutí ani chybu a musí celá projít JSON parserem a lokálním schématem. Odmítnutí a neúplná odpověď nepostupují k zápisu souborů.

Parser vyžaduje JSON objekt; dovoluje jeho extrakci z okolního textu. JSON pole samo o sobě není souborovým kontraktem. Manifesty obsahují relativní `path`; manifesty zápisu vyžadují přítomný textový `content`. Explicitní prázdný řetězec je platný obsah, chybějící pole, `null` a jiné typy se odmítají před prvním zápisem.

Nové A3/B3 LIVE i BATCH používají `FILE_CONTENT_V1`: pouze textový `content` bez dalších polí. Cesta, akce a expected hash patří do důvěryhodného WorkOrderu. Model neřídí transportní dělení souboru a žádná další response není pokračováním souboru. Pokud se očekávaný úplný soubor nevejde do maximálního výstupu modelu, příprava musí odpovědnost rozdělit do menších souborů místo skládání modelových chunků. JSON Schema i následná lokální validace vynucují tentýž kontrakt. Lokální vady obsahu, dodatečná metadata i nepovolený prázdný soubor se odmítají; opravná smyčka má nejvýše tři pokusy a nesmí opakovat neurčitý síťový submit. QFILE také používá `FILE_CONTENT_V1`, případný návrh názvu odděluje do `QFILE_PLAN_V1`; SEND AS BATCH pro něj není dostupné. Historické obálky `A3_FILE`/`B3_FILE` s contract/path/action/chunking jsou podporovány pouze odpovídajícím čtecím adaptérem, nevyžadují se od nové výroby.

GENERATE v A3 automaticky nedodává položky označené `kind=binary`, PNG/JPG/JPEG ani typy explicitně vyloučené z automatického generování; všechny takové očekávané položky zapisuje do `MISSINGFILES.md`. Pokud po běhu zůstává alespoň jeden takový nedodaný očekávaný soubor, terminální stav LIVE GENERATE je `partial`; `completed` neznamená úplný výstup, pokud evidence obsahuje `missing_deliverables`. ReRun, který díky doloženým již existujícím zápisům nemusí vytvářet nový soubor, eviduje `no_changes`. MODIFY přijímá akce `add` a `modify`, jiné akce odmítá. Při dry-run změny OUT neprovádí.

### Cesty a zápisy

Souborové dávky GENERATE a MODIFY sdílejí evidenci `generate_batch`/`generate_batches`. Nový manifest verze 3 obsahuje `mode`, auditní snímek a samostatné FileContexty. GENERATE vytváří A3_FILE, MODIFY B3_FILE pro textové položky `touched_files`, s pevnou akcí a přesnými relevantními zdroji. Instrukce obsahují CORE a příslušnou fázi; schéma se vynucuje přes `text.format`. Import zachovává kompatibilitu s manifesty verze 1/2 a historickými C_FILES_ALL dávkami.

Jádro připojuje k manifestu `overwrite_hashes`, `dry_run` a `versing` mimo digest specifikace. Import ověřuje kontrakty a ochranné hashe před zápisem. Dry-run eviduje validované `planned_files`, nemění OUT ani nevytváří snapshot a při úplném výsledku končí stavem `dry_run`. Zapnuté verzování vytvoří snapshot OUT před první skutečnou změnou; totožný opakovaný import další snapshot nevytváří. Opravná dávka zachovává režim, specifikaci, původní obsah a ochranné hashe, aniž by měnila zdrojový manifest.

Cesty nesmějí obsahovat absolutní umístění, disk či UNC, traversal, prázdné segmenty, nepovolené znaky Windows, rezervovaná zařízení ani segmenty končící tečkou nebo mezerou. Souborový manifest nepovoluje zpětná lomítka, duplicity bez ohledu na velikost písmen ani konflikt souboru s jeho podadresářem. Vyhodnocená cesta musí zůstat pod OUT i po rozvinutí souborových odkazů.

Manifest zápisu se validuje před první změnou jeho souborů. `atomic_write_text` zapisuje UTF-8 bez BOM do dočasného souboru ve stejném adresáři, provede flush/fsync a atomické nahrazení. Při nahrazování existujícího souboru přebírá jeho režim oprávnění. Vícesouborová operace není transakcí: chyba disku nebo přerušení mezi zápisy může ponechat část změn. Validace cesty neuzamyká souborový systém proti souběžným změnám jiným procesem.

VERSING vytvoří uvnitř OUT snapshot pojmenovaný názvem kořene a suffixem `DDMMYYYYHHMMSS`. Vynechává prostředí, LOG a rozpoznané snapshoty; odkazy kopíruje jako odkazy. Existující snapshot nepřepisuje. ReRun používá uloženou strukturu a návaznost; soubor přeskočí pouze při doloženém zápisu a existujícím výstupu, nikoli na základě samotného návrhu obsahu v odpovědi.

### Batch

GENERATE i MODIFY BATCH provede živou requirements syntézu, plán, strukturu a případný quality gate. Nová struktura musí obsahovat implementační kontrakt v1. Deterministický Context Compiler vytvoří samostatný A3_FILE/B3_FILE pouze z povinností cíle a přímých kontraktů. Každý řádek má custom_id, POST /v1/responses a body bez tools nebo previous_response_id. Globální auditní snapshot zůstává v manifestu jednou; není obsahem jednotlivých requestů. B3 zachovává přesný původní cíl a přímé zdrojové závislosti s hashovou vazbou. Detaily a hranice ověření popisuje [Context Compiler](CONTEXT_COMPILER.md).

Před pracovní dávkou se **nevytváří žádný pomocný JSONL, žádný testovací Files upload a žádný testovací `POST /batches`**. Po lokální validaci existuje právě jeden pracovní vstupní soubor a právě jeden pokus o vytvoření pracovní dávky. Neurčitý výsledek tohoto jediného pracovního `POST /batches` se eviduje jako `submission_unknown` a před jakýmkoli dalším pracovním submittem se musí dohledat podle přesného `input_file_id`; automatické opakování neurčitého vytvoření je zakázané.

GENERATE i MODIFY BATCH dovoluje response_id, technický A0, přílohy, připojená úložiště/file search a diagnostiku IN v živé přípravě. Modely přípravy musí podporovat návaznost a použité přílohy/nástroje podle pevné matice. Příprava převádí potřebné závěry do samostatné specifikace pro dávku. Diagnostika OUT je při odesílání dávky zakázaná. Akce „Zrušit zpracování“ volá endpoint cancel a nemaže dávku.

A2 verze 2 obsahuje společná pravidla, balíčky s verzemi, rozhraní s jedinečnými ID a přesnými definicemi a soubory s poli `path`, `purpose`, `language`, `kind`, `dependencies`, `provides`, `requires`, `behavior`. Souborové závislosti odkazují jen na jiné položky manifestu; rozhraní jen na definovaná ID. Chybějící poskytovatel nebo neznámý odkaz blokuje A3. Neplatný A2 může vyvolat nejvýše dvě opravná živá volání, protože tato volání opravují skutečný pracovní artefakt A2 a nejsou samostatným testem kompatibility. Binární a vynechané soubory jsou evidovány jako nedodané.

Před validací každé nové odpovědi A2 v LIVE i BATCH probíhá `prepare_structure`: na kopii specifikace doplní chybějící `dependencies`, pokud vyžadované rozhraní deklaruje v `provides` právě jeden soubor. Zachovává existující vazby a jejich pořadí, nevytváří duplicity ani vlastní závislost. Vlastní poskytované rozhraní nebo již uvedený poskytovatel další vazbu nepotřebuje. Při více poskytovatelích bez existující volby se žádný nevybírá odhadem. Příprava nemění neznámé odkazy ani nevymýšlí chybějící rozhraní či poskytovatele.

`validate_structure` zůstává přísný a původní manifest neopravuje. Po ověření tvaru a jednoznačnosti identifikátorů hlásí vztahové chyby souhrnně, s cestami souborů, identifikátory rozhraní a dostupnými poskytovateli. Opravné volání dostává všechny zjištěné problémy i aktuální připravený manifest; dvě opravná volání jsou horní mez, nikoli počet opravovaných vazeb. Jednoznačné doplnění nevyžaduje další API volání. Původní odpovědi zůstávají zachované, ověřené kandidáty `A2_prepared_candidate_*` a kanonický `preparation_snapshot` ukládá příprava samostatně s evidencí doplněných vazeb. Následující BATCH používá připravenou strukturu ve společném snímku. Již uložené dávkové manifesty, požadavky a jejich hashe se zpětně nenormalizují. Nově vytvářená opravná dávka používá aktuální souborové instrukce pro jedinou úplnou část, přičemž zachovává specifikaci původního manifestu.

`generate_batch` je společná evidence GENERATE/MODIFY. Nový manifest má `version: 3`, `mode`, neměnný snapshot se SHA-256, FileContexty, requesty, mapování cest, měření a plán dependency vln. Verze 1 a 2 zůstávají čitelné pro import podle vlastních kontraktů. `encode_requests` znovu sestaví pracovní kontext a ověří jeho shodu i hash původních zdrojů. Jedna dávka má jeden model, 1 až 50 000 řádků a nejvýše 200 MB. A3/B3 Batch vyžaduje index 0, počet částí 1, `has_more: false` a `next_chunk_index: null`. Incomplete a nepovolený prázdný obsah se neimportují.

Hybridní import běží na pracovním vlákně a stáhne výstupní i chybové JSONL do evidence běhu. Nejprve kontroluje ID celé dávky; neznámé ID zastaví import, duplicita zneplatní daný soubor. Cestu porovnává s uloženým manifestem. Úspěšné položky zapisuje atomicky, existující změněný obsah zachovává. Stav je `partial` nebo `files_complete_unverified`; druhý stav nepotvrzuje sestavení ani funkčnost. Hash souborů chrání opakovaný import i následné uživatelské změny.

Ruční opakování a oprava vybraných cest manifestu v3 zachovává kanonické kontrakty a přidává konkrétní feedback a aktuální soubor. Neopakuje přípravu ani jiné soubory. Předchozí neurčitý submit blokuje další odeslání. Legacy manifest vyžaduje novou implementační přípravu; nikdy se tiše neposílá původní globální snapshot. Import failure sám generování nespouští. `generate_batches` a `batch_imports` zachovávají vztahy a výsledky přes restart.

Stažení preferuje OUT uložené u souvisejícího běhu, poté OUT panelu nebo adresář vybraný uživatelem. Uchovává nezpracovaný `batch_<id>_output.jsonl`, zpracovává souborové kontrakty a eviduje jednotlivá response ID. Neúplné nebo duplicitní části souboru hlásí jako chybu. Chyba jedné položky nevrací zpět již uložené položky. Stažení dávky není vícesouborovou transakcí.

Počet částí souboru je celé číslo 0 až 5001; nula označuje dosud neurčený počet. Index části je 0 až 5000. Kladný deklarovaný počet se mezi odpověďmi nesmí měnit a musí souhlasit s koncovou částí. `has_more` je boolean, pokračování odkazuje na následující index a poslední část má `next_chunk_index: null`. Stejná kontrola platí pro synchronní generování i import dávky. Import připouští části v jiném pořadí, ale odmítá rozporné konce a kolize cílových cest bez ohledu na velikost písmen. Již uložený výsledek téže dávky jiná položka nepřepisuje. Balíček musí mít explicitní seznam `files`; neplatný typ se nepovažuje za prázdný výsledek.

## Vlastní kaskády

`CascadeDefinition` obsahuje název, verzi, časy, `default_out_dir` a kroky. Prázdnou kaskádu worker odmítne. Každý `CascadeStep` má model, volitelnou teplotu, instrukce, vstupní text nebo strukturovaný `input_content_json`, existující ID a lokální cesty příloh, výraz návaznosti, typ výstupu, schéma a `expected_out_files`.

Načítání vyžaduje textové typy názvů, cest, modelů a zadání. Výraz návaznosti může být text nebo null. Verze musí být kladné celé číslo, časové údaje konečná nezáporná čísla; nula se při uložení zachovává. Poškozené hodnoty se nenahrazují aktuálním časem, výchozí verzí ani textovou reprezentací objektu.

Podporované substituce jsou `{{step.N.response_id}}`, `{{step.N.json}}`, `{{step.N.out_file_path:REL_PATH}}` a `{{step.N.out_file_id:REL_PATH}}`; kroky se číslují od 1. Substituce používá výsledky provedených kroků. Strukturovaný vstup může být objekt nebo seznam objektů. Lokální přílohy se nahrávají s účelem `user_data`.

JSON výstup používá předvolbu `manifest`, `prompts`, vlastní schéma nebo automaticky připravené schéma. Program převádí konkrétní objekty do strict formátu; nepřítomná volitelná pole přenáší jako nullable hodnoty a obnovuje je před kontrolou původního kontraktu. U neurčitého schématu provede samostatnou funkční přípravu s pevným strict kontraktem a nejvýše dvě opravy návrhu. Tyto požadavky vytvářejí artefakt potřebný pro vlastní práci a nejsou preflightem kompatibility. Schéma ukládá k běhu. Selhání přípravy zastaví pracovní krok bez požadavku na ruční psaní schématu. Původní významové podmínky ověřuje i lokálně. Externí odkazy jsou zakázané. V předvolbě `prompts` se dynamická pole `input_content_json` a `output_schema_custom` přenášejí jako serializovaný JSON text a program je rozbalí. Textové kroky rovněž používají strict schéma; výsledek obsahuje jejich rozbalené odpovědi.

Zápis manifestu do OUT spouští neprázdný `expected_out_files`. Bez něj samotný JSON manifest soubory neukládá. OUT běhu má přednost před `default_out_dir`. Všechny očekávané cesty musí být v manifestu před prvním zápisem. Ukládá se celý validovaný manifest, včetně dalších souborů; očekávané soubory se následně nahrávají do Files API a jejich cesty a ID jsou dostupné dalším krokům. Selhání uploadu nevrací lokální zápis zpět. Každá odpověď kroku se eviduje pod společným ID běhu. `CascadeLogger` používá stejný `RunLogger`/Run Bundle kontrakt jako ostatní běhy, takže KASKÁDA je v Run Exploreru součástí stejné typované evidence.

## IN, přílohy a vzdálené prostředky

Sken IN aplikuje allow/deny seznamy přípon a masek. Vynechává Git, prostředí, runtime adresáře, cache, symlinky, junctions a rozpoznané snapshoty. Výchozí limit souboru je 10 MiB. Prázdné, binární a nečitelné soubory nejsou nahratelné. Citlivé názvy a rozpoznané vzory tajných údajů sken blokuje, pokud uživatel výslovně nepovolí jejich upload; manifest i tehdy zachovává příznak citlivosti. Detekce je heuristická. Tato uploadová heuristika není obsahovou redakcí Run Bundle.

Balíček IN je textový soubor s JSONL položkami `path` a `content`, nikoli ZIP. Načtený obsah musí odpovídat SHA-256 ze skenu a být dekódovatelný jako UTF-8. Celkový limit balíčku je 40 MiB. Podle schopností modelu může aplikace vytvořit i vector store a čekat na indexaci. Ručně vybrané přílohy nepodléhají stejnému skenu. Podporované dokumenty se připojují jako `input_file`, obrázky jako `input_image`. Nepodporovaná příloha nebo neověřitelná metadata běh zastaví. Kontrola velikosti zahrnuje součet příloh; pravidla jsou v [matici požadavků](REQUEST_MATRIX.md).

Běžné běhy mohou vytvářet vzdálené soubory a úložiště pro IN a diagnostiku. Dokončení je automaticky neodstraňuje; spravují se v příslušných panelech. Totéž platí pro přílohy a výstupy kaskád. Validační politika kvůli testu kompatibility nevytváří žádný pomocný soubor, vector store, Response ani Batch. Vlastní pracovní dávkové soubory se po převzetí výsledků odstraňují; neúspěšný úklid je zaznamenaný.

## Komunikace a opakování

Klient používá OpenAI SDK pro vybrané operace a REST pro ostatní nebo při nedostupném SDK. Čtecí operace mohou po selhání SDK použít REST. Chyba mutace SDK se předává jako `OpenAIError` bez druhého provedení přes REST. Seznamy souborů, úložišť a dávek zpracovávají stránkování.

SDK má vypnuté vlastní retry. REST vrstva má nejvýše čtyři pokusy, počáteční prodlevu 0,8 s a strop 8 s; respektuje číselný Retry-After do tohoto stropu. Generující POST `/responses` a vytvoření `/batches` mají jediný pokus. Ostatní operace mohou opakovat timeouty, chyby spojení, HTTP 429 a 5xx. Nad nimi mohou volající používat aplikační retry. Nastavení `response_timeout_s` má výchozí hodnotu 300 s a řídí limit jednotlivého HTTP požadavku stejně pro SDK i REST. Je dostupné v Nastavení. Časový limit nezaručuje dokončení služby a neprokazuje příčinu její prodlevy.

GENERATE/MODIFY používají pro samostatné pracovní Responses `background=true`, `store=true`, bez streamování. Platí to i pro přípravu před Batch; řádky souborové dávky background nesmějí obsahovat. Klient přijme ID před dokončením a dotazuje se na stejnou odpověď každé 2 s. `queued` a `in_progress` nejsou výsledky; strict JSON a doménové kontrakty se ověřují až po `completed`. `failed`, `incomplete` a neznámé vzdálené stavy blokují další zpracování. `cancelled` potvrzuje zrušení. QA, QFILE a kaskády zachovávají synchronní transport.

`response_poll_timeout_s` (výchozí 3600 s) omezuje jedno sledování jedné odpovědi; ReRun zahajuje nový interval. Aktivní HTTP požadavek může doběhnout do svého samostatného limitu. GET a cancel v tomto režimu mají nejvýše čtyři pokusy řízené sledovací smyčkou, bez vnořeného retry SDK/REST; prodlevy jsou 0,8/1,6/3,2 s. Nedostupný prostředek nebo zamítnutý přístup sledování přeruší bez nového generování. Průběh ukazuje vzdálený stav a dobu sledování, nikoli odhad procent z času.

Evidence `response_journal` ukládá před odesláním přesný payload a jeho hash, ihned po přijetí ID pak odpověď a poslední ověřený stav. Dokončený výsledek se ukládá před jeho doménovým zpracováním. Obnova používá původní přílohy, diagnostiku, přípravné podklady a uložené odpovědi, včetně částí souborů a opravných pokusů; znovu generuje pouze dosud neodeslané požadavky. Výsledky přehrává přes stejné validátory a teprve potom posouvá checkpointy či zapisuje OUT. Hashové kontroly dokončených souborů platí i při této obnově. Zámek `execution.lock` brání současnému vykonávání stejného běhu ve více instancích.

Stav `response_pending` znamená přerušené sledování s uloženým ID; obnova pokračuje ve stejném poskytovatelském Response tam, kde to pracovní kontrakt dovoluje. `submission_unknown` znamená nepotvrzený výsledek generujícího POST, včetně pádu mezi odesláním a trvalým uložením ID; automatické opakování je zakázáno. Historický timeout bez ID se také nesmí automaticky zopakovat. Definitivně odmítnutý požadavek vyžaduje opravu nastavení a nový běh. Zastavit ukončuje místní čekání, nikoli vzdálenou generaci; samostatné Zrušit generování volá cancel a výsledek posuzuje podle vráceného vzdáleného stavu.

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

API klíč se před inicializací API panelů načítá primárně z OS credential storage přes `keyring`. Uložený credential má přednost před zděděnou proměnnou procesu. Starý Windows záznam `HKCU\\Environment\\OPENAI_API_KEY` slouží pouze jako migrační zdroj: aplikace jej načte, zapíše do credential storage, provede readback a teprve po úspěšném ověření legacy hodnotu odstraní. Při neúspěšném zápisu nebo readbacku legacy záznam zůstává zachován. Pokud credential storage není dostupné, může aplikace použít legacy záznam nebo explicitní runtime `OPENAI_API_KEY`; tato fallback cesta sama legacy hodnotu nemaže. Explicitní smazání se persistuje jako interní stav prázdného klíče, aby se nemohl znovu aktivovat stale klíč zděděný od parent procesu. Načtená hodnota sjednotí prostředí aktuálního procesu i jednotlivé panely. Uložení vždy provádí write + readback a při selhání se pokusí obnovit předchozí credential. Restart Windows ani rodičovského terminálu není nutný. Klíč se nezapisuje do settings JSON, LOG ani Run Bundle.

SSH pin je Base64 SHA-256 veřejného host key s volitelným prefixem `SHA256:`. Porovnání rozlišuje velikost písmen. Lokální diagnostika spouští distribuovaný PowerShell kolektor s časovým limitem a kontrolou návratového kódu. Spuštění navržené opravy vyžaduje samostatné potvrzení. SMTP používá nastavené SSL nebo STARTTLS; odeslání testovací zprávy je uživatelskou akcí.

SSH OUT spouští schválený obsah `.sh` přes stdin vzdáleného `sh -s` na hostiteli ze snímku konfigurace běhu. Windows OUT používá lokální `.bat`. Potvrzení uvádí cíl a SHA-256 skriptu; výstupy obou variant mají samostatné logy. SSH vyžaduje známý host key a případný odpovídající pin. SMTP ověřuje certifikát i jméno serveru v SSL a STARTTLS; neplatné hlavičky vracejí chybu bez připojení.

Textový kontrakt odmítá nedokončené a chybové odpovědi, odmítnutí modelu a odpověď bez textu. JSON objekt nesmí obsahovat duplicitní klíče ani nestandardní číselné konstanty. `structured_output.validate_output` validuje nové odpovědi nejprve přes `orchestration.contracts.parse_json_strict`: přijímá jediný kořenový objekt bez markdown obalu a okolní prózy, odmítá i nekonečno vzniklé exponentem a neplatné Unicode surrogate. Původní odpověď a strojový kód chyby zůstávají v evidenci. Kanonický otisk je SHA-256 UTF-8 JSON s uspořádanými klíči, bez doplňkových mezer a bez normalizace Unicode či řádků; historický tolerantní reader zůstává oddělený. Kaskáda kontroluje modely, teploty, schémata a odkazy na předchozí kroky před první síťovou operací. Chybějící hodnotu odkazu nenahrazuje prázdným textem.

## Logy, Run Bundle a Historie

Rozpočtový JSON ledger odvozuje zaúčtované tokeny a náklady z trvalých rezervací orchestration SQLite. Před další rezervací i po settlementu obnoví souhrn skutečné spotřeby a dosud aktivních rezervací; původní měření zůstává v evidenci. Doloženě uvolněná rezervace se do souhrnu nezapočítává, neurčitý submit a chybějící či neplatná usage nadále drží nedoloženou část rezervy. Reasoning je součástí `output_tokens`, nikoli další přirážka. Neúplná usage nesmí být naceněna jako nulová spotřeba. Pád mezi SQLite settlementem a zápisem JSON nesmí při dalším načtení obnovit již zaúčtovaný odhad. Limity tokenů, ceny, počtu požadavků a politika neznámých cen zůstávají povinnými branami před novým submitem.

Settlement se páruje primárně podle explicitního ID rezervace v ledgeru, včetně obnovených dávek s odlišným rodičovským runem WorkOrderu. Starší záznam bez ID může použít jen jednoznačnou shodu request hashe v runu loggeru; nedoložená vazba rezervu neuvolní.

Před obnoveným odesláním souborové dávky se ověří celý manifest a uložené otisky WorkOrderů ještě před registrací v ledgeru a uploadem. Chybějící lokální záznam rodičovského běhu se doplní při zachování identity WorkOrderu; změněný WorkOrder je odmítnut. Spojení orchestration SQLite repository se po každé operaci uzavírá i při výjimce; nedokončená transakce se vrací zpět.

Cílený retry souborové dávky s `IMPLEMENTATION_GRAPH_V3` vyžaduje neexpirovanou explicitní autorizaci `within_approval`, která dovoluje opravy a odpovídá běhu, rozsahu, modelům, politice a limitům. Nesoulad nebo vypnuté opravy se odmítnou před uploadem. Nový WorkOrder zachovává logickou identitu úlohy a dostává další číslo pokusu a samostatnou rezervaci. Nejvyšší pokus se zjišťuje z ledgeru i při opakování původní dávky; po třetím pokusu jsou automatické opravy vyčerpány a další postup vyžaduje explicitní rozhodnutí uživatele. Limit se automaticky neobchází novou přípravou či lineage. Rozpracovaný nebo neurčitý předchozí pokus blokuje další odeslání. Primární dávka a přípravný snapshot se nemění; oprava vychází z ověřeného staged obsahu a zachovává očekávaný hash cíle pro bezpečnou publikaci. Legacy dávky zůstávají importovatelné, ale bez kompatibilního implementačního detailu a schválených podkladů se z nich nové odeslání nesestavuje.

Kořenový `execution.lock` je dočasný provozní zámek, nikoli historický artefakt. Nové integritní manifesty jej neobsahují; ověření staršího manifestu ignoruje pouze existenci a obsah tohoto zámku, bez přepisování manifestu či souhrnného hashe. Všechny trvalé podklady a stejně pojmenované soubory v podadresářích nadále podléhají plné kontrole integrity.

`run_state.json` zůstává kompatibilním mutable runtime stavem pracovního workflow, ale není kanonickou historickou pravdou. Každý nový běh má vlastní Run Bundle v adresáři `RUN_*`: `bundle.json`, `run.json`, `steps.jsonl`, `events.jsonl`, `requests/`, `responses/`, `validations/`, `checkpoints/`, `artifacts/`, `manifests/`, `reports/`, `lineage.json` a `checksums.json`. Podrobný kontrakt je v [RUN_BUNDLE_SPEC.md](RUN_BUNDLE_SPEC.md).

Kanonická evidence je typovaná a verzovaná. `RunRecord` popisuje běh a jeho přesný stav; `StepRecord` logické kroky; `EventRecord` append-only události s monotónní sequence; `RequestRecord` přesný odeslaný payload a jeho hash; `ResponseRecord` kompletní response, usage a incomplete/error stav; `ValidationRecord` významnou validaci; `ArtifactRecord` soubor nebo externí zdroj s provenance a SHA-256; `CheckpointRecord` explicitní bezpečný nebo nebezpečný bod pokračování; `LineageRecord` vztah source run → nový target run. Runtime log a `run_state.json` mohou sloužit provoznímu workflow, ale Run Explorer staví nad kanonickou evidencí a rebuildovatelným `HistoryIndex`.

**Kanonická evidence se obsahově nerediguje, nemaskuje, neořezává ani nenahrazuje.** Přesný request, response, event data, uložený artefakt a technický detail musí zachovat skutečný obsah. Prostředí je považováno za důvěrné; ochrana této evidence je vlastností přístupu k pracovnímu prostředí, nikoli obsahové transformace logu. Kompatibilní metoda `_redact` proto u nové evidence obsah nemění. Historické legacy běhy mohou pocházet ze starších verzí, které data redigovaly; chybějící původní obsah se zpětně nedoplňuje odhadem.

Lokální vstupy a výstupy potřebné pro rekonstrukci se kopírují do Run Bundle a dostávají SHA-256. Vzdálený Files API nebo vector-store zdroj, jehož bytes aplikace lokálně nemá, je evidován jako explicitní externí ArtifactRecord s `available_local=false`; aplikace nepředstírá, že takový obsah je self-contained. Zapečetění vytváří integritní manifest a `bundle_hash`; Run Explorer rozlišuje ověřenou integritu, změněnou/neúplnou evidenci a legacy běh bez integritního manifestu.

Historie používá `LOG/history_index.json` jako odvozený read-model. Index lze kdykoli smazat a znovu sestavit z Run Bundle/legacy adresářů; není zdrojem pravdy. UI poskytuje časové, projektové, stavové, modelové a fulltextové filtry, přehled, timeline kroků, lidský i technický pohled na odpovědi, správu artefaktů, event stream, lineage a raw technický detail. Velký artefakt může být v UI zobrazen pouze jako jasně označený náhled, ale kanonický obsah v bundle se tím nesmí zkrátit.

Transportní kopie `background_response`, `provider` a `received` nejsou samostatné pracovní fáze. Nový logger je váže na aktivní explicitní pracovní request; bez takové vazby nevytváří odhadnutý StepRecord. Starší technické StepRecordy zůstávají v důkazním inspektoru. Ve workflow GENERATE/MODIFY/QA/QFILE se nezobrazují jako fáze. Lidské názvy mají přednost před kódem fáze; neuzavřený krok ukončeného běhu ukazuje `Konec fáze nezapsán`. Trvání běhu není součtem překrývajících se kroků. Spotřeba stejné vzdálené odpovědi se v přehledu počítá právě jednou.

Výběr běhu načítá metadata bez plných odpovědí a bez úplného hashování bundle. Obsah detailu se načítá zvlášť v lokálním pracovníku a omezená cache se invaliduje podle změny souborů. Bezpečnostní kontrola bodů obnovy běží odděleně a akce je do jejího dokončení zakázaná. Kontrola integrity před přímým spuštěním se nevynechává. Nezměněný HistoryIndex se nepřepisuje a návaznosti se odvozují z téhož indexu bez druhého skenu adresářů. Větší seznam se filtruje mimo GUI thread.

Všechny kontextové akce, včetně otevření komiksu, dávky a zdrojového běhu, řídí jednotná politika. Nesouvisející doménové akce se nezobrazují. Technická evidence a export jsou v sekundární nabídce. Detail má vlastní skutečné akce spuštění, klonu a převzetí dávky; nevyžaduje návrat do formuláře Zadání.

Nový vstupní checkpoint obsahuje `input_archive` s verzí, příznakem úplnosti a ID archivovaných souborů. Chyba archivace blokuje bezpečné pokračování. Přímá větev rekonstruuje IN z ověřených archivovaných souborů v izolovaném adresáři. Nespoléhá na aktuální obsah původní pracovní složky. Opravný pokyn souborové dávky je explicitní pole nového manifestu a kontextu nových souborových requestů; nemění uložený přípravný snapshot, výstupní kontrakt ani limity.

Při sledování vzdálené odpovědi opakovaný identický výsledek GET nevytváří nové kopie deníku a stavu. Změna stavu nebo obsahu se nadále zapisuje. Rozdílné transportní GET request ID není novou pracovní odpovědí; průběh sledování dál aktualizuje Operations.

Zdrojový běh je v Historii neměnný. `Pokračovat`, `ReRun`, `Opravit`, `Klonovat` a `Použít v novém běhu` nikdy nepřepisují starý Run Bundle. Pokračování/repair/rerun používá pouze explicitní validní safe checkpoint a vytváří nový RUN s LineageRecord; klon přebírá pouze přesně uložené zadání a nastavení a automaticky nezdědí starou response historii; znovupoužití souborů vyžaduje stabilní reusable ArtifactRecord a ověření SHA-256. Odeslaný Batch se neduplikuje novým submittem, ale dokončuje se v původním běhu. Legacy adapter je read-only, nevytváří domýšlené checkpointy, kroky ani provenance a chybějící údaj zobrazuje jako neznámý.

Vzdálený Batch `completed` znamená pouze dokončení dávky poskytovatelem. Projekt není `completed`, dokud neproběhne požadovaný import a validace lokálního výsledku. Stejně tak Response se stavem `incomplete`, error nebo odmítnutím není úspěšná odpověď a nesmí být v Historii zobrazena jako `completed`.

KájovoNG neimplementuje cenový engine ani runtime finanční budget. Hospodárnost je řešena návrhem workflow a provozním rozhodnutím uživatele. Runtime validuje pouze technické a kontraktní limity API. Před skutečným provider submitem zmrazí `WORK_ORDER_V2` s explicitním `attempt_id` a zapíše nefinanční provider-operation evidenci pro idempotenci, `submission_unknown` a recovery. Raw provider `usage` se archivuje jako neměnná telemetrie bez převodu na peněžní hodnotu. Historická uživatelská data s dřívějšími ekonomickými poli se nemažou; orchestration SQLite se deterministicky migruje tak, aby nový schema zachoval jen nefinanční identitu, stav a provider evidence. Automatická validace nesmí přidávat samostatné generativní požadavky nebo dávky, jejichž jediným účelem je ověřit budoucí pracovní požadavek.

## Dokončení uložených dávek a výchozí model

Souborové Batch waves používají stabilní globální identitu úlohy podle seřazené produkční množiny zmrazeného implementačního grafu; číslo úlohy se při změně výběru ani přechodu do další wave nezačíná počítat znovu. Hashovaný přípravný snapshot obsahuje původní cílové hashe všech produkčních souborů včetně odložených cílů; `null` znamená původně neexistující soubor. Followup i retry zachovávají celý snapshot a neodvozují oprávnění k přepsání z aktuálního OUT. Starý snapshot bez této mapy zůstává čitelný pro import, ale nové odeslání navazující wave nebo opravy se odmítne bez odhadování chybějícího základu.

Navazující dávka eviduje `source_manifest_hash` původní wave. Opakovaný import již převzaté primární dávky podle této vazby nevytváří další submit. Dokud navazující dávka čeká na převzetí, souhrnný stav zůstává `batch_pending`; úplně převzaté soubory bez funkčního ověření mají stav `files_complete_unverified`. Neurčitý submit uchovává manifest, Files ID a hash JSONL a blokuje nové odeslání do dohledání původní operace.

Po úspěšném odeslání pracovní dávky uživatel pokračuje akcí **Dokončit** v Historii nebo Dávkách. Obě místa používají stejnou operaci na pracovním vlákně. Probíhající vzdálená dávka vrátí informaci o čekání. U implementačního grafu V3 se ukončené položky ověří a převezmou do stagingu bez opakování A1/A2; připravená další dependency wave se odešle jako samostatně evidovaná pracovní dávka. Již odeslaná wave se opakovaným importem nevytváří znovu. Publikace do OUT je oddělená hranice chráněná původními cílovými hashi. Historické čtecí adaptéry zachovávají kontrakt uložených legacy dávek. Žádný mezikrok zkušební dávky v aktuálním workflow neexistuje.

`run_state.json` zachovává `batch_records` podle ID pracovní dávky včetně dostupného serverového `created_at`. Přehled páruje `batch_id` a `generate_batches` s místními běhy a jejich projektem; serverový čas zobrazuje v místním pásmu jako `DD.MM.YYYY HH:mm:ss`. Chybějící čas není odvozován z času vzniku běhu. Cizí dávka bez místních podkladů nemá akci dokončení běhu. Starší stav bez `batch_records` zůstává čitelný.

Serverový stav a uložení do OUT jsou oddělené. `batch_imports[batch_id].import_status` eviduje výsledek konkrétního importu, zatímco `status` běhu zohledňuje úplnost výstupu i dosud nepřevzaté opravné dávky. `files_complete_unverified` označuje úplné uložení souborů, nikoli ověření funkčnosti. Částečný import zůstává opakovatelný. GENERATE i MODIFY ověřují jednotlivé souborové výsledky podle uloženého manifestu a chrání zápisy hashi. Jen historické souhrnné dávky MODIFY používají jediné ID C1 a kontrakt C_FILES_ALL. Již změněné nebo cizí soubory se nepřepisují. Dokončení nesmí běžet současně s další operací stejného běhu nebo s aktivním během používajícím překrývající se OUT. Výsledek obnoví Historii i Dávky a Run Bundle eviduje BATCH import i jeho artefakty.

Zrušení zpracování je dostupné pro `validating`, `in_progress` a `finalizing` a vyžaduje potvrzení. Aplikace nemaže serverové soubory ani nenabízí smazání záznamu dávky, které Batch API nepodporuje.

`default_model` je jediná trvalá předvolba modelu. Výběr v Nastavení a akce **Nastavit jako výchozí** u hlavního modelu i v Modely používají stejné ukládání. Změna se projeví v paměti a ovladačích až po úspěšném zápisu. Předvolba platí pro čisté zadání při spuštění aplikace a akci Nové; rozpracované a načtené zadání, explicitní modely A1/A2/A3, ReRun i odeslaná dávka zachovávají vlastní model. Nová volba vyžaduje podporovaný model z katalogu účtu. Uložená nedostupná předvolba zůstane viditelná bez tichého nahrazení.

## Převodník UTF-8

`python -m utf8nobom.py` spouští samostatné Qt rozhraní. Převodník vytváří kopie a ZIP zálohy mimo vstupní adresáře. Překrývající se vstupy deduplikuje, stejně pojmenované adresáře rozlišuje v názvech záloh a existující zálohu nepřepisuje. Git metadata a odkazy nekonvertuje. ZIP s traversal položkou odmítne beze změny; při přepisu zachovává komentář archivu a metadata položek. Oprava kódování je heuristická a výsledek je třeba posoudit podle konkrétních dat; originál zůstává v záloze.

## Distribuce a ověření

Editor kaskády ověřuje model, teplotu, strukturovaný vstup a dostupnost vlastního schématu před změnou uloženého kroku. Externí návaznost je dostupná i v prvním kroku. GENERATE i MODIFY mají před souborovými dávkami živou přípravu. Minimální explicitní `max_output_tokens` je 16. Kombinace režimů a příznaků vymezuje `docs/REQUEST_MATRIX.md` a reprodukovatelný export `docs/REQUEST_COMBINATIONS.csv`.

Wheel obsahuje balíčky `kajovo`, `kajovong`, `utf8nobom`, diagnostický skript, logo a oba fonty. Testy nejsou distribuovanými balíčky. Fonty ve zdrojovém stromu používají Git LFS. Windows sestavení vytváří `dist/Kajovo/Kajovo.exe`, macOS sestavení `dist/Kajovo.app`; název lze předat sestavovacímu skriptu. Sestavení provádí instalaci závislostí, generování ikon a PyInstaller. Generované adresáře a binární distribuce nejsou zdrojovým kódem.

Repozitář neobsahuje placené `verify_*_live.py` nástroje, které by kvůli samotnému ověření automaticky odesílaly generativní Responses nebo zkušební dávky. CI a standardní validační postupy jsou offline vůči placeným generativním endpointům: používají mocky, lokální validátory a statické kontroly. Ruční spuštění skutečné pracovní funkce aplikace samozřejmě může vytvořit placený pracovní požadavek; ten není validačním preflightem.

| Testy | Ověřované chování |
| --- | --- |
| `test_run_bundle.py` | Run Bundle, append-only eventy, exact evidence bez redakce, integrita, checkpointy, lineage, legacy adaptér a HistoryIndex |
| `test_batch_completion.py`, `test_batch_completion_ui.py` | Převzetí pracovních dávek, historie, ochrana souborů, souběh a výchozí model |
| `test_workflows.py` | Offline režimy, batch, snímky konfigurace a validace výstupu |
| `test_delivery_pipeline.py`, `test_requirements.py`, `test_modify_batch.py` | Osm variant GENERATE/MODIFY, kanonický quality gate, traceability, reasoning, dlouhé vstupy/výstupy a souborové dávky |
| `test_preparation_snapshot.py`, `test_desktop_preparation_recovery.py` | Částečné checkpointy, ReRun, integrita podkladů a ochrana uživatelských úprav |
| `test_no_paid_preflight.py`, `test_response_policy.py` | Zákaz placených validačních Responses/BATCH a neplacená validační hranice |
| `test_contracts.py`, `test_cascade.py` | JSON, cesty, souborové výstupy, schémata a kaskády |
| `test_filesystem_boundaries.py`, `test_security_regressions.py` | Souborové hranice, citlivé vstupy, hashe a bezpečnost zápisu |
| `test_response_journal.py` | Dlouhé odpovědi, výpadky, obnova, zrušení a ochrana před opakovaným generováním |
| `test_api_client.py`, `test_retry.py`, `test_text_chunks.py` | HTTP, SDK, stránkování, opakování a dělení textu |
| `test_config.py`, `test_diagnostics.py` | Nastavení, hesla a SSH pin |
| `test_desktop.py`, `test_repository_contract.py` | Importy, Qt, ovládání, prostředky a kódování |
| `test_utf8nobom.py` | Zálohy, deduplikace vstupů a převod ZIP |

Povinné kontroly jsou `python -m pytest -q`, `python -m ruff check --select F,B,E9 kajovo kajovong utf8nobom tests Build` a `python -m pip check`, spuštěné v projektovém prostředí. CI používá Windows a Python 3.13. Automatické testy nahrazují HTTP a nevyžadují produkční API klíč.

Úspěch kontrol platí pro jejich rozsah. Skutečné pracovní OpenAI požadavky, SMTP, vzdálené SSH, oprávnění účtu, interaktivní provoz a platformní distribuce vyžadují ověření v odpovídajícím prostředí. Automatické testy nejsou zárukou nepřítomnosti všech chyb.

## Kontext a náklady souborového dodání

Nové LIVE A3/B3 používá FileContext stejně jako Batch v3. První chunk nemá historii přípravy ani přílohy celého IN; pokračování navazuje jen uvnitř souboru a používá hash kontextu/prefixu. Globální zdroje zůstávají v přesných obnovitelných artefaktech a kanonickém bezeztrátovém Run Bundle; evidence se obsahově nerediguje. Requesty se měří lokálně, pokračování vyžaduje ne-generativní input token count se shodným hashem požadavku. Generativní preflight není povolen. Runtime uchovává technická měření a raw provider usage, nikoli cenové odhady. Podrobný kontrakt, omezení integrační validace a scheduleru jsou v [CONTEXT_COMPILER.md](CONTEXT_COMPILER.md).


### Parametry obrazové editace

Společná validace `photo_batch.validate_image_edit_parameters` probíhá před vytvořením úlohy i před prvním uploadem. Model musí podporovat editaci v pracovní dávce podle pevné matice. GPT Image 2 dovoluje vlastní rozměry dělitelné 16, nejvýše 3840 bodů na hranu, poměr stran nejvýše 3 : 1 a plochu 655360 až 8294400 bodů. Starší modely používají automatickou velikost, 1024 × 1024, 1536 × 1024 nebo 1024 × 1536. Formáty jsou png, jpeg a webp. Hodnota uvedená poskytovatelem sama neobchází lokální zákaz dávkového modelu. Zdroje a validační důkazy jsou v `docs/ui/validation-plan.csv`.

PHOTO a COMIC sdílejí technickou kontrolu přes `image_runtime.inspect_image`. Aplikační maxima jsou 50 000 000 bajtů a 64 000 000 dekódovaných pixelů, vždy dále omezená capability vybraného modelu. Distribuovaná `orchestration/policies/images.json` je neměnná kopie obrazové politiky; samotná přítomnost jejích dalších pravidel nedokládá implementaci nového schvalování nebo repair workflow. PHOTO importer přijme právě jeden obraz, ověří base64, úplné dekódování a skutečný formát před zápisem; neplatný nebo víceznačný výsledek nevybere heuristicky a nezapíše do OUT. Technické ověření není obsahové schválení.

## Komiks

Desktopové studio obsahuje samostatnou sekci Komiks. Projekt má uložené nastavení stylu, explicitní typografická pravidla, bubliny, SFX, paletu, obrazové podklady, verzovanou strict bibli, knihovnu postav a prostředí, nezávislé panely a historii. Technický transport zůstává v existujícím `kajovo.core.openai_client`; nevzniká paralelní poskytovatel ani webové IPC. Doménové příkazy `ComicService` používají existující Operations, ProgressEvent, ResponseJournal, RunLogger a Run Bundle. Úplný kontrakt a konkrétní vazby na testy jsou v [COMICS.md](COMICS.md).

`ComicStore` ukládá metadata do vlastní knihovny SQLite a neměnné obrázky do souborů. Migrace 1 je atomická, zapíná FK; revize chrání souběžné změny a OS zámek vlastnictví operace. Historické podklady se nepřepisují. Projekt má vratný koš. Prompt je verzované AST s textem a atomickými odkazy na entity; backend kontroluje jejich typ, příslušnost a stav. Žádná entita není povinná.

Bible a canonical descriptory používají skutečné Responses se strict schématem. Explicitní volby mají přednost před odvozenými pravidly. Reference postav/prostředí vznikají živou editací. Hotové panely i následná editace používají skutečné Image Batch API. Modely, limity a syntaxe vycházejí z pevné capability matice. Výchozí obrazový model je `gpt-image-2.5-sunburst-2026-09-08`, kvalita `max`; textový `gpt-6-astra`. Dokumentovaná dostupnost se nezaměňuje s oprávněním konkrétního účtu.

Každý panel má vlastní cílové rozměry a nezávislý custom_id. Aplikační operace se dělí pouze podle endpointu a limitů API. Snapshoty a stav přežijí restart. Neurčitý submit se nesmí slepě opakovat. Stažené výsledky se archivují před parsováním a mapují podle custom_id; úspěšné panely přežijí částečné selhání. Opakování chyb neposílá úspěšné položky. Progress uvádí skutečnou etapu a známé počty, nikoli odhadovaná procenta.

Nativní generovací rozměr a finální raster jsou odlišné pojmy. Finální canvas vzniká deterministickým crop/pad/resize bez protažení, papírové formáty mají výchozích 300 DPI. Přesné texty jsou samostatné editovatelné Qt vrstvy; stejný renderer vytváří náhled i export. Přetečený text blokuje export. Obrazová editace vytváří kandidátní verzi, schválení a obnova jsou explicitní. Textová i canvas editace vytvářejí verze bez API volání. Vizuální identita je podporována referencemi a bibli, nikoli zaručena determinismem modelu.

Upload ověřuje obsah a limity, originál uchovává v místní knihovně, pracovní kopie normalizuje orientaci a metadata. Normalizace aplikuje EXIF orientaci jednou, zachová průhlednost včetně paletových PNG a vytváří nové RGB/RGBA PNG bez EXIF, ICC a textových metadat. Původní bajty se nepřepisují. Kompilátor panelu ověří otisky, odvozenou pracovní kopii verzovaně uloží a znovu použije. Sloty vytváří až po deduplikaci dvojice SHA-256 normalizovaného obrazu a transformační politiky: základ editace, identity podle ID entity, styl podle ID assetu. Všechny logické role zůstávají ve snapshotu i promptu svázané s konečným `image_index` a `slot_id`; shodný fyzický obraz se posílá pouze jednou. Limit se kontroluje po deduplikaci a je menší z limitu modelu a 16 fyzických obrazů. Žádné veřejné anonymní URL ani klientské klíče nevznikají. Base64 obrázky nepatří do textových logů; přesná provider evidence může být bezeztrátový binární archiv s hashovaným odkazem. Usage a cena se evidují jen v rozsahu doložených dat. Neznámá cena není nula.

Placená ruční akceptace Komiksu je oddělený výslovně spuštěný pracovní scénář, nikoli preflight ani součást běžných testů. Testovací PASS nelze použít místo dokladu dokončené živé operace nebo vizuální kontroly. Produkční distribuce zůstává desktopové sestavení podle `Build/README.md`; nevzniká ad-hoc serverové nasazení.

## Živé průběhy operací

Všechna popup okna pro běžící práci používají společný význam průběhu. Zobrazují doložené dokončené fáze, aktuální fázi, další známý krok, dílčí počty, čas a stáří poslední skutečné události. Textový audit zůstává dostupný jako technický detail, ale není jediným zdrojem informace o stavu.

Událost průběhu může určit zdroj práce (`local`, `api`, `files_api`, `batch_api`, `upload`, `download`, `disk`, `validation`) a další krok. `api` znamená konkrétní OpenAI Responses API; Files API a Batch API se zobrazují samostatně. Neznámý postup se nezobrazuje jako falešné procento a ETA se uvádí pouze při doložitelném měření. Terminální stav není odvozen pouze z hodnoty progress baru.

Volba maximální propracovanosti je v živém plánu zobrazena jako skutečná etapa `A2Q` nebo `B2Q`. ReRun zobrazuje převzaté, právě prováděné a zbývající etapy odděleně; přeskočená etapa neznamená nové placené volání.
