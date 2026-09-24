# Historie — Run Studio

Sekce **Historie** je produkční Run Studio nad `HistoryIndex`, `RunBundle` a `LegacyRunAdapter`. Není novou databází. Index i prezentační model lze kdykoli znovu sestavit z kanonické evidence a pouhé procházení neposílá žádný OpenAI požadavek.

## Hlavní plocha

Jeden běh je jedna horizontální časová stopa. Virtuální `RunTableModel` a malovaný `TrackDelegate` nevytvářejí QWidget pro každý segment. Stopu tvoří skutečné pracovní `StepRecord`; transportní kopie background/provider/received zůstávají pouze v technické evidenci standardního workflow. Legacy záznam bez kroků ukazuje, že podrobný průběh nebyl uložen.

Metadata řádku zahrnují mode, projekt, Run ID, čas, doložené trvání, stav, LIVE/BATCH, modely a počty vstupů, výstupů a chyb. Segment má lidský název, stage, text/ikonu/barvu stavu, trvání a počty response, artefaktů a chyb. Chybějící timestamp neprodukuje odhad trvání.

Filtry:

- fulltext nad rebuildovatelným `HistoryIndex`, včetně Run ID, Response ID a názvů artefaktů;
- datum od/do, projekt, mode, stav, LIVE/BATCH a model;
- jen chybové a pokročilé příznaky BATCH/checkpoint/výstup/lineage.

Časové měřítko a mezery vycházejí z timestampů. Zoom zvětšuje pracovní plochu, Přizpůsobit vychází ze skutečné šířky okna. Chybějící konec znamená neurčené trvání; součet překrývajících se fází nenahrazuje celkový čas. Tooltip zpřístupňuje celý název krátkého segmentu a klávesové šipky vybírají fáze. Vybraná fáze určuje kontext a dostupné akce. Spojnice větví se kreslí jen pro vybraný běh a přímé návaznosti s doloženým bodem větvení; chybějící checkpoint se nenahrazuje bodem na konci řádku.

### Matice obrazovek a vazeb

| Předloha | Produkční pohled | Skutečná data a obsluha |
|---|---|---|
| Hlavní Historie | `HistoryPage`, `RunTableModel`, `RunTrackView` | index, lokální filtry, virtuální stopy, kontext fáze, centrální politika, detail a sekundární nabídka |
| GENERATE | `RunDetailView`, `PhaseInspector`, `TextCard` | uložené zadání, výstupy vybrané fáze, unikátní odpovědi, oddělený vzdálený/místní BATCH stav, skutečné převzetí |
| MODIFY | `ModifyMap` | manifest, uložené vstupy a výstupy; asynchronní dvě verze se zvýrazněnými změnami; neexistující výsledek není automaticky chyba |
| QA | `TextCard`, metadata odpovědi | lidský text, kopírování/TXT skutečného obsahu, přílohy, přímé opakování a upravená větev od vstupu |
| QFILE | `ArtifactBrowser` | ověřený lokální text/obrázek/PDF, stránkování a zoom, export, oddělené souborové a obsahové ověření |
| KASKADA | `CascadeTimeline`, `PhaseInspector` | skutečné časové intervaly, vybraný krok a jeho závislosti, návazné běhy a přímá oprava |
| Kompozér opravy | `BranchComposer` | zdrojová chyba, technický detail, bezpečný bod, zachované soubory, nový pokyn, lokální první placená operace a potvrzení |

Technická evidence obsahuje původní kroky, požadavky, odpovědi, validace, události, návaznosti, checkpointy a stav. Nemění kanonické záznamy. Detaily i hlavní plocha mají skutečné akce; doménové možnosti se nezobrazují mimo svůj kontext. Klon a převzetí dávky vždy používají přesný zdroj otevřeného detailu, nikoli náhodně vybraný řádek v pozadí.

### Výkon a validace

Výběr řádku nečte plné request/response a nehashuje celý bundle. Obsah detailu se načítá lokálním pracovníkem; cache je omezená počtem položek i paměťovým rozpočtem a invalidovaná změnami souborů. Kontrola checkpointů běží zvlášť, sdílí načtené response a hashe v rámci jedné kontroly a teprve po úspěchu povolí spuštění. Kompletní integrita zůstává samostatnou skutečnou akcí a součástí přímého spuštění. Vyhledávání od tisíce řádků běží mimo GUI thread; starší výsledek nepřepisuje novější filtr.

Samotné otevření `RunBundle` nečte deník událostí a nevytváří chybějící adresáře. `Operations.start_read` vlastní lokální pracovní vlákno bez vytváření skrytého progresového dialogu; po dokončení neuchovává výsledek ani operaci. Nezměněná odpověď při pollingu nevyvolává opakovaný zápis celého response journalu. Toto nemění intervaly, limity, obsah modelových požadavků ani síťovou obnovu.

Po potvrzení větve probíhá kontrola integrity, checkpointu a rekonstrukce IN mimo GUI v operaci „Příprava nové větve“. Ta rezervuje výstupní adresář; po jejím dokončení přebírá stejný zámek skutečný doménový worker přes `Operations.adopt`. Stejné potvrzení nelze použít dvakrát. ZIP export ověřuje místní artefakty a zveřejní archiv atomicky, až když je celý dopsán.

Na hlavní ploše zůstává hledání, projekt, druh a stav. Datum, model, transport a pokročilé příznaky se nastavují v „Další filtry“. Technická evidence uchovává i chybějící metadata; hlavní přehled nevyplňuje prázdné hodnoty opakovanými stavovými větami.

`tests/test_history_forensic.py` používá skutečný logger a ResponseJournal pro kontrolu vazeb transportu, bezsíťového načítání, invalidace cache, dostupnosti doménových akcí, asynchronního výběru, klávesnice, integrity a obnoveného IN. `scripts/render_studio.py` vytváří všech sedm stavů z dočasných kanonických záznamů včetně opravné větve a normalizuje časy fixture. Produkční UI neobsahuje jejich data.

## Typové detaily

- **GENERATE:** A0R → A1 → A2 → volitelně A2Q → A3, původní zadání, LIVE/BATCH hranice, request/response, tokeny, retry, artefakty a validace. Peněžní odhady ani ceny se nepočítají a nezobrazují.
- **MODIFY:** B0R → B1 → B2 → volitelně B2Q → B3 a deterministická mapa změněné/nové/zachované/odstraněné/chybové/přeskočené. Textový diff používá archivovaný originál a výsledek; velké nebo binární soubory ukazují metadata a hashe. Dry-run je návrh a ne hotový zápis do OUT.
- **QA:** zadání, přílohy, lidský text odpovědi, response/request ID, model, tokeny, incomplete reason a error.
- **QFILE:** zadání, výsledný ArtifactRecord, MIME preview a samostatné stavy `souborový kontrakt platný` a `obsah ověřen`. Text, obrázek a PDF mají read-only náhled; ostatní formáty metadata a externí otevření.
- **KASKADA:** skutečné chronologické StepRecordy, model, čas, vstupy/výstupy, request/response, validace, artefakty, retry a chyby. Pravý inspektor zvýrazní jen skutečné dependencies vybraného kroku.
- **COMIC:** generický důkazní detail a návrat do doménové sekce Komiks.
- Neznámý budoucí kind dostane generický detail nad Run/Step/Request/Response/Artifact/Validation/Event/Checkpoint/Lineage záznamy.

## Forenzní inventura producentů

| Druh | Zdroj a fáze | Evidence / transport | Výstupy a partial stavy | Bezpečné navázání |
|---|---|---|---|---|
| GENERATE | `core/pipeline.py`; A0R, A1, A2, volitelně A2Q, A3 | Step/Request/Response; LIVE nebo A3 BATCH | manifest, generated ArtifactRecordy, missing deliverables; completed/partial/failed/dry-run/BATCH stavy | `input_ready` a kanonické preparation snapshoty; hashově ověřené hotové cesty lze přeskočit |
| MODIFY | `core/pipeline.py`; B0R, B1, B2, volitelně B2Q, B3 | Step/Request/Response; LIVE nebo B3 BATCH | modified/new/preserved manifest, write evidence, partial B3, dry-run | `input_ready`, preparation snapshot, původní IN artefakty a completed hashes |
| QA | `core/pipeline.py`; QA | jeden LIVE Request/Response, textové a souborové přílohy | lidský text, incomplete/error metadata | nové běhy mají `input_ready`; staré bez checkpointu jsou read-only |
| QFILE | `core/pipeline.py`; QFILE | jeden LIVE Request/Response, A3_FILE validation | jeden ArtifactRecord, file-contract validation; obsahové ověření zvlášť | nové běhy mají `input_ready`; bez něj žádný direct rerun |
| KASKADA | `core/cascade_pipeline.py`; skutečné pořadí definovaných kroků | StepRecord na krok, LIVE request/response, dependencies a runtime values | text/JSON/soubory, retry, failed current step; pozdější stavy pouze podle evidence | `cascade_input_ready` a `cascade_step_completed` s definicí, signatures, cache, archivovanými lokálními vstupy a required evidence |
| COMIC | `core/comic_service.py`; komiksová operace/panely | Run Bundle + doménový Comic store, Image BATCH | obrazové ArtifactRecordy a verze panelů | Run Studio je read/generic; pracovní retry a obnova zůstává v Komiksu |
| Photo Studio / Image Batch | `core/photo_batch.py`, `studio/photos.py` | vlastní job store, nikoli Run Bundle/HistoryIndex | výsledky fotografií a batch joby | obnova pouze v Photo Studio; Run Studio nevymýšlí RunRecord |
| Legacy/unknown | `LegacyRunAdapter` | pouze skutečně nalezené logy; žádné domyšlené StepRecordy | chybějící fakta = `Není evidováno` | read-only bez explicitního kanonického checkpointu |

Terminal/nonterminal a remote status se normalizují pouze v prezentační vrstvě. Kanonický `status`, StepRecord, event, validation, checkpoint, lineage a batch state se nepřepisují odhadem.

## Přímé historické větve

`Pokračovat`, `Znovu spustit` a `Opravit` už nikdy nenaplňují Workbench a nepřepínají do Zadání. `HistoryBranchLauncher`:

1. načte source Run Bundle a lokálně ověří integritu;
2. validuje explicitní `CheckpointRecord`, jeho state hash, povinné artefakty a response;
3. u GENERATE/MODIFY ověří preparation snapshot a hotové hashe;
4. ukáže čistě lokální confirmation/repair composer včetně první nové placené operace;
5. po potvrzení vytvoří nové Run ID a nový `RunLogger`;
6. zapíše target-only LineageRecord `continue`, `rerun` nebo `repair`;
7. spustí existující `RunWorker` nebo `CascadeRunWorker` přes standardní `Operations` dialog a output lock;
8. zablokuje druhé potvrzení a po změně obnoví Run Studio.

Source bundle je immutable. Confirmation, výběr checkpointu, výpočet první placené operace a filtry jsou lokální a negenerativní.

Opravný pokyn je pole nového worker configu a LineageRecordu. Přidává se pouze do nově prováděných requestů za checkpointem; nemění původní prompt hash ani podmínky zděděných artefaktů.

QA navíc nabízí `Upravit QA a spustit novou větev`. Je dostupné jen pro explicitní `input_ready` před prvním QA requestem; uživatelův doplňující pokyn se eviduje v novém běhu a vstoupí do jeho skutečného requestu, zdroj se nemění.

## Clone a reuse

**Klonovat jako nové zadání** je jediná rodina akcí, která otevře Workbench. Načte přesný uložený `ui_state`, odstraní `response_id`, resume/preparation metadata a recovery instruction. Lineage `clone` se zapíše až při následném skutečném startu nového běhu. Varianta klonu s reusable artefaktem nejprve ověří jeho SHA-256 a zkopíruje pouze zvolený soubor do izolovaného vstupu.

## BATCH

Sekce Dávky zůstává samostatná. Run Studio pouze zobrazuje vazbu a volá stejný `complete_saved_batch`. Poslední doložený remote status je uložen odděleně v `batch_records`; místní výsledek je v `batch_imports`. Remote `completed` s chybějícím importem znamená **K převzetí**, nikoli dokončený projekt. `files_complete_unverified` znamená souborově úplné, ale funkčně neověřené.

Dokud odeslaný Batch čeká na vzdálené dokončení nebo místní import, přímý continue/rerun/repair je zablokován, aby nevznikl druhý submit. `Převzít soubory` pracuje s původním Batch ID a je dostupné jen při doloženém remote `completed` a chybějícím místním importu. Částečný import lze bezpečně opakovat; teprve po dokončeném importu může explicitní rerun vytvořit nový samostatný běh.

## Checkpointy a legacy

Nové GENERATE, MODIFY, QA a QFILE běhy ukládají před prvním síťovým požadavkem `input_ready` s přesným `ui_state`. GENERATE/MODIFY dále ukládají kanonické preparation checkpointy. Nové KASKADA běhy ukládají `cascade_input_ready` a po každém dokončeném kroku `cascade_step_completed` s definicí, signaturami, runtime hodnotami, required response a výstupními ArtifactRecordy.

Staré záznamy checkpoint nedostávají heuristicky. Legacy adapter je read-only; chybějící StepRecord, checkpoint, čas nebo validace se zobrazí jako `Není evidováno`/`Neověřeno`. Bez explicitního validního safe boundary není přímá akce dostupná.

## Artefakty a bezpečnost

Otevření, náhled, export, uložení a porovnání vyžadují místní `path_in_bundle`, existující soubor a platný SHA-256. Cesta musí zůstat pod Run Bundle; export nesmí přepsat source bundle. Remote-only artifact nelze otevřít. Textový náhled je omezen na 1 MiB a canonical soubor se netruncuje. Diff nad 5 MiB se synchronně nevytváří.

## Prezentační stavy

Centrální mapping pokrývá created, preparing, running, response_pending, batch_prepared, batch_pending, importing, completed, partial, failed, cancelled, stopped, dry_run, submission_unknown, files_complete_unverified, closed, corrupt_state, unknown a remote Batch stavy. Barva je vždy doplněna ikonou a textem; 100% progress sám nikdy neznamená completed.
