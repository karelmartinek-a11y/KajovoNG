# 03_remediation_plan

## Pořadí nápravy

### 1. Zafixovat důkazní test harness pro core pipeline
- **Cíl:** získat automaticky spustitelný důkaz pro OpenAI/OCR/DB tok.
- **Dotčené soubory:** `tests/`, případně `tests/fixtures/`, `pyproject.toml`
- **Rizika:** test fixture design, nutnost oddělit boundary mock od business logiky.
- **Hotovo když:** existují unit + integration testy pro OpenAI, OCR, persistence a promotion.

### 2. Doplnit forenzní schema logování přes celý pipeline
- **Cíl:** z logu rekonstruovat případ end-to-end.
- **Dotčené soubory:** `kajovospend/processing/service.py`, `kajovospend/persistence/repository.py`, `kajovospend/diagnostics/logging_setup.py`
- **Rizika:** příliš verbózní logy nebo únik citlivých dat.
- **Hotovo když:** každý attempt a finalize krok loguje correlation/document/file/attempt ids, timing, branch, outcome code a bezpečný provenance payload.

### 3. Rozšířit progress model z file-level na phase-level
- **Cíl:** skutečný progress dialog s významem stavu, procenty a ETA.
- **Dotčené soubory:** `kajovospend/application/controller.py`, `kajovospend/processing/service.py`, `kajovospend/ui/dialogs/forms.py`, `kajovospend/ui/main_window.py`
- **Rizika:** špatně odhadované ETA, příliš časté UI update.
- **Hotovo když:** progress umí fázi, current/total kroků, procento, elapsed, estimated remaining a detail message.

### 4. Odstranit synchronní síťové UI akce
- **Cíl:** eliminovat zbylé freeze body.
- **Dotčené soubory:** `kajovospend/ui/main_window.py`, `kajovospend/application/controller.py`
- **Rizika:** duplicita background wrappers.
- **Hotovo když:** načítání OpenAI modelů a podobné síťové akce běží přes background worker.

### 5. Zpevnit OpenAI auditovatelnost
- **Cíl:** prokazatelné a bezpečně logované AI volání.
- **Dotčené soubory:** `kajovospend/integrations/openai_client.py`, `kajovospend/processing/service.py`
- **Rizika:** logování příliš mnoha dat.
- **Hotovo když:** loguje se endpoint, model, timeout, selected pages, payload fingerprint, output validation result a request duration bez úniku tajných dat.

### 6. Zpevnit OCR auditovatelnost a tuning
- **Cíl:** vědět, jak a proč OCR proběhlo.
- **Dotčené soubory:** `kajovospend/ocr/document_loader.py`, `scripts/ocr_benchmark.py`, `scripts/ocr_evaluate.py`
- **Rizika:** nárůst complexity.
- **Hotovo když:** OCR runtime loguje engine, page count, raster branch, timeout/failure/success, duration, plus existuje benchmark report použitý jako source of truth pro konfiguraci.

### 7. Sjednotit textovou kvalitu a encoding
- **Cíl:** odstranit transliteraci a nekonzistenci textů.
- **Dotčené soubory:** `README.md`, `docs/*`, `startspend.bat`, runtime stringy v `kajovospend/*`
- **Rizika:** rozbití textových testů, nutná aktualizace gate testů.
- **Hotovo když:** repozitář používá konzistentní UTF-8 a sjednocenou češtinu tam, kde je UI/provozní text určen uživateli.

### 8. Aktualizovat dokumentaci podle skutečného pipeline
- **Cíl:** dokumentace musí odpovídat kódu.
- **Dotčené soubory:** `README.md`, `docs/*`
- **Rizika:** dokumentace zastará bez test/doc gate.
- **Hotovo když:** README a docs pokrývají AI flow, OCR flow, logging, progress, troubleshooting a test commands.
