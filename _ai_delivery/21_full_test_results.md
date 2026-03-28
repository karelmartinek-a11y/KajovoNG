# 21 Full test results

## Test suite

Příkaz:

```bash
QT_QPA_PLATFORM=offscreen pytest -q
```

Výsledek:

```text
36 passed in 29.19s
```

## Důkazní testy a jejich význam

- `tests/test_openai_client_truth.py`
  - oficiální OpenAI endpoint,
  - audit metadata,
  - odmítnutí nevalidního JSON.

- `tests/test_document_loader_truth.py`
  - provenance skutečného souboru,
  - reálné OCR nad obrázkem uloženým na disk.

- `tests/test_runtime_workflow_smoke.py`
  - import skutečného souboru,
  - working DB,
  - promotion do production DB,
  - finální detail dokumentu.

- `tests/test_logging_schema.py`
  - JSON schema verze 2,
  - forenzní pole.

- `tests/test_progress_dialog.py`
  - percenta,
  - ETA,
  - detail kroku.

- `tests/test_encoding_regressions.py`
  - UTF-8 README,
  - bez mojibake tokenů.

## Coverage běh

Příkaz:

```bash
QT_QPA_PLATFORM=offscreen pytest --cov=kajovospend --cov-report=term-missing -q
```

Shrnutí:
- testy zůstaly zelené,
- coverage přibližně `48 %`,
- kritické verifikační cesty jsou pokryté lépe než zbytek repa,
- coverage běh původně odhalil `ResourceWarning` kvůli neuzavíraným SQLite spojům; v této fázi opraveno.

## Regresní scénáře prověřené mimo test runner

1. Reálný OCR obrázek → Tesseract text + confidence + SHA-256 provenance.
2. Reálný textový dokument → import → offline extrakce → ARES validace → promotion → `document.finalized`.
3. OpenAI boundary verifikace → oficiální `v1/responses` URL + fingerprint + validation status.
