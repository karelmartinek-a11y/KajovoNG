# Postimplementační audit Historie, forenzního logování a Run Bundle

## 1. Rozsah a závěr

Tento dokument je povinný postimplementační audit přestavby Historie KájovoNG. Navazuje na `HISTORY_LOGGING_FORENSIC_AUDIT.md`, který popsal stav před změnou. Audit kontroluje implementované datové kontrakty, Run Bundle, Run Explorer a jejich vazby na GENERATE, MODIFY, QA, QFILE, KASKÁDY, Batch, recovery, ReRun/Continue/Repair/Clone/Reuse, stop/cancel, chyby, retry, import a zápisy souborů.

Výsledek: původní plochá Historie a rozptýlená evidence byly nahrazeny jednotnou forenzní vrstvou. Nové běhy používají verzovaný Run Bundle, typované recordy, explicitní checkpointy a lineage. Run Explorer čte odvozený rebuildovatelný index a zpřístupňuje lidský i raw pohled na evidenci. Zdrojový běh se při navazujících akcích nemění.

Kanonická evidence je **bezeztrátová a obsahově neredigovaná**. Kompatibilní runtime stav `run_state.json` zůstává mutable kvůli existujícím workflow, ale není kanonickou historií.

## 2. Původní nedostatky

Před změnou byly zásadní problémy:

1. skutečnost běhu byla rozptýlena mezi `run_state.json`, `events.jsonl`, requests/responses, manifesty, recovery artefakty a workflow-specifická data;
2. chyběl společný `RunRecord`, `StepRecord`, `RequestRecord`, `ResponseRecord`, `ValidationRecord`, `ArtifactRecord`, `CheckpointRecord` a `LineageRecord`;
3. eventy neměly jednotné stabilní ID a monotónní sequence;
4. provenance souboru se často dala zjistit jen workflow-specifickou interpretací názvu nebo kontextu;
5. bezpečný bod pokračování nebyl explicitní objekt;
6. vztah mezi původním a navazujícím během nebyl jednotně evidován;
7. lokální vstup nebo výstup mohl po změně externí cesty přestat být rekonstruovatelný;
8. staré UI Historie bylo ploché a při filtrování četlo velké množství souborů z disku;
9. evidence používala historické mechanismy obsahové redakce, které byly v rozporu s novým požadavkem důvěrného prostředí;
10. neexistoval jediný integritní manifest celého běhu.

Podrobný stav před implementací je v `HISTORY_LOGGING_FORENSIC_AUDIT.md`.

## 3. Implementované datové kontrakty

`kajovo/core/run_bundle.py` zavádí kanonické verzované recordy:

### RunRecord

Ukládá identitu běhu, projekt, mode, created/started/finished, přesný stav a result class, parent/clone/continue vazby, Batch ID, response ID, model summary, input/output summary, configuration hash a bundle hash.

### StepRecord

Reprezentuje logický krok procesu. Má stabilní `step_id`, sequence, stage/title/kind, čas, stav/progress, model/reasoning a explicitní vazby na requesty, response, artefakty, validace a checkpoint.

`steps.jsonl` je append-only proud verzí kroku; čtecí vrstva použije poslední verzi stejného `step_id`, aniž by se ztrácela starší evidence.

### EventRecord

Append-only událost s `event_id`, `sequence`, timestamp, severity, source module, operation, lidskou a technickou zprávou a vazbami na request/response/artifact. Sequence je monotónní v rámci běhu.

### RequestRecord

Ukládá přesný skutečný payload, endpoint/metodu/model, reasoning metadata, retry attempt, roli a SHA-256 payloadu. Request je evidován jako neměnný důkaz.

### ResponseRecord

Ukládá kompletní provider response, provider response ID, přesný status, output text/strukturu, usage včetně reasoning/cached tokenů, incomplete reason, error a SHA-256. `incomplete` nebo error se nikdy nepovažuje za `completed`.

### ValidationRecord

Ukládá významné explicitně evidované validace včetně targetu, validatoru, statusu, errors/warnings a evidence. Batch import používá strukturované ValidationRecord pro rozhodnutí o importu. Další validační výsledek je současně dohledatelný v Step/Event/Response evidence; nízkoúrovňová dílčí kontrola není nutně samostatný record, pokud je součástí jednoho vyššího validačního kroku.

### ArtifactRecord

Každý archivovaný významný lokální soubor má stabilní ID, role/kind, cestu v bundle, původ, MIME/size, SHA-256, request/response provenance, reuse flag a reconstruction role. Lokální vstupy a výstupy se archivují jako důkazní kopie. Vzdálený prostředek, jehož bytes aplikace nikdy lokálně neměla, je explicitně označen `available_local=false`.

### CheckpointRecord

Checkpoint je explicitní objekt se state snapshotem a hashem, kompatibilitní verzí, required artifact/response IDs, `safe_to_continue`, důvodem a invalidation rules. `validate_checkpoint()` před pokračováním ověřuje jeho integritu.

### LineageRecord

Ukládá relaci source run → target run. Podporuje `continue`, `clone`, `rerun`, `repair`, `reuse_artifacts`, `retry_batch` a `regenerate_partial`. Source checkpoint, inherited artifacts/configuration a notes jsou explicitní.

## 4. Struktura Run Bundle

Nový běh používá následující logickou strukturu:

```text
RUN_<id>/
    bundle.json
    run.json
    run_state.json
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

`run_state.json` je runtime compatibility state. Kanonickou historickou pravdou jsou recordy a artefakty Run Bundle.

`seal()` vytváří `checksums.json` a bundle hash. `verify_integrity()` kontroluje chybějící nebo pozměněné evidované soubory. Run Explorer rozlišuje:

- Integrita ověřena;
- Evidence je neúplná / změněná;
- legacy běh bez Run Bundle integritního manifestu.

## 5. Zákaz obsahové redakce

Nová kanonická evidence se **obsahově nerediguje, nemaskuje, neořezává ani nenahrazuje**. Přesný request, response, event data, artefakt a technický detail musí zachovat skutečný obsah. Historická kompatibilní metoda `_redact()` u nové evidence obsah nemění.

Ochrana je řešena přístupem k důvěrnému pracovnímu prostředí. Toto pravidlo je zapsáno v `SSOT.md`, `RUN_BUNDLE_SPEC.md`, `CONTEXT_COMPILER.md` a README.

UI smí pro výkon zobrazit jasně označený omezený náhled velkého artefaktu. Kanonický soubor v bundle se tím nezkracuje.

Legacy běh může obsahovat data, která byla redigována starší verzí. Původní chybějící obsah se zpětně nedoplňuje odhadem.

## 6. Run Explorer — implementovaný UX

Sekce Historie je dashboard nad `HistoryIndex` a Run Bundle.

### Horní filtry

Implementováno:

- fulltext;
- RUN ID;
- Response ID;
- projekt;
- mode;
- stav;
- model;
- Má chybu;
- Má BATCH;
- Má checkpoint;
- Má výstupy;
- Navazuje;
- Dnes;
- Včera;
- Posledních 7 dní;
- Posledních 30 dní;
- Tento měsíc;
- vlastní interval od–do;
- Reset;
- Obnovit;
- Exportovat Run Bundle;
- Otevřít Run Bundle.

Vlastní interval podporuje obě nebo jednu mez a běžné datumové formáty.

### Levý seznam běhů

Zobrazuje časové seskupení, RUN ID, projekt, mode, stav, počet kroků, response a artefaktů, legacy indikaci a dostupnou akci Dokončit pro nepřevzatý Batch.

### Přehled

Zobrazuje projekt, RUN ID, mode, čas, trvání, stav, modely, počty API requestů/response/chyb, Batch ID, vstupy/výstupy, parent run, lidské shrnutí, integritu a checkpoint selector.

### Průběh

Timeline používá StepRecord. Dvojklik otevře Detail kroku se záložkami Přehled, Vstupy, Odpovědi, Soubory, Validace, Události a Technické.

### Odpovědi

Výchozí je lidský pohled. Technický detail ukazuje celý normalizovaný ResponseRecord a full response bez redakce. Filtruje se stav a Response ID; incomplete reason je viditelný.

### Soubory

Artifact manager zobrazuje role, krok, velikost, SHA-256, čas, původ a reusable status. Dostupné akce:

- Otevřít;
- Náhled;
- Uložit jako;
- Použít v novém běhu;
- Zobrazit původ;
- Porovnat právě dva artefakty;
- Zdrojový request;
- Zdrojová response.

Textové porovnání vytváří unified diff. Velké nebo binární soubory se kvůli responzivitě nečtou celé jen pro porovnání; UI ukáže jejich hash/metadata a provenance.

### Události

Event stream filtruje severity a identifikační vazby. Detail ukazuje celý EventRecord.

### Návaznosti

Zobrazuje parent i children runs a typ relace. Dvojklik otevře související běh, pokud není skryt filtrem.

### Technické

Zobrazuje `bundle.json`, `run.json`, `run_state.json`, integritu, checkpointy, lineage a počty recordů. Raw viewer má vyhledávání a Kopírovat vše.

## 7. Interaktivní akce a neměnnost historie

### Pokračovat

- pouze z explicitního safe checkpointu;
- ověřuje state hash a required artefakty/response;
- pro GENERATE/MODIFY znovu validuje preparation snapshot;
- přebírá jen hashově doložené již hotové výstupy;
- před startem ukáže source run, checkpoint, důvod a informaci o možné nové placené práci;
- vytvoří nový RUN a LineageRecord;
- source run se nemění.

### ReRun

Použije poslední explicitní safe checkpoint a vytvoří nový run s lineage `rerun`. Bez safe checkpointu nepoužívá heuristický replay a odkáže uživatele na Clone.

### Klonovat

Načte pouze přesně uložený `ui_state`. Automaticky nepřebírá response ID, resume files ani hotové výsledky. Lineage se zapíše při vytvoření nového běhu.

### Opravit

Použije explicitní checkpoint a evidovanou chybu. Předchozí platná práce se může převzít, zdroj se nepřepisuje.

### Použít soubory znovu

Pouze pro stabilní `ArtifactRecord` s `reusable=true`. Před kopírováním se ověří SHA-256. Artefakty se vloží do izolovaného dočasného IN a nový MODIFY běh dostane lineage `reuse_artifacts`.

### Již odeslaný Batch

Continue/ReRun nevytvoří duplicitní Batch submit. Již odeslaná dávka se dokončuje v původním běhu.

## 8. Legacy migrace

`LegacyRunAdapter` je read-only:

- nepřepisuje staré soubory;
- nevytváří odhadem StepRecord;
- nevytváří odhadem CheckpointRecord;
- nevymýšlí stabilní ArtifactRecord provenance pro starý soubor;
- chybějící fakta zůstávají neznámá;
- integrita je `legacy`, nikoli falešné `verified`;
- Continue je bez explicitního checkpointu blokovaný.

Odvozený `HistoryIndex` může legacy běh indexovat pro hledání, ale nepovyšuje odhad na kanonický důkaz.

## 9. Postimplementační procesní audit

| Proces | RunRecord | Step/Event evidence | Request / Response | Artefakty | Checkpoint / lineage | Výsledek auditu |
|---|---|---|---|---|---|---|
| GENERATE LIVE | ano | ano | přesné pracovní payloady a response | vstupy i zapsané výstupy s SHA-256 | přípravné/dokončené checkpointy; nové akce přes lineage | vyhovuje |
| MODIFY LIVE | ano | ano | přesné pracovní payloady a response | IN důkazní kopie + modified outputs | stejné jako GENERATE | vyhovuje |
| GENERATE BATCH | ano | příprava + Batch eventy | živá příprava + pracovní JSONL/output | manifest, raw output/error, výsledné soubory | připravený manifest / importní checkpoint; duplicate submit blokován | vyhovuje |
| MODIFY BATCH | ano | příprava + Batch eventy | živá příprava + pracovní JSONL/output | původní vstup, manifest, raw output/error, změněné soubory | ochranné hashe + explicitní checkpoint | vyhovuje |
| QA | ano | stav/event evidence | exact request/response | případné vstupní reference | dokončený běh; Clone/lineage | vyhovuje |
| QFILE | ano | stav/event evidence | exact request/response | výsledný soubor archivován | dokončený běh; navázání pouze bezpečně | vyhovuje |
| KASKÁDA | ano | společný RunLogger; kaskádové kroky/eventy | request/response každého pokusu | expected outputs a file refs | společný Run Bundle | vyhovuje |
| response recovery | ano | polling/recovery eventy | originální request hash + stejné response ID | obnovené podklady | nepřidává nové generování při známém response ID | vyhovuje |
| Continue | nový target run | nový běh | až následná explicitní pracovní operace | verified převzaté podklady | `continue` lineage + source checkpoint | vyhovuje |
| ReRun | nový target run | nový běh | pouze práce po explicitním checkpointu | verified převzaté podklady | `rerun` lineage | vyhovuje |
| Repair | nový target run | nový běh | konkrétní navazující práce | platné předchozí podklady | `repair` lineage | vyhovuje |
| Clone | nový target run | nový běh | staré response se nepřebírají | zadání/nastavení | `clone` lineage | vyhovuje |
| Reuse artifacts | nový target run | nový běh | až následná pracovní operace | SHA-256 ověřené reusable artefakty | `reuse_artifacts` lineage | vyhovuje |
| Stop | stav/event | kooperativní stop je evidovaný | neprovádí skrytý nový request | existující evidence zůstává | source run se nemění | vyhovuje |
| Cancel Response | stav/event | vzdálený výsledek cancel je evidovaný | cancel míří na existující response | evidence se zachová | bez implicitního replay | vyhovuje |
| Cancel Batch | Batch stav/event | používá existující Batch ID | cancel existující dávky | manifest/output evidence zůstává | nevytváří nový Batch | vyhovuje |
| `submission_unknown` | přesný stav | evidence nejistoty | automatický druhý generativní submit zakázán | input_file_id/manifest zachován | recovery musí nejdřív dohledat původní submit | vyhovuje |
| incomplete response | přesný status | error/incomplete evidence | full response a reason | výstup se nepovažuje za validní | neposouvá úspěšný checkpoint | vyhovuje |
| network retry | event evidence | čtecí/retriable operace oddělené od generujících POST | generující POST nemá slepý retry | beze změny | zabraňuje dvojímu generování | vyhovuje |
| Batch import | importní evidence | oddělený vzdálený stav/import | raw output/error zachován | atomické souborové zápisy + SHA-256 | `files_complete_unverified` není funkční success | vyhovuje |
| lokální zápis OUT | fs event | každý zápis evidovaný | — | okamžitě archivovaný output artefakt | hash chrání další recovery | vyhovuje s netransakčním multi-file omezením |
| Files API / vector refs v běhu | Run evidence | external artifact event/record | relevantní pracovní request je zachován | lokálně nedostupný obsah `available_local=false` | stav není domýšlen | vyhovuje explicitní hranici |
| standalone Git/SMTP/SSH/resource správa | mimo obecný run lifecycle, pokud nejde o součást běhu | provozní evidence | podle konkrétní operace | externí systém je zdroj pravdy | není automaticky Run checkpoint | záměrná hranice rozsahu Run Bundle |

## 10. Stavové zásady prověřené auditem

- `completed` vzdáleného Batch není `completed` projektu;
- `files_complete_unverified` znamená kompletní import souborů, nikoli funkční ověření projektu;
- `incomplete` Response není úspěšná odpověď;
- `response_pending` znamená známé ID a přerušené sledování;
- `submission_unknown` znamená nejistotu po submitu a zakazuje automatický nový generativní POST;
- `partial`, `failed`, `cancelled`, `stopped`, `dry_run` a další stavy se neslučují do jednoho „hotovo“;
- checkpoint není libovolný event a vzniká pouze jako explicitní objekt.

## 11. Testy

Regresní pokrytí zahrnuje mimo jiné:

- exact no-redaction evidence;
- monotónní event sequence;
- RequestRecord payload hash;
- ResponseRecord completed/incomplete a token usage;
- binární self-contained artifact a detekci manipulace;
- safe checkpoint + chybějící artefakt;
- checkpoint s kanonickým ResponseRecord;
- lineage bez změny source run;
- legacy bez domýšlení kroků/checkpointů;
- rebuildovatelný/searchable HistoryIndex;
- legacy Run Explorer;
- fulltext/mode filter;
- vlastní datumový interval;
- přítomnost akcí compare/source request/source response;
- Windows UTF-8 čtení runtime evidence;
- no-redaction recovery journal;
- Photo Studio model capability test.

CI je offline vůči placeným OpenAI generativním endpointům. Skutečné produkční OpenAI/SMTP/SSH/Windows-native interakce CI nesimuluje.

## 12. Dokumentace a migrace

Aktualizováno:

- `docs/SSOT.md`;
- `docs/UI_DESIGN.md`;
- `docs/UI_VALIDATION_MATRIX.csv`;
- `docs/REQUEST_MATRIX.md`;
- `README.md`;
- `docs/CONTEXT_COMPILER.md`.

Přidáno:

- `docs/HISTORY_LOGGING_FORENSIC_AUDIT.md`;
- `docs/RUN_BUNDLE_SPEC.md`;
- `docs/HISTORY_RUN_EXPLORER.md`;
- tento `docs/HISTORY_LOGGING_IMPLEMENTATION_REPORT.md`.

Staré běhy nejsou přepisovány ani automaticky migrovány na falešně úplný nový model. Nová metadata/index jsou odvozená; původní evidence zůstává nedotčená.

## 13. Zbývající reálné hranice rekonstrukce

Následující body nejsou skryté implementační chyby, ale objektivní hranice dostupné evidence:

1. **Legacy běh:** pokud stará verze určitý údaj nebo soubor nikdy neuložila, nový adapter jej nemůže obnovit. Zobrazuje `Není evidováno` a nic si nevymýšlí.
2. **Historicky redigovaný legacy obsah:** pokud stará verze původní obsah před uložením změnila, nový systém nemá zdroj pro zpětné získání originálu.
3. **Externí remote-only bytes:** Files API/vector-store objekt, jehož bytes nebyly v době běhu lokálně k dispozici, je evidován jako external artifact s `available_local=false`; identita/provenance je zachována, ale Run Bundle fyzicky neobsahuje data, která aplikace nikdy neměla.
4. **Externí služby mimo run lifecycle:** samostatná správa Git, SMTP, SSH nebo resource panelu není automaticky považována za modelový běh. Pokud je konkrétní diagnostická operace součástí běhu, její relevantní evidence se ukládá; samostatná administrační operace má vlastní provozní hranici.
5. **Multi-file filesystem transakce:** jednotlivý zápis je atomický a evidovaný, ale více souborů jako celek není filesystemová transakce. Pád mezi dvěma zápisy může zanechat validně evidovanou částečnou změnu.
6. **Poskytovatel:** lokální Run Bundle prokazuje to, co aplikace odeslala, přijala a uložila. Nemůže dokazovat interní stav poskytovatele, který API nikdy nevrátilo.
7. **CI:** offline testy nedokazují aktuální oprávnění konkrétního OpenAI účtu ani dostupnost externí SMTP/SSH infrastruktury.

Tyto limity se v UI a dokumentaci nepřetvářejí na „úspěšně rekonstruováno“.

## 14. Akceptační stav

Cílový standard přestavby je naplněn:

- Historie je Run Explorer, nikoli plochá tabulka;
- běhy lze filtrovat časově, obsahově, podle projektu/mode/stavu/modelu a příznaků;
- každý nový běh má přehled a typovanou timeline, pokud workflow dané kroky vytváří;
- requesty/response jsou dohledatelné v raw i lidské podobě;
- artefakty mají SHA-256 a provenance;
- bezpečné artefakty lze znovu použít;
- Continue/ReRun/Repair/Clone vytvářejí nový run;
- explicitní lineage je dohledatelné;
- Run Bundle má integritní kontrolu;
- canonical raw evidence se nerediguje;
- legacy evidence se nepředstírá jako úplná;
- Batch a Response stavy se nezplošťují;
- HistoryIndex je rebuildovatelný;
- dokumentace odpovídá nové architektuře.
