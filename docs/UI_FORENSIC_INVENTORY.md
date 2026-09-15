# Výchozí forenzní inventář desktopového UI

Tento dokument zachycuje výchozí rozhraní `kajovo/desktop`. Produkční implementaci `kajovo/studio`, její backendové vazby a ověření popisuje [UI_DESIGN.md](UI_DESIGN.md). Výchozí snímky jsou v `docs/ui/before`; inventář studia v `docs/ui/studio-inventory.json`.

## Účel

Tento dokument je lidsky čitelná mapa celého desktopového rozhraní KájovoNG. Přesný strojově čitelný inventář všech konstrukcí ovládacích prvků, signálů, handlerů, popupů, validačních bodů a dohledatelných backendových volání generuje `scripts/audit_ui.py` z AST všech modulů `kajovo/desktop`. Audit je odvozený read model; kanonickou pravdou zůstává zdrojový kód.

Příkaz:

```text
python scripts/audit_ui.py --write docs/UI_INVENTORY.json --check
```

`--check` blokuje explicitní vazbu typu `self.neexistujici_metoda`. Inventář u každého prvku eviduje zdrojový soubor a řádek, třídu/metodu, druh komponenty, přiřazení, popisek, signál, cílový handler, popup, validační důkaz a backendová volání handleru. Tím je možné audit opakovat po každé změně UI bez ručního odhadu.

## Zásadní pravidlo funkčnosti

Každý **interaktivní** prvek musí mít skutečný handler nebo explicitní pozdější signal binding. Handler nesmí pouze měnit kosmetický stav, pokud text prvku slibuje pracovní operaci. Operace, která pracuje s API, soubory, Git, SMTP, SSH, Run Bundle, BATCH nebo persistencí, musí volat odpovídající backendovou vrstvu. Prezentační prvky jako popisek, nadpis, badge, náhled nebo vysvětlující text backendovou operaci přirozeně nemají.

Zakázány jsou placeholder tlačítka, simulovaný progress, tlačítka bez účinku a UI volby, které backend neumí validovat nebo provést.

## Modulový inventář

| Modul | UI / odpovědnost | Skutečný backend / funkce |
|---|---|---|
| `application.py` | Hlavní shell, navigace, Zadání, modely, režimy, LIVE/BATCH, Maximum Quality, IN/OUT, diagnostika, spuštění/zastavení, save/load/new | `RunWorker`, pipeline, request rules, model registry, model matrix, konfigurace, Files/vector-store selection, recovery/rerun |
| `photos.py` | Photo Studio, fotografie, náhledy, prompt, šablony, profesionalizace promptu, Image Edit BATCH joby | `photo_prompt`, `photo_templates`, `photo_batch`, `OpenAIClient`, `Jobs` |
| `cascades.py` | Editor a runtime kaskád, kroky, vstupy, proměnné, výstupy, schéma, očekávané cesty | `cascade_types`, `cascade_contract`, `cascade_store`, `cascade_pipeline`, model registry |
| `resources.py` | Files API a vector stores, upload/delete/attach, soubory store, atributy | `OpenAIClient` Files a Vector Stores API, lokální attachment stav |
| `batches.py` | Stav pracovních dávek, lokální import, dokončení, cancel, retry/repair | `batch_completion`, `OpenAIClient`, Run evidence |
| `batch_view.py` | Sdílené read-only popisky stavu dávky a legacy preflight | `batch_completion` metadata; žádný placený request |
| `history_run_explorer.py` | Run Explorer, filtry, timeline, response, artefakty, eventy, lineage, technical view | `RunBundle`, `HistoryIndex`, `LegacyRunAdapter`, integrity validator |
| `history_enhanced.py` | Rozšířené akce nad Run Explorerem, artefakty a navigace evidence | Run Bundle/History adapter a bezpečné UI akce |
| `history_actions.py` | Instalace Run Exploreru do hlavního shellu | `history_actions_impl`, Run evidence |
| `history_actions_impl.py` | Continue, Clone, ReRun, Repair, reuse artefaktů | checkpoint validator, lineage, nový Run, snapshot/artefakt integrity |
| `history.py` | Kompatibilní historie / pomocné view | read-only historická evidence |
| `recovery.py` | Obnova přerušených response/batch stavů | response journal, run state, API retrieve; bez slepého generativního replay |
| `versions.py` | Git stav, remote, push/pull, milníky, strom, editor, diff | Git backend a bezpečný filesystem |
| `settings.py` | API klíč, provozní limity, bezpečnost vstupů, SMTP | `config`, `secret_store`, `notifications` |
| `windows.py` | Model picker, oddělené sekce, splash | model registry/capability cache, skutečné widgety hlavního shellu |
| `dialogs.py` | Informační/varovné/potvrzovací dialogy, file picker, text input, progress | skutečné signal bindings, `ProgressEvent`/`ProgressClock`, cancellation callbacks |
| `jobs.py` | Asynchronní GUI joby | `QThread`, skutečný operation callable, result/error/cancel stav |
| `design.py` | Design tokeny a databáze komponent | prezentační vrstva; žádná pracovní operace |

`__init__.py` neobsahuje samostatné interaktivní UI.

## Hlavní informační architektura

### Zadání

Interaktivní skupiny:

- projekt a pracovní režim;
- model a modelové override pro přípravné/generační fáze;
- LIVE/BATCH a Maximum Quality;
- prompt a výsledek;
- Files API, vector stores a lokální IN;
- OUT a vazba IN = OUT;
- previous response / návaznost;
- teplota a další modelové parametry pouze podle capability kontraktu;
- diagnostika Windows a SSH;
- Nový, Uložit, Načíst, Spustit, Zastavit a ReRun;
- vstup do správy aktivních běhů.

Spuštění prochází validačním backendem a teprve poté vytváří `RunWorker`. UI nevytváří zkušební placený Responses/BATCH request.

### Fotografie

Interaktivní skupiny:

- Přidat fotografie / složku, výběr, odebrání a náhled;
- knihovna šablon: použít, uložit jako, upravit, duplikovat, smazat;
- prompt editor;
- `Vylepšit prompt` přes skutečný Responses request a možnost vrátit původní text;
- model Image Edit, kvalita, velikost, formát a OUT;
- jediná cesta vlastní editace fotografie: pracovní `/v1/images/edits` BATCH;
- seznam jobů, refresh stavu, stažení, cancel, otevření OUT a před/po náhled.

### Kaskády

Každý krok má skutečnou definici v datovém kontraktu. UI umožňuje správu definic a pořadí, model, instrukce, text/JSON vstupy, lokální a API soubory, proměnné předchozích kroků, návaznost, typ výstupu, schema, manifest/prompts a očekávané cesty. Spuštění používá `cascade_pipeline`; editor není samostatná simulace workflow.

### Zdroje

Files API i vector stores jsou skutečné remote operace. Připojit/odpojit znamená změnu konfigurace pracovního běhu; upload/list/delete/create/remove/attributes jsou API operace. Destruktivní akce vyžadují potvrzení.

### Dávky

UI odděluje stav OpenAI Batch od lokálního převzetí výsledků. `completed` vzdáleného Batch není automaticky dokončený projekt. `Dokončit`, cancel, retry souborů a repair používají existující batch/run evidence a nesmějí vytvářet skrytý duplicitní submit.

### Historie

Produkční Run Studio obsahuje:

- `history_data.py`: oddělená metadata/detail, invalidovaná omezená cache a kontrola checkpointů bez GUI I/O;
- `history_overview.py`: skutečné textové výstupy a lidský inspektor fáze;
- `history_cascade.py`: model a malovaný časový průběh kaskády se zvýrazněním skutečných závislostí;
- všechny primární i sekundární akce pod jednotnou politikou, včetně Komiksu, Dávky a zdrojového běhu;
- samostatný technický inspektor zachovávající i původní transportní záznamy, které nejsou uživatelskými fázemi;
- přímé akce v detailu navázané na jeho zdroj, nikoli na jiný řádek historie v pozadí.

- virtuální seznam běhů s malovanými DAW stopami skutečných StepRecordů;
- fulltextový HistoryIndex a časové/projektové/režimové/stavové/transport/modelové filtry;
- zoom, Fit, výběr fáze a kompaktní parent/children overlay;
- typové GENERATE, MODIFY, QA, QFILE, KASKADA a COMIC detaily;
- generický detail budoucích kindů, raw request/response, validace, události a integritu;
- ArtifactRecord náhledy, SHA kontrolu, export, textový diff a binární metadata;
- lokální branch composer a centrální availability policy;
- přímé spuštění Continue/Rerun/Repair přes existující worker a Operations;
- clone-only přechod do Zadání a sdílené převzetí původního BATCH.

Continue, ReRun a Repair vytvoří a okamžitě spustí nový run přímo v Historii. Clone otevře editovatelné Zadání a lineage zapíše až jeho pozdější start. Zdrojový Run Bundle se nemění. Staré `workbench.resume`, `resume_notice` a `resume_submitted` nejsou součástí produkční cesty.

Forenzně nalezené producenty Run Bundle: GENERATE, MODIFY, QA, QFILE, KASKADA a COMIC. Photo Studio/Image Batch používá vlastní job store a do Run Bundle Historie se nezapisuje. Legacy producent zůstává read-only bez dopočtených kroků a checkpointů.

### Verze projektu

Git operace, milníky, strom projektu, editor a diff používají skutečný Git/filesystem backend. Obnova a odstranění jsou potvrzované destruktivní operace.

### Modely

Modelový výběr vychází z účtového katalogu a pevné capability matice. UI nesmí model tiše nahradit. Detail modelu je read model nad reálnými capability daty.

### Nastavení

API klíč je přes `secret_store`; uložení je považováno za úspěšné až po ověření persistence. SMTP test skutečně volá notifikační backend. TLS a SSL se vzájemně vylučují. Bezpečnost vstupů je oddělena od forenzní evidence: kanonický Run Bundle se obsahově nerediguje.

## Popup a dialogová taxonomie

| Typ | Účel | Kontrakt |
|---|---|---|
| `DetailDialog` | info/warning/error a technické podklady | lidské sdělení je výchozí, raw detail na vyžádání |
| potvrzovací `DetailDialog` | destruktivní nebo placená pracovní akce | explicitní Potvrdit/Zrušit; žádné skryté pokračování |
| `FilePicker` | otevření/uložení/adresář | jednotné české popisky, omezení velikosti okna |
| `TextInputDialog` | krátký/volný text | explicitní potvrzení a cancel |
| `ProgressDialog` | hlavní modelový běh | strukturovaný `ProgressEvent`; procenta pouze z reálných jednotek |
| `TaskProgressDialog` / upload varianta | asynchronní podpůrná operace | stav, log, cancel callback, terminální success/fail/cancel |
| `ModelPicker` | volba kompatibilního modelu | data z model registry/capability cache |
| `DetachedPageDialog` | samostatné okno existující sekce | přesouvá skutečný widget, nevytváří duplikovanou logiku |
| `StepDetailDialog` | detail kroku Historie | čte Run Bundle recordy a raw evidence |
| `TemplateDialog` | Photo prompt template | ukládá přes `PhotoTemplateStore` |

## Validační vrstvy

1. **Ovladač** – rozsah, typ a dostupnost prvku.
2. **UI guard** – povinné hodnoty, potvrzení destruktivní akce, jasná chyba člověku.
3. **Backend kontrakt** – skutečné API/model/filesystem pravidlo; UI jej nesmí nahrazovat.
4. **Provider/IO výsledek** – skutečná odpověď nebo chyba.
5. **Forenzní evidence** – pokud je operace součástí run lifecycle, request/response/event/artifact je uložen v Run Bundle.

Deaktivované tlačítko nikdy není jedinou validací. Backend musí odmítnout neplatnou konfiguraci i při programovém volání.

## Vizuální systém

Kanonické tokeny jsou v `kajovo/desktop/design.py` v `DESIGN_TOKENS`.

### Barevné spektrum

- canvas: velmi světlá studená šedá;
- surface: čistá bílá;
- sidebar: tmavá modro-inkoustová;
- primary: petrolejová pro hlavní pracovní akci;
- accent/info: tlumená modrá pro navigační a informační akce;
- success: zelená;
- warning: jantarová;
- danger: karmínová;
- focus: modrá s vysokou čitelností;
- text: tmavá inkoustová, secondary text tlumený modrošedý.

Barva nenahrazuje textový stav. Chyba, warning, success a čekání musí mít vždy i slovní označení.

### Databáze komponent

`COMPONENT_CATALOG` definuje kanonické rodiny:

- Buttons: Primary, Secondary, Quiet, Success, Warning, Danger;
- Typography: Brand, Heading, Subtitle, SectionTitle, CardTitle, Hint, Metric;
- Badges: Neutral, Info, Success, Warning, Danger;
- Surfaces: Card, NoticeInfo, NoticeSuccess, NoticeWarning, NoticeDanger, EmptyState;
- Data: CopyTable, list/tree a technical viewer;
- Dialogs: FitDialog, DetailDialog, ProgressDialog, TaskProgressDialog, FilePicker.

Nová obrazovka má použít tyto komponenty místo lokálního hardcoded QSS. Lokální styl je přípustný pouze pro skutečně unikátní vizualizaci, nikoli pro další variantu tlačítka/karty/formuláře.

## UX pravidla

- jedna jasná primární akce v pracovním kontextu;
- pokročilé parametry jsou dostupné, ale nezahlcují základní tok;
- každá čekající operace má skutečný stav a možnost pochopit, co se děje;
- destructive action je opticky i textově odlišena;
- tabulky podporují výběr, copy a horizontální scroll;
- technický detail je jeden klik od lidského pohledu;
- dlouhé texty/JSON se necpou do message boxu;
- operace se sítí a diskem neblokují GUI thread;
- dostupnost modelu/akce se nefalšuje fallbackem;
- stav API se nezaměňuje s lokálním dokončením;
- legacy evidence se nezobrazuje jako nová úplná evidence.

## Kontrola regresí

`tests/test_ui_forensic_contract.py` ověřuje minimálně:

- existenci sémantických design tokenů a komponent;
- zahrnutí všech desktopových modulů do AST auditu;
- explicitní `self.*` signal/button vazby směřují na reálné metody (s výjimkou standardních zděděných Qt akcí);
- aktivní UI neobsahuje staré tvrzení o placené preflight zkoušce ani staré tvrzení o povinné redakci;
- History Run Explorer a Photo Studio jsou skutečně instalovány v hlavním entrypointu.

Tento kontrakt doplňují existující workflow, desktopové a integrační testy konkrétních obrazovek.
