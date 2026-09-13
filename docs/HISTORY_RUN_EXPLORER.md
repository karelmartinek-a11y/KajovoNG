# Historie — Run Explorer

## Cíl

Sekce **Historie** je pracovní dashboard nad kanonickou evidencí běhů. Výchozí pohled je lidsky čitelný; raw technická evidence je dostupná jedním přepnutím. UI nesmí zploštit vzdálený stav Batch na stav projektu a nesmí vydávat incomplete response za úspěšnou.

## Layout

Run Explorer má horní toolbar, levý seznam běhů a hlavní tabovaný detail.

### Horní toolbar

Obsahuje:

- fulltext nad odvozeným `HistoryIndex`;
- období: Vše, Dnes, Včera, posledních 7/30 dní, tento měsíc a vlastní interval od–do;
- kompatibilní přesný RUN ID a Response ID filtr;
- projekt, mode, stav a model;
- `Má chybu`, `Má BATCH`, `Má checkpoint`, `Má výstupy`, `Navazuje`;
- Reset, Obnovit, Exportovat běh a Otevřít Run Bundle.

Vlastní interval přijímá datum `DD.MM.YYYY`, `YYYY-MM-DD` nebo `DDMMYYYY`; lze zadat obě meze nebo pouze jednu. Pokud uživatel zadá obě meze opačně, UI je při vyhodnocení bezpečně prohodí. Filtrování nečte při každé změně celý obsah všech request/response souborů. Read-model je odvozený a rebuildovatelný.

### Levý seznam

Položka zobrazuje časové seskupení, RUN ID, projekt, mode, stav a počty kroků/odpovědí/souborů. Legacy běh je explicitně označen. Existující pending Batch zachovává akci **Dokončit**, která přebírá již odeslanou dávku a nevytváří nový generativní požadavek.

Hlavní akce jsou:

- ReRun jako nový běh;
- Pokračovat od checkpointu;
- Klonovat jako nový běh.

Akce se deaktivují, pokud chybí nutná evidence nebo je operace nebezpečná.

## Přehled

Zobrazuje projekt, RUN ID, mode, čas a trvání, stav, modely, počty request/response/error, Batch IDs, vstupy, výstupy, parent run a lidské shrnutí. Nahoře je skutečný stav integrity.

Checkpoint selector nabízí pouze explicitní `CheckpointRecord`. Unsafe záznam nelze použít pro pokračování.

Akce:

- Pokračovat;
- Klonovat;
- ReRun;
- Opravit;
- Otevřít výstupy;
- Otevřít Run Bundle.

## Průběh

Timeline je postavena nad StepRecord. Sloupce obsahují sequence, krok, stav, trvání, model, reasoning, počty request/response/artefaktů a checkpoint ID.

Dvojklik otevře **Detail kroku** se záložkami:

- Přehled;
- Vstupy;
- Odpovědi;
- Soubory;
- Validace;
- Události;
- Technické.

Legacy běh bez StepRecord zobrazí pouze sdělení, že kroky nejsou evidovány. UI je nevytváří z filename/mtime heuristik.

## Odpovědi

Centrální seznam ResponseRecord podporuje filtr stavu a response ID. Pravá část má:

- **Lidsky** — output text, případně čitelný obsah;
- **Technický detail** — celý normalizovaný record a full response bez obsahové redakce.

Status/incomplete reason zůstávají viditelné. Tisk a export TXT pracují s lidským pohledem; raw evidence zůstává v Run Bundle.

## Soubory

Artifact manager ukazuje název, roli, krok, velikost, SHA-256, čas, zdroj a reuse flag.

Akce:

- Otevřít;
- Náhled;
- Uložit jako;
- Použít v novém běhu;
- Zobrazit původ;
- Porovnat právě dva vybrané artefakty;
- Otevřít přímo evidovaný zdrojový RequestRecord;
- Otevřít přímo evidovaný zdrojový ResponseRecord.

Textové porovnání používá lokální unified diff. U souborů nad 5 MiB se plný diff kvůli responzivitě nenačítá a UI zobrazí jejich metadata/hash; binární artefakty se porovnávají podle integrity, velikosti a provenance. Náhled velkého souboru může být v UI omezen na 1 MiB, ale je označen jako náhled. Kanonický soubor v bundle zůstává kompletní a nezměněný.

**Použít v novém běhu** funguje pouze pro nový Run Bundle s explicitním `artifact_id`, `reusable=true` a platným SHA-256. Vybrané soubory se kopírují do izolovaného dočasného IN a nový MODIFY běh zaznamená lineage `reuse_artifacts`.

## Události

Strukturovaný event stream lze filtrovat podle severity a společného filtru nad event type, step, request, response a artifact vazbami. Detail zobrazuje celý EventRecord.

## Návaznosti

Strom ukazuje parent i children runs a typy clone/continue/rerun/repair/reuse atd. Dvojklik otevře související běh, pokud není skryt aktuálním filtrem. Převzaté artefakty, checkpoint a configuration jsou součástí LineageRecord.

## Technické

Obsahuje:

- `bundle.json`;
- `run.json`;
- `run_state.json`;
- integritu, checkpointy, lineage a počty recordů.

Technical viewer podporuje hledání a Kopírovat vše. Raw evidence se obsahově nemaskuje.

## Pokračovat

`Pokračovat od checkpointu`:

1. ověří integritu state snapshotu a required artefaktů/response;
2. načte přesný uložený `ui_state`;
3. pro GENERATE/MODIFY znovu validuje `preparation_snapshot`;
4. převezme pouze hashově doložené již hotové výstupy;
5. před startem ukáže source run, checkpoint, důvod bezpečnosti a upozornění, že navazující modelové kroky mohou být znovu placené;
6. vytvoří nový RUN;
7. do nového RUN uloží LineageRecord.

Zdrojový běh se nemění.

Pokud source run obsahuje již odeslaný Batch, pokračování nevytvoří druhý submit. Batch se dokončuje v původním běhu.

## ReRun

ReRun v novém Run Exploreru není staré heuristické „obnov co nejvíc“. Použije nejnovější explicitní bezpečný checkpoint a vytvoří nový běh s lineage `rerun`. Pokud bezpečný checkpoint neexistuje, ReRun je zablokován a uživatel může použít Klonovat, pokud existuje přesný uložený `ui_state`.

## Klonovat

Klon načte pouze přesně uložené zadání a nastavení. Nezdědí automaticky staré Response IDs, `resume_files` ani hotové výsledky. Uživatel může před spuštěním vše změnit. Po startu nového běhu se zapíše lineage `clone`.

## Opravit

Repair vychází z explicitního checkpointu a konkrétní evidence chyby. Platné předchozí podklady mohou být převzaty, ale zdrojový běh se nepřepisuje. Nový běh má lineage `repair`.

## Legacy chování

Legacy adapter je read-only. U chybějících údajů UI používá `Není evidováno`. Legacy run nemá automaticky safe checkpoint, StepRecord ani verified bundle integritu. Tím se zabrání tomu, aby UI vydávalo rekonstruovanou domněnku za skutečnou historickou evidenci.

## Responzivita

Index minimalizuje opakované filesystem scany. Velké soubory se načtou až při explicitním náhledu a UI preview má limit; kanonická evidence se nezkracuje. Další optimalizace může přidat stránkování tabulek bez změny datového kontraktu.
