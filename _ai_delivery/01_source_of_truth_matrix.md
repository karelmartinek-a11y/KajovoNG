# 01_source_of_truth_matrix

| Oblast | Tvrzení | Status | Důkaz | Poznámka |
|---|---|---|---|---|
| OpenAI | Používá oficiální OpenAI endpointy | VERIFIED | `kajovospend/integrations/openai_client.py:30-31`, `38-42`, `88-93` | Kód volá `api.openai.com/v1/models` a `/v1/responses`. |
| OpenAI | Posílá reálné HTTP requesty | VERIFIED | `kajovospend/integrations/openai_client.py:38-42`, `88-93` | Použit `requests.get/post`, nikoli simulace. |
| OpenAI | Produkční běh byl v auditu skutečně ověřen proti OpenAI | UNKNOWN | — | Chyběl auditní API key a kontrolovaný fixture běh. |
| OpenAI | Existuje forenzní telemetry pro endpoint/model/timing/response metadata | FAILED | `kajovospend/persistence/repository.py:3227-3233`, `kajovospend/processing/service.py:481-563` | Logují se generické eventy, ale ne endpoint/model/latence/response id. |
| Čtení souborů | Text/PDF/obrázky se čtou ze skutečných souborů | VERIFIED | `kajovospend/ocr/document_loader.py:91-102`, `116-131`, `195-203` | `read_text`, `read_bytes`, Pillow, PdfReader. |
| OCR | PDF bez textové vrstvy jdou přes raster + OCR | VERIFIED | `kajovospend/ocr/document_loader.py:153-175`, `205-241` | Rasterizace přes `pypdfium2`, OCR přes Tesseract. |
| OCR | OCR je v auditu prokázané na reálném fixture běhu | UNKNOWN | — | Nebyl dodán korpus a OCR se nespouštělo nad vzorkem. |
| OCR | OCR parametry jsou důkazně optimalizované | PARTIAL | `kajovospend/ocr/document_loader.py:288-301`, `scripts/ocr_benchmark.py`, `scripts/ocr_evaluate.py` | Existují benchmark/eval skripty, ale runtime používá fixní konfiguraci bez doložených výsledků. |
| Databáze | OCR/load výsledky se zapisují do working DB | VERIFIED | `kajovospend/processing/service.py:244-245`, `751-784`; `kajovospend/persistence/repository.py:658-699`, `823-904` | Strany, text blocks, dokument, supplier, items. |
| Databáze | Promotion zapisuje do production DB | VERIFIED | `kajovospend/persistence/repository.py:3112-3218` | `attach database prod_db` a zápisy do `final_documents`/`final_items`. |
| Databáze | Oddělení working/prod DB není simulované | VERIFIED | `kajovospend/persistence/project_context.py:34-45`, `kajovospend/persistence/repository.py:2026-2041` | Guard na fyzickou odlišnost + separation report. |
| Logging | Existuje strukturované JSON logování | PARTIAL | `kajovospend/diagnostics/logging_setup.py:10-40`; `kajovospend/persistence/repository.py:3227-3233` | JSON logging existuje, ale je rozdělené a chybí detailní schema pipeline. |
| Logging | Z logu lze rekonstruovat celý případ od vstupu po DB výsledek | PARTIAL | `kajovospend/persistence/repository.py:3222-3233`, `2053-2160` | Část stopy existuje, ale chybí per-step timing, provenance a AI/OCR metadata. |
| Progress reporting | Import a pending běží mimo UI thread | VERIFIED | `kajovospend/ui/main_window.py:80-102`, `2273-2292`, `2096-2103` | Použit `QThread` + `BackgroundWorker`. |
| Progress reporting | Progress dialog poskytuje detailní význam, ETA a procenta | FAILED | `kajovospend/ui/dialogs/forms.py:771-799`, `kajovospend/application/controller.py:565-580` | Jen bar, message a storno. Bez ETA a detailní semantiky. |
| Testy | Existují automatické gate/UI smoke testy | VERIFIED | `tests/test_kdgs_gate.py`, `tests/test_ui_contract.py`, `tests/test_kdgs_widgets.py`, `tests/test_main_window_runtime.py`, `tests/test_smoke.py` | Ano, ale mimo core processing. |
| Testy | Kritické processing toky jsou pokryté | FAILED | `tests/` inventář | Chybí OpenAI/OCR/persistence/promotion/logging integrační testy. |
