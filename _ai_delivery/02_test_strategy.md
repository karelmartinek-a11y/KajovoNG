# 02_test_strategy

## Seznam existujících testů

1. `tests/test_kdgs_gate.py`
   - validace brand assetů,
   - existence KDGS dokumentů,
   - palette metadata,
   - text integrity / zákaz známých mojibake tokenů,
   - zákaz `QMessageBox` patternu v textových souborech.

2. `tests/test_ui_contract.py`
   - design tokeny,
   - breakpointy a state contract,
   - registrace hlavních page hostů,
   - dialog suite,
   - theme contract,
   - reduced motion,
   - mojibake guard v hlavních UI souborech.

3. `tests/test_kdgs_widgets.py`
   - `StateHost` state coverage,
   - preview widget minimální rozměry.

4. `tests/test_main_window_runtime.py`
   - offscreen build MainWindow,
   - breakpoint/runtime geometry,
   - toolbar width guards,
   - dialog runtime smoke.

5. `tests/test_smoke.py`
   - default settings smoke.

## Co bylo skutečně spuštěno v tomto auditu

- `python -m pytest -q tests/test_kdgs_gate.py tests/test_smoke.py` → PASS
- `python -m pytest -q tests/test_ui_contract.py` → PASS
- `python -m pytest -q` → FAIL na collectu kvůli chybějícímu `PySide6`

## Mezery v pokrytí

### Kritické mezery

1. **OpenAI client**
   - žádný test pro `list_models()` a `extract_document()`
   - žádný test pro validaci chybových stavů, embedded JSON, code fences, HTTP 4xx/5xx

2. **DocumentLoader / OCR**
   - žádný test pro text soubor, validní PDF s text layer, scan PDF bez text layer, obrázek, chybějící tesseract, timeout OCR, invalid PDF fallback

3. **Processing pipeline**
   - žádný test pro `process_import_directory()`, `process_pending()`, `process_file()`
   - žádný test pro dokument segmentation, offline candidate flow, OpenAI direct flow, manual completion flow, incomplete flow, stop/cancel flow

4. **Persistence / promotion**
   - žádný test pro `replace_document_pages`, `replace_page_text_blocks`, `update_processing_*`, `replace_processing_items`, `finalize_result`, `record_promotion_audit`, idempotent promotion a collision guards

5. **Logging**
   - žádný test JSON schema pro app log,
   - žádný test `decisions.jsonl`,
   - žádný test `operational_log`,
   - žádný test korelace přes `correlation_id`

6. **Progress UI**
   - žádný test, že progress bar ukazuje správný rozsah a význam,
   - žádný test pro cancel semantics,
   - žádný test pro export progress,
   - žádný test pro ETA/procenta (protože zatím neexistují)

7. **Encoding / text quality**
   - gate hlídá známé mojibake patterny, ale nehlídá konzistentní diakritiku/terminologii a encoding v širším repu.

## Návrh kompletní testovací matice

### A. Unit testy

1. `tests/test_openai_client.py`
   - official endpoint constants
   - request body schema pro responses API
   - image input append logic
   - JSON parse success / embedded JSON recovery / invalid JSON failure
   - amount parsing / VAT parsing / items coercion
   - error extraction z HTTP odpovědí

2. `tests/test_document_loader.py`
   - `.txt` → page split přes form feed
   - validní PDF text layer
   - PDF bez `%PDF-` header → text secondary
   - PDF scan + OCR branch
   - image OCR branch
   - `tesseract` FileNotFound / timeout / non-zero return
   - `build_openai_image_inputs()` pro image a PDF

3. `tests/test_processing_service_offline.py`
   - candidate extraction
   - incomplete reason
   - manual completion flow
   - ARES bypass flow
   - stop event flow

4. `tests/test_processing_service_openai.py`
   - openai direct allow/deny rules
   - OpenAI success → update processing document/supplier/items
   - OpenAI invalid output → unrecognized
   - missing API key → unrecognized
   - ARES fail → quarantine

5. `tests/test_repository_persistence.py`
   - file import
   - page/text block replace
   - processing doc/supplier/item updates
   - attempt lifecycle
   - operational log write

6. `tests/test_repository_promotion.py`
   - final success path
   - idempotent promotion
   - promotion collision
   - blocked promotion without items / missing fields / supplier invalid
   - dual-db separation proof

7. `tests/test_logging_schema.py`
   - `JsonFormatter`
   - project `log_event()` payload shape
   - required keys: timestamp, level, event_type, correlation_id, file_id, document_id, attempt_id

8. `tests/test_progress_dialog.py`
   - dialog update semantics
   - indeterminate vs determinate state
   - cancel disables button and updates text

### B. Integration testy

1. **Fixture: text invoice**
   - import → offline extraction → finalize → DB assertions

2. **Fixture: image invoice**
   - OCR → candidate extraction → finalize/unrecognized by expected data

3. **Fixture: scanned PDF**
   - raster OCR → page_text_blocks persisted → downstream pipeline

4. **Fixture: OpenAI mocked transport at HTTP boundary**
   - ne mock processing service, ale mock `requests.post/get` na boundary klienta
   - ověř request endpoint, headers, payload, parsed response a DB side effects

5. **Fixture: production promotion**
   - after finalize verify `final_documents`, `final_items`, `promotion_audit`

### C. End-to-end scénáře

1. `soubor.pdf -> import -> OCR -> offline complete -> ARES ok -> final -> promotion -> reporting`
2. `soubor.pdf -> import -> OCR incomplete -> OpenAI -> ARES ok -> final`
3. `soubor.png -> OCR fail -> unrecognized`
4. `OpenAI returns malformed JSON -> invalid -> unrecognized`
5. `stop processing during batch -> graceful stop + logs + progress end state`
6. `duplicate file -> duplicate/quarantine rule`

## Priorita doplnění testů

1. P0: `openai_client`, `document_loader`, `repository promotion`, `processing_service` core
2. P1: logging schema, progress dialog, export progress
3. P2: širší UI runtime coverage a encoding regression suite
