# 10 Change report

## Změněné soubory

- `kajovospend/integrations/openai_client.py`
  - doplněn audit oficiálních endpointů, request fingerprint, validace JSON a obsahových polí, bezpečná metadata o volání.
- `kajovospend/ocr/document_loader.py`
  - doplněna provenance souboru, SHA-256, velikost souboru, OCR confidence a robustnější OCR pipeline.
- `kajovospend/diagnostics/logging_setup.py`
  - rozšířené JSON schema logů pro forenzní rekonstrukci.
- `kajovospend/application/controller.py`
  - runtime progress nyní drží procenta, detail, krok a ETA.
- `kajovospend/ui/dialogs/forms.py`
  - `TaskProgressDialog` zobrazuje název operace, krok, detail, procenta a ETA.
- `kajovospend/processing/service.py`
  - logování dokončeného a selhaného OpenAI volání, serializace progress payloadu.
- `README.md`
  - aktualizovaná architektura, OpenAI flow, OCR flow, persistence, logging a progress reporting.
- `docs/processing-flow.md`, `docs/logging-schema.md`, `docs/progress-reporting.md`
  - nová provozní dokumentace.
- `tests/conftest.py`
  - stabilizace import cesty a Qt offscreen režimu.
- `tests/test_openai_client_truth.py`
  - verifikace oficiálního OpenAI endpointu a audit metadata.
- `tests/test_document_loader_truth.py`
  - reálné čtení souboru a OCR nad skutečným obrázkem.
- `tests/test_logging_schema.py`
  - test logovacího schématu.
- `tests/test_progress_dialog.py`
  - test progress dialogu.
- `tests/test_runtime_workflow_smoke.py`
  - smoke test toku od reálného souboru po working a production DB.
- `tests/test_encoding_regressions.py`
  - regresní test UTF-8 a diakritiky.

## Proč byly změny nutné

Audit ukázal chybějící důkazní telemetry kolem OpenAI a OCR, slabý progress dialog bez ETA a detailu, neúplné testy kritických cest a zastaralou dokumentaci bez přesného popisu toků.

## Očekávaný provozní dopad

- lepší auditovatelnost,
- lepší diagnostika při selhání OpenAI/OCR,
- jasnější UX při dlouhých operacích,
- menší riziko regresí v oblasti encodingu, persistence a logů.

## Známá omezení

- ETA je heuristická, ne deterministická,
- integrační test OpenAI zůstává bez živé sítě, ale důkazně kontroluje oficiální endpoint, payload a audit metadata,
- OCR test závisí na dostupném Tesseractu v prostředí.
