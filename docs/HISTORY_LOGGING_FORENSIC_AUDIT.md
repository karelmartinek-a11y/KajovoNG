# Forenzní audit historie, logování a obnovitelnosti

## Rozsah a závěr

Audit byl proveden před přestavbou uživatelského rozhraní Historie. Vychází z aktuálního `main` a kontroluje `runlog.py`, `response_journal.py`, `recoverable_artifacts.py`, `pipeline.py`, `delivery_preparation.py`, `batch_completion.py`, `cascade_log.py`, `desktop/recovery.py`, `desktop/history.py` a vazby v `desktop/application.py`.

Současná implementace obsahuje řadu kvalitních dílčích bezpečnostních a recovery mechanismů, ale **nemá jednotný forenzní datový model běhu**. Kanonická pravda je rozptýlena mezi `run_state.json`, `events.jsonl`, pojmenované JSON soubory v `requests/`, `responses/`, `manifests/`, obsahově adresované JSON artefakty a několik workflow-specifických evidencí. Historie proto neumí spolehlivě vytvořit úplnou časovou osu ani obecně určit provenance artefaktu, bezpečný checkpoint nebo lineage mezi běhy.

Nejzávažnější odchylky od cílového kontraktu:

1. `RunLogger` a `CascadeLogger` obsahově redigují evidence; cílový kontrakt naopak vyžaduje bezeztrátovou evidenci bez obsahové redakce.
2. `events.jsonl` nemá `event_id`, monotónní `sequence`, `step_id`, explicitní vazby na request/response/artefakty ani jednotnou severity.
3. `run_state.json` je mutovaný stav, nikoli append-only důkaz změn; některé workflow jej navíc zapisují přímo mimo `RunLogger`.
4. Neexistují explicitní `RunRecord`, `StepRecord`, `RequestRecord`, `ResponseRecord`, `ValidationRecord`, `ArtifactRecord`, `CheckpointRecord` a `LineageRecord`.
5. Neexistuje self-contained Run Bundle s integritním manifestem a binárními artefakty.
6. Recovery používá vedle přesných stavů také heuristiky podle názvů souborů, mtime a shody OUT adresáře.
7. Historie při filtrování opakovaně čte obsah request/response souborů z disku a není postavena nad lokálním read-modelem/indexem.
8. Stávající UI je plochý seznam běhů + plochý seznam request/response souborů; chybí timeline, kroky, artefakty, validace, checkpointy, lineage a integrita.

## Matice současné evidence

| Proces / evidence | Kde vzniká | Kde se ukládá | Přesný request | Přesná response | Vazba na soubory | Rekonstrukce | Checkpoint | Hlavní nedostatek |
|---|---|---|---|---|---|---|---|---|
| Vznik běhu | `RunLogger.__init__` | `run_state.json`, `events.jsonl` | — | — | ne | částečně | ne | mutable state, event bez sequence/id |
| GENERATE/MODIFY příprava | `delivery_preparation.py` | `requests/`, `responses/`, state snapshot | ano | ano | nepřímo | dobrá pro konkrétní workflow | implicitní | checkpoint není samostatný objekt |
| LIVE Responses | `pipeline.py`, `response_journal.py` | journal, responses, events | ano v journalu / request souboru | ano | omezeně | dobrá pro background response | implicitní | chybí normalizovaný request/response record |
| Response polling | `response_journal.py` | journal + events | GET není samostatný RequestRecord | stav response | ne | částečně | ne | polling je provozní evidence bez jednotného recordu |
| BATCH příprava/submit | generate/batch moduly | state, manifesty, JSONL | JSONL existuje | batch record podle workflow | částečně | dobrá pouze s workflow znalostí | implicitní | endpointové a importní stavy nejsou sjednoceny |
| BATCH import | `batch_completion.py` | state, raw JSONL, OUT | — | raw output/error JSONL | ano, ale ne přes ArtifactRecord | částečně | ne | přímý přepis state mimo logger, chybí provenance objekt |
| Souborový zápis OUT | pipeline/import | OUT + saved map/journal | — | — | hash evidence existuje | relativně dobrá | ne | artefakt není archivován v bundle, může zmizet z OUT |
| Recovery | `desktop/recovery.py` | čte rozptýlené zdroje | — | — | heuristicky i přesné zdroje | částečně | implicitní | filename/mtime/OUT heuristiky, bez checkpoint kontraktu |
| ReRun | `desktop/application.py` | nový runtime nebo resume konkrétní dávky | — | — | využívá verified output evidence | workflow-specifická | ne | lineage není explicitně zaznamenána |
| Kaskády | `cascade_log.py`, cascade pipeline | samostatný logger | podle kroků | podle kroků | workflow-specificky | oddělená od hlavního log modelu | vlastní runtime logika | duplicitní logger a redakce, bez společného bundle |
| Diagnostika | pipeline/diagnostics | misc/files/OUT podle procesu | ne vždy | ne vždy | nejednotně | závisí na procesu | ne | část evidence pouze ve volném textu nebo souborech |
| Files API / vector stores | desktop/core panely | převážně provozní stav/log | ne jako Run RequestRecord | ne jako Run ResponseRecord | vzdálená ID | ne úplně | ne | není jednotně run-scoped |
| Git operace | desktop Git panel | provozní log / Git repo | — | — | Git metadata | mimo Run Bundle | ne | není explicitní součást běhu |
| Historie | `desktop/history.py` | čte LOG adresáře | zobrazuje soubory | zobrazuje soubory | ne jako artefakty | omezená | ne | plochý seznam, disk scan/fulltext při filtrování |

## Detail zjištění

### `kajovo/core/runlog.py`

- Vytváří základní adresáře a `run_state.json` + `events.jsonl`.
- `update_state()` mutuje jeden stavový dokument. Samotná změna stavu není vždy reprezentována samostatným append-only eventem.
- `event()` ukládá pouze `ts`, `type`, `data`; chybí monotónní sequence, stabilní ID, step, severity a explicitní relace.
- `save_json()` vytváří soubor a generický `file.saved.<kind>` event, ale nevytváří typovaný záznam requestu, response ani artefaktu.
- `_redact()` mění obsah evidence podle názvu klíčů a výskytu `Bearer`. To je neslučitelné s novým kanonickým kontraktem bezeztrátové evidence.
- Exact JSON je u části dat souběžně ukládán přes `recoverable_artifacts`, což dokazuje, že projekt již potřebuje bezeztrátovou recovery vrstvu; není však sjednocena s historií.

### `kajovo/core/recoverable_artifacts.py`

- Pozitivum: obsahově adresované JSON artefakty, SHA-256 kontrola a exkluzivní vytvoření.
- Omezení: index je verzovaný pouze jako jednoduchá mapa `name -> digest`; není to obecný `ArtifactRecord` a neobsahuje provenance.
- Ukládá JSON hodnoty, nikoli obecné binární vstupy/výstupy.
- `STATE_ARTIFACTS` pokrývá pouze vybrané state klíče.
- Index se přepisuje jako aktuální mapa, takže sám o sobě není append-only historie všech změn.

### `kajovo/core/response_journal.py`

- Pozitivum: před submit uloží přesný pracovní payload a hash; zná stavy `submitting`, `queued`, `in_progress`, `completed`, `rejected`, cancel a submission-unknown scénáře.
- Pozitivum: dokončené provider response ukládá před doménovým zpracováním.
- Omezení: journal je workflow-specifický a není reprezentován jako obecné `RequestRecord`/`ResponseRecord`.
- Poll requesty a retry chyby mají eventy, ale ne jednotné request IDs/record IDs.
- `save()` z kopie response odstraňuje `_request_id`, takže journal sám není plnou raw evidencí všech provider metadat; provider response soubor tuto mezeru pokrývá jen u některých stavů.

### `kajovo/core/pipeline.py` a `delivery_preparation.py`

- Requesty a response se v důležitých krocích ukládají, ale jejich sémantika je odvozována z názvu souboru a stage.
- `_log_api_action()` používá volný `api.trace` event a aktualizuje `last_response_id`; není to obecný StepRecord.
- Mnoho provozních stavů je dohledatelných, ale pro UI je nutná workflow-specifická interpretace.
- Validace kontraktů se provádějí, avšak nejsou obecně ukládány jako `ValidationRecord` s targetem a evidence.

### `kajovo/core/batch_completion.py`

- Dobře rozlišuje vzdálený stav Batch a import souborů; `completed` Batch tedy není automaticky dokončený projekt.
- `import_bundle()` odmítá incomplete response a validuje custom IDs/kontrakty/chunky/cesty.
- Některé funkce zapisují `run_state.json` přímo přes `atomic_write_text`, čímž obcházejí centrální event/evidence vrstvu.
- Raw output/error JSONL jsou zachovány, ale nejsou indexovány jako explicitní artefakty s provenance.

### `kajovo/core/cascade_log.py`

- Implementuje druhý téměř paralelní logger.
- Opakuje obsahovou redakci.
- Nemá `recoverable_artifacts` ekvivalent hlavního loggeru a nemá společný datový model evidence.
- Kaskády proto nelze v Historii zobrazit stejně hluboce bez workflow-specifických výjimek.

### `kajovo/desktop/recovery.py`

- Preferuje přesné `preparation_snapshot` artefakty, což je správný směr.
- Pokud přesná evidence chybí, prohledává request/response soubory podle mtime, názvů (`A2_response`, `B2_response`, `resume_structure`) a může hledat jiné runy se stejným OUT.
- Tyto fallbacky jsou vhodné pro legacy kompatibilitu, nikoli jako kanonický checkpoint mechanismus.
- Neexistuje explicitní objekt popisující, proč je konkrétní bod bezpečný pro pokračování a co se při pokračování zdědí/invaliduje.

### `kajovo/desktop/history.py`

- Současné UI je `QListWidget` běhů a druhý `QListWidget` request/response souborů.
- Filtry jsou RUN ID, response ID, datum a fulltext.
- Fulltext při každém filtrování načítá soubory z request/response adresářů; při stovkách/tisících běhů je to neškálovatelné.
- Neexistuje timeline, detail kroku, artifact manager, validation view, lineage, integrity view ani checkpoint selector.
- ReRun je jediná obecná navazující akce; jeho bezpečnost je dána konkrétní recovery implementací, ne explicitním checkpointem.

## Situace, které dnes nelze z logu univerzálně a spolehlivě určit

1. Přesný seznam všech logických kroků běhu v pořadí, pokud workflow nevytvořilo konzistentně pojmenované request/response artefakty.
2. Jednoznačný parent/child vztah dvou běhů po ReRun/repair/reuse.
3. Který konkrétní request vytvořil libovolný soubor v OUT bez workflow-specifické znalosti.
4. Která validace se vztahovala ke kterému requestu/response/artefaktu jako samostatný důkazní objekt.
5. Zda libovolný historický bod je bezpečný checkpoint bez spuštění konkrétní recovery logiky.
6. Úplný obsah vstupního/výstupního lokálního souboru poté, co externí cesta zmizela nebo byla změněna, pokud soubor nebyl samostatně archivován.
7. Jednotnou časovou osu přes GENERATE/MODIFY/QA/QFILE/KASKÁDY/BATCH v jednom schématu.
8. Integritu celého běhu jedním ověřením; existují dílčí hashe, ale ne bundle manifest pokrývající všechny důkazní soubory.
9. Historický stav některých mutable state polí před jejich přepsáním, pokud nebyl současně uložen event nebo content-addressed snapshot.
10. Přesnou provenance dat, která se do recovery dostala legacy fallbackem podle názvu/mtime/shody OUT.

## Cílová architektura po auditu

Implementace má zachovat kompatibilitu se současnými pracovními workflow, ale zavést nad nimi jednotnou evidenční vrstvu:

1. `RunBundle` jako kanonický self-contained archiv běhu.
2. Verzované recordy: Run, Step, Event, Request, Response, Validation, Artifact, Checkpoint, Lineage.
3. Append-only JSONL pro kroky/eventy/validace/lineage a immutable jednotlivé request/response/checkpoint dokumenty.
4. Binární i textové artefakty kopírované do bundle s SHA-256 a provenance.
5. Žádná obsahová redakce evidence; důvěra je řešena přístupem k prostředí.
6. Legacy adapter: staré běhy se nebudou přepisovat a neznámé hodnoty zůstanou explicitně neznámé.
7. Odvozený lokální index/read-model pro rychlé filtrování; musí být plně rebuildovatelný z Run Bundle/legacy zdrojů.
8. Run Explorer nad read-modelem, ne nad ad-hoc skenem request/response souborů.
9. Nové pokračování/clone/rerun/repair vždy vytvoří nový běh a `LineageRecord`; původní evidence zůstane immutable.
10. Bezpečnost checkpointu bude explicitní, hashově ověřitelná a kompatibilitně verzovaná.

Tento audit je vstupem implementace. Po dokončení musí následovat druhý procesní audit všech workflow a regresní testy.