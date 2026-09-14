# Run Bundle — kanonická evidence běhu

## Účel

Run Bundle je kanonická, bezeztrátová a rekonstruovatelná evidence nového běhu KájovoNG. Provozní textový log může existovat paralelně, ale nesmí být jediným zdrojem skutečnosti. Nový běh vytváří `kajovo.core.run_bundle.RunBundle`; staré běhy čte `LegacyRunAdapter` bez zpětného přepisování a bez domýšlení chybějících faktů.

## Důvěrné prostředí a zákaz obsahové redakce

Evidence se **obsahově nerediguje**. Payload, response, event data, artefakt a technický detail musí uchovat skutečný obsah. Ochrana je řešena řízením přístupu k pracovnímu prostředí, nikoli maskováním, ořezáním, nahrazením nebo skrytím důkazních dat.

UI smí pro výkon zobrazit pouze omezený náhled velkého souboru, pokud tento stav jasně označí. Kanonický artefakt se tím nesmí změnit ani zkrátit.

## Struktura

Každý nový `RUN_*` adresář obsahuje minimálně:

```text
RUN_<id>/
    bundle.json
    run.json
    run_state.json             # kompatibilní runtime state
    steps.jsonl
    events.jsonl
    requests/
    responses/
    validations/
    checkpoints/
    artifacts/
        inputs/
        intermediate/
        outputs/
        external/
        records.jsonl
    manifests/
    reports/
    lineage.json
    checksums.json
```

`run_state.json` zůstává kompatibilním runtime stavem stávajících workflow. Kanonický historický model je `run.json` + append-only/immutable evidence výše.

## Datové kontrakty

### RunRecord

Obsahuje verzi schématu, `run_id`, projekt, mode, časy, přesný stav a result class, label, parent/clone/checkpoint lineage, Batch IDs, root/last response IDs, model summary, input/output summary, bundle ID, hash konfigurace a hash bundle.

Důležité stavy se neslučují: `created`, `preparing`, `running`, `response_pending`, `batch_prepared`, `batch_pending`, `importing`, `completed`, `partial`, `failed`, `cancelled`, `stopped`, `dry_run`, `submission_unknown` a kompatibilní historické stavy.

### StepRecord

Jeden logický krok. Má stabilní `step_id`, monotónní sequence, stage/title/kind, čas, stav/progress, model/reasoning, explicitní request/response/artifact/validation vazby, retry group, parent step, checkpoint a lidské/technické shrnutí.

`steps.jsonl` je append-only stream verzí StepRecord; čtení použije poslední záznam daného `step_id`. Tím lze zachovat změny kroků bez přepisu historie.

### EventRecord

Append-only událost s `event_id`, `run_id`, `step_id`, monotónní sequence, timestamp, event type, severity, source module, operation, human/technical message, plnými data a vazbami na request/response/artifact.

Nové EventRecord zachovávají také kompatibilní `ts`/`type` aliasy pro starší recovery kód.

### RequestRecord

Ukládá skutečný pracovní payload, SHA-256 payloadu, endpoint, metodu, model, reasoning, retry attempt, roli požadavku, vzdálené request ID a zdrojovou cestu. Background Responses požadavek vzniká v evidence ještě před placeným submittem.

### ResponseRecord

Ukládá kompletní response, hash, provider response ID, stav, `output_text`, případnou parsovanou strukturu, usage včetně reasoning/cached tokenů, incomplete reason, error, validační stav a artefaktové vazby.

`incomplete` nebo error není `completed`.

### ValidationRecord

Samostatný důkaz významné validace: target type/id, validator, čas, stav, errors, warnings a evidence.

### ArtifactRecord

Každý důležitý soubor nebo externí zdroj má roli, kind, cestu v bundle, původní cestu/ID, zobrazovaný název, MIME, byte size, SHA-256, čas, source, vazbu na request/response/parent artifact, `reusable`, reconstruction role, metadata a dostupnost lokálního obsahu.

Lokální input/output artefakty se kopírují do bundle. Vzdálený zdroj, pro který aplikace nemá lokální bytes, je explicitně označen `available_local=false`; nesmí se předstírat, že je self-contained.

### CheckpointRecord

Checkpoint je explicitní objekt, nikoli libovolný event. Má hash state snapshotu, kompatibilitní verzi, required artifact/response IDs, `safe_to_continue`, důvod a invalidation rules.

`validate_checkpoint()` před pokračováním kontroluje kompatibilitu, stavový hash a všechny požadované archivované artefakty/response recordy. Unsafe checkpoint se nesmí použít pro automatické pokračování.

### LineageRecord

Vazba **source run → target run**. Podporované typy: `continue`, `clone`, `rerun`, `repair`, `reuse_artifacts`, `retry_batch`, `regenerate_partial`. Ukládá source checkpoint, user action, inherited artifact IDs, inherited configuration a notes.

Lineage se zapisuje pouze do nového cílového běhu. Zdrojový běh se kvůli pokračování nemění.

## Self-contained vstupy a výstupy

Při uložení `ui_state` RunLogger archivuje dostupné soubory lokálního `IN` jako `in_project_file`. Přiložené Files API IDs a vector-store IDs, pro něž nemá lokální bytes, jsou explicitní external records. Při zápisu OUT se skutečné bytes okamžitě archivují jako generated/modified artifacts. Import BATCH navíc archivuje raw output/error JSONL a výsledné soubory.

Archivace neznamená, že se pracovní OUT/IN cesta přesměruje. Bundle je důkazní kopie.

## Integrita

`seal()` vytvoří `checksums.json` s SHA-256 evidovaných souborů a `bundle_hash`; `run.json` obsahuje výsledný hash. `verify_integrity()` kontroluje chybějící soubory a hashové změny. Pozdější append-only událost na již uzavřeném běhu vyvolá opětovné zapečetění aktuální evidence.

UI zobrazuje:

- `Integrita ověřena`, nebo
- `Evidence je neúplná / změněná`, nebo
- u legacy běhu explicitně informaci, že historický běh nemá Run Bundle integritní manifest.

## Legacy

Legacy adapter:

- staré soubory nikdy nepřepisuje;
- nevytváří odhadem StepRecord ani CheckpointRecord;
- chybějící hodnoty zobrazuje jako neznámé;
- legacy request/response soubory může zpřístupnit pro čtení;
- legacy files může indexovat jako historickou evidence, ale nedává jim nový stabilní ArtifactRecord ID pro navazující automatizaci;
- legacy integrita je `legacy`, nikoli falešné `verified`.

## Derived History index

`HistoryIndex` zapisuje `LOG/history_index.json`. Je to odvozený read-model s metadata běhu, searchable textem, response IDs, modely a booleany error/Batch/checkpoint/output/lineage. Aktualizace používá mtime relevantních zdrojů a nenačítá plný obsah všech request/response souborů při každém stisku filtru.

Index není kanonická evidence. Lze jej smazat a kompletně znovu sestavit z Run Bundle/legacy adresářů.

## BATCH

Vzdálený stav Batch `completed` znamená pouze, že provider dokončil dávku. Projekt se považuje za dokončený až podle workflow po stažení, validaci a zápisu/importu výsledků. `batch_completion.py` synchronizuje přímé legacy state zápisy do Run Bundle a ukládá raw output/error JSONL, validation record a výsledné artefakty.

## Immutabilita a pokračování

Prohlížení Historie zdrojový běh nemění. `Pokračovat`, `ReRun`, `Opravit`, `Klonovat` a `Použít v novém běhu` vytvářejí nový běh nebo pouze připraví UI pro vznik nového běhu. Nový běh dostane LineageRecord. Odeslaný Batch se nikdy neklonuje druhým submittem; dokončuje se ve svém původním běhu.


## Obrazová evidence Komiksu

Režim `COMIC` ukládá `comic_operation_id`, `comic_library_dir` a `comic_batch_ids`. Obnova RunLogger je povolena jen pro totožné ID komiksové operace. Obrázkové odpovědi používají `image_evidence_version=1`: `b64_json` je reprezentováno přesným binárním artefaktem a údaji `binary_artifact.asset_id`, `artifact_id`, `encoding=base64`. Kompletní stažený provider soubor je bezeztrátový gzip binární artefakt. Textové logy neobsahují base64. Requesty, usage, provider IDs, snapshoty a hashe zůstávají trasovatelné. Místní knihovna drží normalizovaná metadata a výsledné verze; Historie nabízí návrat do Komiksu.
