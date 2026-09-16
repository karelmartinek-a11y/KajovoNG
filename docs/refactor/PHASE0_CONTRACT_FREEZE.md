# Phase 0 – contract freeze před architektonickým refaktorem

Tento dokument je normativní ochranný rámec pro refaktor KájovoNG. Nenahrazuje `docs/SSOT.md`; pokud je mezi tímto dokumentem a SSOT rozpor, platí SSOT.

## Baseline

- repository: `karelmartinek-a11y/KajovoNG`
- branch baseline: `main`
- baseline commit: `4ec9bc2f15cd4b1548048c4e3e0d0fb378f4c931`
- canonical product contract: `docs/SSOT.md`
- canonical production UI: `kajovo.studio.application.create_window`
- UI contract: `docs/UI_DESIGN.md`

Refaktor musí být prováděn po malých vratných krocích. Po každém kroku musí zůstat zelené povinné kontroly definované v `AGENTS.md` a CI.

## Forbidden changes

Během architektonického refaktoru je bez samostatného funkčního zadání zakázáno:

- redukovat existující funkce nebo uživatelská workflow;
- měnit význam režimů GENERATE, MODIFY, QA nebo QFILE;
- oslabit ochranu proti nechtěnému přepsání existujícího nebo mezitím změněného souboru;
- oslabit atomický zápis, hashování a forenzní RunBundle evidence;
- odstranit recovery/cancel chování;
- zaměnit lokální dokončení běhu za pouhé remote dokončení OpenAI batch/response operace;
- změnit retry/idempotency semantiku bez cíleného testu;
- nahrazovat funkci placeholderem, mockem nebo statusovým hackem v produkčním runtime;
- ukládat secrets do logu, RunBundle, settings JSON nebo chybové zprávy;
- odstranit legacy UI dříve, než budou jeho stále používané regresní kontrakty přeneseny na produkční `kajovo.studio`.

## Behaviour contract catalogue

Minimální kontrakty, které musí zůstat během migrace zachované:

| ID | Oblast | Kontrakt |
| --- | --- | --- |
| RUN-GEN-001 | GENERATE | Nový běh nesmí tiše přepsat existující cílové soubory. |
| RUN-MOD-001 | MODIFY | Soubor změněný externě po načtení nesmí být přepsán bez detekce konfliktu. |
| RUN-CAN-001 | lifecycle | Cancel musí skončit jako skutečné ukončení běhu, ne pouze změna UI textu. |
| RUN-ERR-001 | lifecycle | Neznámá chyba musí být dohledatelná v evidenci a nesmí být tiše spolknuta. |
| API-RET-001 | API | Idempotentní GET/status operace mohou být retryovány podle retry politiky. |
| API-RET-002 | API | Nejednoznačné POST `/responses` a vytvoření batch se nesmí automaticky opakovat způsobem, který může vytvořit duplicitní práci. |
| API-ERR-001 | API | Chyby autentizace, rate limitu, serveru a protokolu musí zůstat rozlišitelné alespoň v technické evidenci. |
| EVID-001 | RunBundle | Evidence musí být zapisována atomicky/durable a opatřena kontrolními hashi tam, kde je to součást dnešního kontraktu. |
| EVID-002 | RunBundle | Evidence nesmí obsahovat API klíče, SMTP hesla ani jiné secrets. |
| REC-001 | recovery | Přerušený běh musí zachovat dostatek stavu pro existující recovery mechanismus. |
| BATCH-001 | batch | Remote `completed` neznamená lokální `completed`, dokud není provedeno stažení, validace, delivery a evidence. |
| UI-001 | UI | Produkční vstup aplikace používá `kajovo.studio`, nikoliv legacy `kajovo.desktop`. |
| UI-002 | UI | Progress/cancel/error stavy musí být výsledkem skutečného backendového stavu. |
| SEC-001 | secrets | Persistentní OpenAI API key má být migrován do OS credential storage; environment smí zůstat pouze kompatibilní/runtime vstup. |

Při každém přesunu kódu musí mít dotčený kontrakt test nebo explicitní characterization test.

## Responsibility map

### Současný stav

`kajovo/core/pipeline.py` dnes sdružuje více odpovědností: konfiguraci běhu, Qt worker lifecycle, orchestrace režimů, API práci, polling, validaci odpovědi, delivery, ochranu přepisů, progress, diagnostiku a evidence. `kajovo/core/cascade_pipeline.py` rovněž obsahuje Qt worker závislosti.

### Cílový stav

```text
kajovo/core/runs/
    config.py
    contracts.py
    executor.py
    generate.py
    modify.py
    delivery.py
    diagnostics.py
    cancellation.py

kajovo/studio/workers/
    run_worker.py
```

Pravidlo závislostí po dokončení migrace:

```text
studio/app -> core
core -X-> studio
core/runs -X-> PySide6
```

Qt worker má být tenký adaptér. Doménová orchestrace musí být testovatelná bez QApplication/QThread.

## Side-effect inventory

Každý přesun musí explicitně zachovat následující kategorie vedlejších efektů:

1. **Filesystem** – project output, RunBundle/evidence, temp soubory, cache, atomic replace, hash validation.
2. **Network** – OpenAI REST/SDK, SMTP, SSH, Git remote.
3. **Process/OS** – subprocess, keyring/credential storage, environment, platform-specific launch/build.
4. **Git** – status/diff/add/commit/push a kontroly vyloučených citlivých souborů.
5. **UI events** – progress, cancel, completion, error a recovery signalizace.

Přesunutí kódu nesmí změnit pořadí kritických side effects, pokud pořadí chrání data (např. write -> fsync/replace -> hash/evidence nebo secret write -> readback -> teprve odstranění legacy hodnoty).

## Run lifecycle

Cílový obecný stavový model je:

```text
CREATED -> PREPARING -> REMOTE_WORK -> PROCESSING_RESPONSE -> DELIVERING -> FINALIZING -> COMPLETED
      \-> FAILED
      \-> CANCELLED
      \-> INTERRUPTED/RECOVERABLE
```

Konkrétní GENERATE/MODIFY/batch kroky zůstávají řízené SSOT. Tento model pouze zakazuje situaci, kdy UI označí běh za dokončený před lokálním delivery/finalization.

## Retry/idempotency matrix

| Operace | Automatický retry | Poznámka |
| --- | --- | --- |
| GET/retrieve status | ano | idempotentní operace, retry na timeout/connection/429/5xx dle klienta |
| polling vector store/file | ano | chyba musí být evidována; opakované selhání nesmí degradovat pouze na neurčitý timeout |
| POST `/responses` | výchozí ne | nejistý výsledek může znamenat již vytvořenou práci |
| create batch | výchozí ne | stejná ochrana proti duplicitnímu submitu |
| lokální delivery | řízeně | opakování nesmí obejít hash/conflict ochranu |

## Security contracts

- OpenAI API key: persistentně přes OS credential storage/keyring; po migraci odstranit legacy registry hodnotu teprve po úspěšném readbacku.
- Explicitní vymazání klíče musí potlačit zděděný stale environment stejně jako dnes.
- SMTP/SSH/jiné secrets nesmí být vypsány do user-visible nebo forensic logu.
- Neznámá exception smí obsahovat typ a bezpečný technický kontext, ne credential material.

## UI parity gate

Legacy `kajovo.desktop` je možné odstranit teprve když:

1. produkční `kajovo.app` a `kajovo.studio` jej neimportují;
2. testy, auditní skripty a recovery/batch pomocné cesty, které stále importují legacy moduly, mají ekvivalent v `kajovo.studio` nebo neutrální core vrstvě;
3. všechny obrazovky, dialogy, popupy, context menu, disabled/error/empty/loading stavy a keyboard shortcuts používané produkčním UI jsou pokryty novým auditem;
4. po odstranění legacy stromu projde plná test suite a UI render/audit.

## Migrační work packages

### R00 – contract freeze a architecture guards

- tento dokument;
- statické testy směru závislostí;
- přesný baseline SHA.

### R01 – CI hardening

- odstranit duplicitní instalaci balíčku;
- explicitní minimální permissions;
- checkout bez persistent credentials, pokud nejsou potřeba;
- zachovat Windows/Python 3.13 full suite;
- následně oddělit rychlé static/core kontroly a doplnit Python 3.12 compatibility lane.

### R02 – secret storage

- keyring jako primární persistentní store pro OpenAI API key;
- bezpečná jednorázová migrace Windows registry -> keyring;
- explicit-empty contract;
- rollback při neúspěšném write/readback.

### R03 – error observability

- odstranit tiché broad exception swallow v kritických cestách;
- normalizovat bezpečné evidence události;
- zachovat retry/fallback tam, kde je součástí kontraktu.

### R04 – pipeline decomposition

- nejprve extrahovat datové kontrakty, cancellation/progress porty a delivery;
- následně GENERATE/MODIFY executory;
- teprve potom ztenčit Qt worker;
- po každém kroku plná regrese.

### R05 – UI consolidation

- přenést testy a auditní nástroje na `kajovo.studio`/core;
- odstranit `kajovo.desktop` až při nulové produkční i testovací závislosti.

### R06 – typing/lint/coverage

- zapínat mypy a širší Ruff pravidla inkrementálně;
- nepřidávat `# type: ignore` jen pro dosažení zeleného CI;
- coverage nejprve změřit, až poté nastavit fail-under.

### R07 – final forensic audit

- porovnat všechny kontrakty proti baseline;
- plná pytest/Ruff/pip check/build/release kontrola;
- audit, že neexistují placeholder runtime cesty ani slepé UI prvky.

## Definition of Done

Refaktor je hotový pouze když současně platí:

- všechny existující funkční kontrakty a nové characterization testy procházejí;
- `kajovo/core/runs` neimportuje PySide6;
- `kajovo/app`/`kajovo/studio` neimportují legacy `kajovo.desktop`;
- po dokončení R05 `kajovo.desktop` neexistuje;
- GENERATE/MODIFY/batch/recovery/RunBundle kontrakty jsou zelené;
- secret migration je otestovaná včetně rollbacku a explicit-empty scénáře;
- povinné CI kontroly procházejí na přesném head SHA;
- nebyla zavedena žádná redukce scope, mock runtime ani placeholder.
