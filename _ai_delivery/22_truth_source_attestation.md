# 22 Truth source attestation

Tímto formálně potvrzuji, že finální ZIP obsahuje důkazy pro následující tvrzení.

## 1. OpenAI integrace není simulovaná v produkčním kódu

Důkazy:
- `kajovospend/integrations/openai_client.py` používá oficiální `https://api.openai.com/v1/models` a `https://api.openai.com/v1/responses`.
- `tests/test_openai_client_truth.py` ověřuje skutečně sestavený request na oficiální URL, model a audit metadata.
- `OpenAIClient` generuje `request_fingerprint`, `duration_ms`, `status_code`, `validation_status`.

Otevřeně přiznaný limit:
- v této verifikaci nebyl puštěn živý request proti produkční OpenAI infrastruktuře.

## 2. Čtení souborů je reálné a jde z disku

Důkazy:
- `DocumentLoader.load()` vrací `source_path`, `source_sha256`, `source_bytes`.
- `tests/test_document_loader_truth.py` zapisuje reálný dočasný soubor a ověřuje jeho obsah i provenance.

## 3. OCR je reálné

Důkazy:
- `DocumentLoader` spouští Tesseract nad skutečným souborem.
- Verifikační běh vytvořil reálný PNG soubor a OCR nad ním vrátil text a confidence.
- `tests/test_document_loader_truth.py` to pokrývá automaticky.

## 4. DB persistence je reálná

Důkazy:
- `tests/test_runtime_workflow_smoke.py` potvrzuje working i production DB,
- smoke běh končí dokumentem ve stavu `final`,
- `ProjectRepository.list_final_documents()` vrací promovaný dokument s business daty.

## 5. Progress UI je navázané na reálná data

Důkazy:
- `TaskProgressDialog` přijímá progress payload a z něj vykresluje krok, detail, procenta a ETA.
- ETA vzniká z elapsed času / počtu kroků, ne z pevného časovače.
- `tests/test_progress_dialog.py` to kontroluje.
