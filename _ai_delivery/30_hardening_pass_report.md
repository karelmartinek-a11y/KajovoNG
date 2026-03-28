# 30 Hardening pass report

## Shrnutí průchodu

Byl proveden ještě jeden konzervativní hardening průchod zaměřený na zbytky synchronního blokování, logovací nesoulad, thread lifecycle a poslední UX/detail problémy. Neproběhl zbytečný redesign. Opraveny byly pouze konkrétní, důkazně nalezené vady.

## Nalezené a opravené problémy

### 1. Zbytek synchronního blokování v UI: načítání OpenAI modelů

**Nález**
- `MainWindow.on_load_openai_models()` stále volal `controller.fetch_openai_models()` synchronně z UI akce.
- Riziko: při síťové latenci mohl UI thread zamrznout.

**Oprava**
- `kajovospend/ui/main_window.py`
  - `on_load_openai_models()` nyní používá `_run_background_task(...)`.
  - přidán `result_handler` a `_apply_openai_models()`.

**Důkaz**
- nový test `tests/test_main_window_runtime.py::test_openai_model_loading_runs_in_background_task`.

### 2. Neshoda mezi dokumentací a skutečným projektovým logem

**Nález**
- dokumentace a app logger tvrdily `schema_version = 2`, ale `logs/decisions.jsonl` z `ProjectRepository.log_event()` dosud nezapisoval `schema_version`, `event_name` ani `project_path`.
- To snižovalo forenzní hodnotu logu a zároveň šlo o dokumentační rozpor.

**Oprava**
- `kajovospend/persistence/repository.py`
  - `log_event()` nyní zapisuje `schema_version`, `event_name`, `project_path`.
  - vybraná forenzní pole (`endpoint`, `model`, `request_fingerprint`, `duration_ms`, `status_code`, `phase`, `result_code`, `source_sha256`) se z payloadu zvedají i na top-level.

**Důkaz**
- nový test `tests/test_logging_schema.py::test_project_decisions_log_uses_schema_v2_and_top_level_forensic_fields`.

### 3. Thread lifecycle hygiene

**Nález**
- `_run_background_task()` přidával `QThread` do `_threads`, ale po doběhu ho neodstraňoval.
- Riziko: zbytečné držení referencí a postupná akumulace.

**Oprava**
- `kajovospend/ui/main_window.py`
  - dokončený thread se po doběhu odebírá z `_threads`.

## Kontrolované oblasti bez další změny

### Progress / ETA
- ETA zůstává heuristická a přiznaná jako heuristika.
- Nebyl nalezen fake timer.
- Změnu výpočtu ETA jsem v této fázi nedělal, protože současné chování je konzistentní s dokumentací a nešlo o důkazný bug.

### Encoding
- Nebyly nalezeny invalid UTF-8 soubory ani známé mojibake tokeny v auditovaných textových souborech.
- Zůstává pouze stylistická nekonzistence: část runtime češtiny je bez diakritiky.

### OpenAI validace
- Klient validuje JSON, datum, IČO, číslo dokladu a částku.
- Processing vrstva navíc validuje položky, ARES a promotion guardy.
- Zůstává ale nízké residual risk, že obsahově slabý, ale formálně validní AI výstup projde do další validace; v této fázi to nebylo měněno, aby nevznikl unáhlený behavior change bez širšího fixture corpus.

## Ověření po hardening zásazích

- `python -m pytest -q -k 'not test_openai_model_loading_runs_in_background_task'` → `37 passed, 1 deselected`
- `python -m pytest tests/test_main_window_runtime.py::test_openai_model_loading_runs_in_background_task -q` → `1 passed`
- `python -m pytest tests/test_logging_schema.py::test_project_decisions_log_uses_schema_v2_and_top_level_forensic_fields -q` → `1 passed`
- `pytest --collect-only -q` → `38 tests collected`

## Závěr

Hardening průchod našel ještě tři reálné vady a všechny byly opraveny bez zbytečného rozšiřování scope. Po opravách je projekt robustnější v oblasti UI responsiveness, forenzního logování a lifecycle managementu background tasků.
